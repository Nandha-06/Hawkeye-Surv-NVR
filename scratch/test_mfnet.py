import onnxruntime

model_path = r"C:\Users\rtnan\desktop\hawkeye\skills\detection\perception-core\models\mobilefacenet.onnx"
session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])

print("Inputs:")
for input in session.get_inputs():
    print(f"  Name: {input.name}, Shape: {input.shape}, Type: {input.type}")

print("\nOutputs:")
for output in session.get_outputs():
    print(f"  Name: {output.name}, Shape: {output.shape}, Type: {output.type}")
