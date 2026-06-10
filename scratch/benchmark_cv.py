#!/usr/bin/env python3
import os
import sys
import json
import time
import subprocess
import queue
import threading
import shutil
import cv2
import numpy as np

def main():
    # 1. Paths Setup
    workspace_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    # Change working directory to workspace_dir so all relative paths resolve correctly
    os.chdir(workspace_dir)

    video_path = "test.webm"
    temp_dir = os.path.join(".temp", "benchmark_frames")
    os.makedirs(temp_dir, exist_ok=True)

    print(f"Extracting 100 frames from {video_path}...")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Cannot open video file {video_path}", file=sys.stderr)
        sys.exit(1)

    frames = []
    while len(frames) < 100:
        ret, frame = cap.read()
        if not ret:
            # Loop video from the beginning
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to read any frames from video even after resetting", file=sys.stderr)
                sys.exit(1)
        frames.append(frame)
    cap.release()

    # Save frames as JPEGs
    frame_paths = []
    for idx, frame in enumerate(frames[:100]):
        rel_path = os.path.join(".temp", "benchmark_frames", f"frame_{idx:03d}.jpg")
        # Ensure we write using standard slashes
        rel_path_normalized = rel_path.replace("\\", "/")
        cv2.imwrite(rel_path_normalized, frame)
        frame_paths.append(rel_path_normalized)

    print(f"Successfully extracted 100 frames to {temp_dir}")

    # 2. Spawn detect.py
    python_exe = os.path.join("skills", "detection", "perception-core", ".venv", "Scripts", "python.exe")
    detect_py = os.path.join("skills", "detection", "perception-core", "scripts", "detect.py")

    cmd = [python_exe, detect_py, "--enable-face-recognition"]
    print(f"Spawning CV pipeline: {' '.join(cmd)}")

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=workspace_dir
    )

    # 3. Queue and Background Reader Threads
    event_queue = queue.Queue()

    def read_stdout(proc):
        try:
            for line in proc.stdout:
                line_str = line.decode('utf-8', errors='ignore').strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    event_queue.put(data)
                except json.JSONDecodeError:
                    # Log any non-JSON stdout lines
                    print(f"[Pipeline Stdout] {line_str}")
        except Exception as e:
            print(f"Error reading stdout: {e}", file=sys.stderr)

    def read_stderr(proc):
        try:
            for line in proc.stderr:
                line_str = line.decode('utf-8', errors='ignore').strip()
                if line_str:
                    # Forward pipeline logs to stderr
                    print(f"[Pipeline Log] {line_str}", file=sys.stderr)
        except Exception as e:
            print(f"Error reading stderr: {e}", file=sys.stderr)

    stdout_thread = threading.Thread(target=read_stdout, args=(process,), daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, args=(process,), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    # 4. Wait for ready event
    print("Waiting for pipeline to emit 'ready' event...")
    ready = False
    while not ready:
        try:
            msg = event_queue.get(timeout=30)
            if msg.get("event") == "ready":
                ready = True
                print("Pipeline ready received!")
            else:
                print(f"Pipeline init event: {msg}")
        except queue.Empty:
            print("Error: Timed out waiting for pipeline ready event.", file=sys.stderr)
            process.terminate()
            sys.exit(1)

    # 5. Benchmarking Loop
    print("Starting benchmark loop...")
    latencies = []
    loop_start = time.perf_counter()

    for idx, f_path in enumerate(frame_paths):
        t_sent = time.perf_counter()
        
        # Construct event message
        msg = {
            "event": "frame",
            "frame_id": idx,
            "frame_path": f_path,
            "camera_id": "benchmark_cam",
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())
        }
        
        # Write to pipeline stdin
        msg_str = json.dumps(msg) + "\n"
        try:
            process.stdin.write(msg_str.encode('utf-8'))
            process.stdin.flush()
        except Exception as e:
            print(f"Error writing to pipeline stdin at frame {idx}: {e}", file=sys.stderr)
            process.terminate()
            sys.exit(1)
            
        # Wait for detections event for this specific frame
        received = False
        while not received:
            try:
                # 10s timeout to allow face recognition and detection models to complete
                msg_out = event_queue.get(timeout=10)
                if msg_out.get("event") == "detections" and msg_out.get("frame_id") == idx:
                    t_recv = time.perf_counter()
                    latency = (t_recv - t_sent) * 1000.0  # in ms
                    latencies.append(latency)
                    received = True
                # We can ignore other intermediate events (like threat_analysis or perf_stats)
            except queue.Empty:
                print(f"Error: Timed out waiting for detections event for frame {idx}", file=sys.stderr)
                process.terminate()
                sys.exit(1)

    loop_end = time.perf_counter()
    total_time = loop_end - loop_start

    # 6. Stop pipeline gracefully
    print("Stopping pipeline...")
    try:
        stop_msg = {"command": "stop"}
        process.stdin.write((json.dumps(stop_msg) + "\n").encode('utf-8'))
        process.stdin.flush()
    except Exception:
        pass

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()

    # 7. Calculate and Report Metrics
    total_frames = len(latencies)
    throughput_fps = total_frames / total_time if total_time > 0 else 0.0
    avg_latency = sum(latencies) / total_frames if total_frames > 0 else 0.0
    p50 = np.percentile(latencies, 50) if latencies else 0.0
    p95 = np.percentile(latencies, 95) if latencies else 0.0
    p99 = np.percentile(latencies, 99) if latencies else 0.0

    # Write report JSON
    report_data = {
        "total_frames": total_frames,
        "total_time_seconds": total_time,
        "throughput_fps": throughput_fps,
        "average_latency_ms": avg_latency,
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "p99_latency_ms": p99
    }

    report_path = os.path.join(".data", "reports", "benchmark_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2)

    # Print report to stdout
    print("================ BENCHMARK REPORT ================")
    print(f"Total frames processed: {total_frames}")
    print(f"Total time (seconds):   {total_time:.4f}")
    print(f"Throughput (FPS):       {throughput_fps:.2f}")
    print(f"Average latency (ms):   {avg_latency:.2f}")
    print(f"p50 latency (ms):       {p50:.2f}")
    print(f"p95 latency (ms):       {p95:.2f}")
    print(f"p99 latency (ms):       {p99:.2f}")
    print("==================================================")

    # 8. Clean up temporary files
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            print("Successfully cleaned up temporary frame directory.")
        except Exception as e:
            print(f"Warning: Failed to clean up {temp_dir}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
