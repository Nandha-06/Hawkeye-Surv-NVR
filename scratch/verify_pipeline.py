import cv2
import os
import sys
import shutil
import subprocess
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

def log(msg):
    print(f"[VERIFY] {msg}", flush=True)

def main():
    # Paths setup
    workspace_root = Path(__file__).resolve().parent.parent
    test_webm = workspace_root / "test.webm"
    temp_dir = workspace_root / ".temp" / "verify_pipeline"
    detector_python = workspace_root / "skills" / "detection" / "perception-core" / ".venv" / "Scripts" / "python.exe"
    detector_script = workspace_root / "skills" / "detection" / "perception-core" / "scripts" / "detect.py"

    log("Starting verification pipeline script...")
    log(f"Workspace root: {workspace_root}")
    log(f"Test webm path: {test_webm}")
    log(f"Temp directory: {temp_dir}")
    log(f"Detector python: {detector_python}")
    log(f"Detector script: {detector_script}")

    # Step 1: Clean/Create Temp directory
    if temp_dir.exists():
        log(f"Removing existing temp directory: {temp_dir}")
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Step 2: Open video and extract frames
    cap = cv2.VideoCapture(str(test_webm))
    if not cap.isOpened():
        log(f"ERROR: Could not open {test_webm}")
        sys.exit(1)

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    log(f"Video loaded: {video_width}x{video_height} @ {video_fps} FPS, {total_frames} total frames.")

    # We target 5 FPS. Since video is 25 FPS, we sample every 5th frame.
    target_fps = 5.0
    step = int(round(video_fps / target_fps))
    if step < 1:
        step = 1

    log(f"Extracting frames every {step} frame(s) to achieve {target_fps} FPS...")
    
    extracted_frames = []
    frame_idx = 0
    base_dt = datetime.now(timezone.utc)  # Base ISO timestamp start time

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % step == 0:
            frame_name = f"frame_{frame_idx:04d}.jpg"
            frame_path = temp_dir / frame_name
            
            # Save frame as JPEG
            cv2.imwrite(str(frame_path), frame)
            
            # Calculate ISO timestamp
            offset_seconds = frame_idx / video_fps
            frame_dt = base_dt + timedelta(seconds=offset_seconds)
            timestamp_str = frame_dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            
            extracted_frames.append({
                "frame_id": len(extracted_frames),
                "frame_path": str(frame_path),
                "timestamp": timestamp_str,
                "width": video_width,
                "height": video_height
            })
            log(f"Extracted and saved: {frame_name} -> {frame_path} at {timestamp_str}")

        frame_idx += 1

    cap.release()
    log(f"Total frames extracted: {len(extracted_frames)}")

    if not extracted_frames:
        log("ERROR: No frames were extracted!")
        sys.exit(1)

    # Step 3: Spawn detect.py subprocess
    cmd = [
        str(detector_python),
        str(detector_script),
        "--enable-face-recognition",
        "--enable-motion-gating",
        "--classes", "person,car,dog,cat"
    ]
    log(f"Spawning CV pipeline: {' '.join(cmd)}")
    
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,  # Let perception log directly to stderr for visibility
        text=True,
        cwd=str(workspace_root)
    )

    try:
        # Step 4: Wait for readiness and log progress
        log("Waiting for readiness/progress events from subprocess...")
        while True:
            line = proc.stdout.readline()
            if not line:
                log("ERROR: Subprocess closed stdout prematurely.")
                sys.exit(1)
            
            line_str = line.strip()
            print(f"[SUBPROCESS OUT] {line_str}", flush=True)
            
            try:
                event = json.loads(line_str)
                if event.get("event") == "ready":
                    log("Subprocess is READY. Model loaded.")
                    break
            except json.JSONDecodeError:
                pass

        # Step 5: Pipe frames in stdin protocol and read results
        for idx, frame_info in enumerate(extracted_frames):
            frame_event = {
                "event": "frame",
                "frame_id": frame_info["frame_id"],
                "camera_id": "test_camera",
                "timestamp": frame_info["timestamp"],
                "frame_path": frame_info["frame_path"],
                "width": frame_info["width"],
                "height": frame_info["height"]
            }
            
            event_json = json.dumps(frame_event)
            log(f"Sending frame event {frame_info['frame_id']} to stdin...")
            proc.stdin.write(event_json + "\n")
            proc.stdin.flush()

            # Read stdout until detections or error for this frame
            while True:
                line = proc.stdout.readline()
                if not line:
                    log("ERROR: Subprocess closed stdout during frame processing.")
                    sys.exit(1)
                
                line_str = line.strip()
                print(f"[SUBPROCESS OUT] {line_str}", flush=True)
                
                try:
                    resp = json.loads(line_str)
                    if resp.get("event") in ("detections", "error") and resp.get("frame_id") == frame_info["frame_id"]:
                        if resp.get("event") == "detections":
                            objects = resp.get("objects", [])
                            motion_gated = resp.get("motion_gated", False)
                            log(f"Result for frame {frame_info['frame_id']}: motion_gated={motion_gated}, objects={len(objects)}")
                            for obj in objects:
                                log(f"  - Detected Object -> Class: {obj.get('class')}, BBox: {obj.get('bbox')}, Confidence: {obj.get('confidence')}")
                        else:
                            log(f"Error on frame {frame_info['frame_id']}: {resp.get('message')}")
                        break
                except json.JSONDecodeError:
                    pass

        # Step 6: Gracefully stop the subprocess
        log("Sending stop command...")
        stop_cmd = {"command": "stop"}
        proc.stdin.write(json.dumps(stop_cmd) + "\n")
        proc.stdin.flush()
        
        try:
            proc.wait(timeout=10)
            log("Subprocess exited gracefully.")
        except subprocess.TimeoutExpired:
            log("Subprocess did not exit in time. Terminating...")
            proc.terminate()
            
    finally:
        # Step 7: Clean up temporary image files
        if temp_dir.exists():
            log(f"Cleaning up temporary directory: {temp_dir}")
            shutil.rmtree(temp_dir)
            log("Cleanup complete.")

if __name__ == "__main__":
    main()
