import onnxruntime
import numpy as np

model_path = r"C:\Users\rtnan\desktop\hawkeye\skills\detection\perception-core\models\scrfd_500m_bnkps.onnx"
session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])

SCRFD_INPUT_SIZE = (640, 640)
SCRFD_FEAT_STRIDE = [8, 16, 32]
SCRFD_NUM_ANCHORS = 2

def _generate_anchor_centers(height, width, stride):
    anchor_centers = np.stack(
        np.mgrid[:height, :width][::-1], axis=-1
    ).astype(np.float32)
    anchor_centers = (anchor_centers * stride).reshape(-1, 2)
    if SCRFD_NUM_ANCHORS > 1:
        anchor_centers = np.stack(
            [anchor_centers] * SCRFD_NUM_ANCHORS, axis=1
        ).reshape(-1, 2)
    return anchor_centers

# Run inference
blob = np.random.randn(1, 3, 640, 640).astype(np.float32)
input_name = session.get_inputs()[0].name
outputs = session.run(None, {input_name: blob})

print("Output shapes:")
for i, out in enumerate(session.get_outputs()):
    print(f"  {out.name}: shape {outputs[i].shape}")

print("\nChecking anchors:")
for idx, stride in enumerate(SCRFD_FEAT_STRIDE):
    h = SCRFD_INPUT_SIZE[1] // stride
    w = SCRFD_INPUT_SIZE[0] // stride
    anchors = _generate_anchor_centers(h, w, stride)
    scores = outputs[idx][0]
    bbox_preds = outputs[idx + 3][0]
    kps_preds = outputs[idx + 6][0]
    print(f"Stride {stride}:")
    print(f"  Anchors shape: {anchors.shape}")
    print(f"  Scores shape: {scores.shape}")
    print(f"  Bbox preds shape: {bbox_preds.shape}")
    print(f"  Kps preds shape: {kps_preds.shape}")
    assert anchors.shape[0] == scores.shape[0], f"Stride {stride} shape mismatch!"
print("\nAll checks passed successfully!")
