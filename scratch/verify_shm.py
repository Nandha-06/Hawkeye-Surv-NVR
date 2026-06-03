import sys
import json
import time
import threading
import numpy as np
from multiprocessing.shared_memory import SharedMemory

# Mock log function
def log(msg):
    print(f"[TEST LOG] {msg}", file=sys.stderr, flush=True)

# Re-declare class as implemented in detect.py
class SharedMemoryStreamReader:
    def __init__(self, camera_id):
        self.camera_id = camera_id
        self.frame = None
        self.ret = False
        self.lock = threading.Lock()
        self.stop_requested = False
        self.shm_cache = {}  # Map shm_name -> SharedMemory object
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue

                if msg.get("command") == "stop":
                    log("SharedMemoryStreamReader: Received stop command")
                    self.stop_requested = True
                    break

                if msg.get("event") == "new_frame":
                    shm_name = msg.get("shm_name")
                    w = msg.get("width", 1280)
                    h = msg.get("height", 720)
                    if not shm_name:
                        continue

                    shm = self.shm_cache.get(shm_name)
                    if shm is None:
                        try:
                            shm = SharedMemory(name=shm_name, create=False)
                            self.shm_cache[shm_name] = shm
                            log(f"SharedMemoryStreamReader: Attached to SHM segment: {shm_name}")
                        except Exception as e:
                            log(f"SharedMemoryStreamReader: Error attaching to SHM {shm_name}: {e}")
                            continue

                    try:
                        raw_arr = np.ndarray((h, w, 3), dtype=np.uint8, buffer=shm.buf)
                        with self.lock:
                            self.frame = raw_arr.copy()
                            self.ret = True
                    except Exception as e:
                        log(f"SharedMemoryStreamReader: Error reading from SHM buffer: {e}")
        except Exception as e:
            log(f"SharedMemoryStreamReader stdin thread error: {e}")
        finally:
            self.stop_requested = True
            self.stop()

    def read(self):
        with self.lock:
            return self.ret, self.frame

    def stop(self):
        self.stop_requested = True
        for shm_name, shm in list(self.shm_cache.items()):
            try:
                shm.close()
                log(f"SharedMemoryStreamReader: Closed SHM segment: {shm_name}")
            except Exception:
                pass
        self.shm_cache.clear()

def run_test():
    # 1. Create a dummy shared memory segment
    shm_name = "Local\\test_shm_verification"
    w, h = 1280, 720
    size = w * h * 3
    
    # Fill shared memory with a specific test pattern (e.g. constant 123)
    shm = SharedMemory(name=shm_name, create=True, size=size)
    shm_buf = np.ndarray((h, w, 3), dtype=np.uint8, buffer=shm.buf)
    shm_buf.fill(123)
    log(f"Created shared memory {shm_name} and populated with dummy data.")

    # Redirect sys.stdin before creating the reader!
    import io
    original_stdin = sys.stdin
    mock_stdin = io.StringIO(
        json.dumps({"event": "new_frame", "shm_name": shm_name, "width": w, "height": h}) + "\n" +
        json.dumps({"command": "stop"}) + "\n"
    )
    sys.stdin = mock_stdin

    # 2. Instantiate the reader
    reader = SharedMemoryStreamReader("test_camera")
    
    try:
        # Wait for the reader thread to process mock stdin
        time.sleep(1.0)
        
        # Read frame
        ret, frame = reader.read()
        if ret and frame is not None:
            log(f"Successfully read frame of shape: {frame.shape}, mean pixel: {np.mean(frame)}")
            assert frame.shape == (h, w, 3), "Invalid shape!"
            assert np.all(frame == 123), "Invalid content!"
            log("SUCCESS: Frame validation checks passed!")
        else:
            log("FAILURE: Could not read frame from shared memory!")
            sys.exit(1)
            
    finally:
        sys.stdin = original_stdin
        reader.stop()
        shm.close()
        shm.unlink()
        log("Test cleanup completed.")

if __name__ == "__main__":
    run_test()
