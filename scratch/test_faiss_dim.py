import faiss

index_path = r"C:\Users\rtnan\Desktop\DeepCamera\skills\detection\perception-core\data\face_index.bin"
try:
    index = faiss.read_index(index_path)
    print(f"Index loaded. Dimension (d): {index.d}, total vectors: {index.ntotal}")
except Exception as e:
    print("Error loading index:", e)
