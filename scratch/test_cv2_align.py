import cv2
import numpy as np

# Create fake landmarks (5, 2)
landmarks = np.array([
    [30.0, 50.0],
    [70.0, 50.0],
    [50.0, 70.0],
    [35.0, 90.0],
    [65.0, 90.0]
], dtype=np.float32)

ARCFACE_REF_POINTS = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041]
], dtype=np.float32)

res = cv2.estimateAffinePartial2D(landmarks, ARCFACE_REF_POINTS)
print("Result of estimateAffinePartial2D:", res)
M = res[0]
print("M:", M)
if M is not None:
    print("M shape:", M.shape)
