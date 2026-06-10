import os
import sys
from pathlib import Path
import cv2
import numpy as np

def log(msg: str):
    print(f"[VERIFY] {msg}", flush=True)

def main():
    # Setup paths
    workspace_root = Path(__file__).resolve().parent.parent
    test_webm_path = workspace_root / "test.webm"
    snapshot_dir = workspace_root / ".data" / "snapshots"
    snapshot_path = snapshot_dir / "test_snapshot.jpg"
    crops_dir = workspace_root / ".data" / "crops"
    aligned_crop_path = crops_dir / "test_aligned_crop.jpg"
    face_image_path = workspace_root / "skills" / "detection" / "perception-core" / "data" / "crops" / "stranger_1_face.jpg"

    log("--- Step 1: Snapshot Extraction & Saving ---")
    log(f"Reading test video from: {test_webm_path}")
    if not test_webm_path.exists():
        raise FileNotFoundError(f"Test video not found at: {test_webm_path}")

    cap = cv2.VideoCapture(str(test_webm_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video file: {test_webm_path}")
    
    success, frame = cap.read()
    cap.release()
    if not success or frame is None:
        raise RuntimeError("Failed to read first frame from video")
    
    log(f"Extracted original frame size: {frame.shape[1]}x{frame.shape[0]}")

    # Downscaling logic (max-width 960px)
    max_width = 960
    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / float(w)
        new_w = max_width
        new_h = max(1, int(round(h * scale)))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        log(f"Downscaled frame from {w}x{h} to {new_w}x{new_h}")
    else:
        resized = frame
        log(f"Frame width {w} is within max_width {max_width}, no downscaling needed")

    # JPEG encode with quality 70
    quality = 70
    ok, buf = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("Failed to encode JPEG snapshot")

    # Atomic write logic
    log(f"Saving snapshot atomically to: {snapshot_path}")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = snapshot_path.with_suffix(".jpg.tmp")
    with open(tmp_path, "wb") as f:
        f.write(buf.tobytes())
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp_path, snapshot_path)
    log("Snapshot saved successfully.")

    log("\n--- Step 2: Face Alignment ---")
    log(f"Loading face crop from: {face_image_path}")
    if not face_image_path.exists():
        raise FileNotFoundError(f"Face crop not found at: {face_image_path}")
    
    face_img = cv2.imread(str(face_image_path))
    if face_img is None:
        raise RuntimeError("Failed to load face image using OpenCV")
    log(f"Original face image size: {face_img.shape[1]}x{face_img.shape[0]}")

    # Import modules from perception-core pipeline
    perception_core_scripts = workspace_root / "skills" / "detection" / "perception-core" / "scripts"
    log(f"Adding to sys.path: {perception_core_scripts}")
    sys.path.insert(0, str(perception_core_scripts))

    from modules.face_recog import _align_face, _SCRFDDetector
    
    scrfd_path = workspace_root / "skills" / "detection" / "perception-core" / "models" / "scrfd_500m_bnkps.onnx"
    log(f"Initializing SCRFD detector using model: {scrfd_path}")
    detector = _SCRFDDetector(str(scrfd_path), providers=["CPUExecutionProvider"])

    # Prepare padded face image to ensure proper detection scale
    h_f, w_f = face_img.shape[:2]
    pad = 100
    padded = np.zeros((h_f + 2*pad, w_f + 2*pad, 3), dtype=np.uint8)
    padded[pad:pad+h_f, pad:pad+w_f] = face_img

    # Run detection on both unpadded and padded images to find the highest-confidence face landmarks
    dets_direct = detector.detect(face_img, threshold=0.1)
    dets_padded = detector.detect(padded, threshold=0.1)

    all_candidates = []
    for bbox, conf, kps in dets_direct:
        all_candidates.append((conf, kps, "direct"))
    for bbox, conf, kps in dets_padded:
        kps_shifted = kps - np.array([pad, pad], dtype=np.float32)
        all_candidates.append((conf, kps_shifted, "padded"))

    if not all_candidates:
        raise RuntimeError("SCRFD face detector did not find any faces in the image (with or without padding).")

    # Select the candidate with the highest detection confidence
    all_candidates.sort(key=lambda x: x[0], reverse=True)
    best_conf, best_kps, source = all_candidates[0]
    log(f"Selected best face candidate from '{source}' with confidence {best_conf:.4f} and landmarks:\n{best_kps}")

    aligned_face = _align_face(face_img, best_kps)
    if aligned_face is None:
        raise RuntimeError("Warping/aligning the face failed.")

    # Save the aligned 112x112 face crop
    log(f"Saving aligned face crop to: {aligned_crop_path}")
    crops_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(aligned_crop_path), aligned_face)
    log("Aligned face crop saved successfully.")

    log("\n--- Step 3: File Checks & Verification ---")
    log("Verifying files are non-empty and readable...")
    assert snapshot_path.exists(), f"Snapshot path {snapshot_path} does not exist"
    assert snapshot_path.stat().st_size > 0, "Snapshot file is empty"
    assert aligned_crop_path.exists(), f"Aligned crop path {aligned_crop_path} does not exist"
    assert aligned_crop_path.stat().st_size > 0, "Aligned crop file is empty"

    snap_img_read = cv2.imread(str(snapshot_path))
    assert snap_img_read is not None, "Failed to read snapshot file back with OpenCV"
    log(f"Successfully read back snapshot: shape {snap_img_read.shape}")

    crop_img_read = cv2.imread(str(aligned_crop_path))
    assert crop_img_read is not None, "Failed to read aligned crop file back with OpenCV"
    assert crop_img_read.shape == (112, 112, 3), f"Expected read crop shape (112, 112, 3), got {crop_img_read.shape}"
    log(f"Successfully read back aligned face crop: shape {crop_img_read.shape}")

    log("All checks and assertions passed successfully!")

if __name__ == "__main__":
    main()
