import subprocess
import time
import sys

print("Spawning detect.py with pipe...")
p = subprocess.Popen(
    [".venv/Scripts/python.exe", "scripts/detect.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

print("Waiting for 10 seconds...")
time.sleep(10)
if p.poll() is not None:
    print(f"Process exited early with code {p.returncode}")
    print("STDOUT:", p.stdout.read())
    print("STDERR:", p.stderr.read())
else:
    print("Process is still running (blocking on stdin). Killing it.")
    p.kill()
