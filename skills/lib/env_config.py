"""
env_config.py — Shared hardware environment detection and model optimization.

Provides a single entry point for any Hawkeye skill to:
  1. Detect available compute hardware (NVIDIA, AMD, Apple, Intel, CPU)
  2. Auto-export models to the optimal inference format
  3. Load cached optimized models with PyTorch fallback

Usage:
    from lib.env_config import HardwareEnv

    env = HardwareEnv.detect()
    model, fmt = env.load_optimized("yolo26n")
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _log(msg: str):
    """Log to stderr."""
    print(f"[env_config] {msg}", file=sys.stderr, flush=True)


# ─── Backend definitions ────────────────────────────────────────────────────

@dataclass
class BackendSpec:
    """Specification for a compute backend's optimized export."""
    name: str               # "cuda", "rocm", "mps", "intel", "cpu"
    export_format: str      # ultralytics export format string
    model_suffix: str       # file extension/dir to look for cached model
    half: bool = True       # use FP16
    extra_export_args: dict = field(default_factory=dict)
    compute_units: Optional[str] = None  # CoreML compute units: "cpu_and_ne", "all", etc.


BACKEND_SPECS = {
    "cuda": BackendSpec(
        name="cuda",
        export_format="pytorch",
        model_suffix=".pt",
        half=True,
    ),
    "rocm": BackendSpec(
        name="rocm",
        export_format="pytorch",     # PyTorch + HIP — ultralytics ONNX doesn't support ROCMExecutionProvider
        model_suffix=".pt",
        half=False,
    ),
    "mps": BackendSpec(
        name="mps",
        export_format="onnx",
        model_suffix=".onnx",
        half=False,  # ONNX Runtime handles precision internally
        # ONNX Runtime + CoreMLExecutionProvider bypasses the broken
        # MPSGraphExecutable MLIR pipeline on macOS 26.x while still
        # leveraging GPU/ANE via CoreML under the hood.
    ),
    "intel": BackendSpec(
        name="intel",
        export_format="openvino",
        model_suffix="_openvino_model",
        half=True,
    ),
    "cpu": BackendSpec(
        name="cpu",
        export_format="onnx",
        model_suffix=".onnx",
        half=False,
    ),
}

# ─── ONNX + CoreML EP wrapper ────────────────────────────────────────────────
# Provides an ultralytics-compatible model interface using onnxruntime directly
# with CoreMLExecutionProvider for ~6ms inference on Apple Silicon (vs 21ms when
# ultralytics defaults to CPUExecutionProvider).

class _BoxResult:
    """Minimal replacement for ultralytics Boxes result."""
    __slots__ = ('xyxy', 'conf', 'cls')

    def __init__(self, xyxy, conf, cls):
        self.xyxy = xyxy   # [[x1,y1,x2,y2]]
        self.conf = conf   # [conf]
        self.cls = cls     # [cls_id]


class _DetResult:
    """Minimal replacement for ultralytics Results."""
    __slots__ = ('boxes',)

    def __init__(self, boxes: list):
        self.boxes = boxes


class _OnnxCoreMLModel:
    """ONNX Runtime model with CoreML EP, compatible with ultralytics API.

    Supports: model(image_path_or_pil, conf=0.5, verbose=False)
    Returns:  list of _DetResult with .boxes iterable of _BoxResult
    """

    def __init__(self, session, class_names: dict):
        self.session = session
        self.names = class_names
        self._input_name = session.get_inputs()[0].name
        # Expected input shape: [1, 3, H, W]
        shape = session.get_inputs()[0].shape
        self._input_h = shape[2] if isinstance(shape[2], int) else 640
        self._input_w = shape[3] if isinstance(shape[3], int) else 640

    def __call__(self, source, conf: float = 0.25, verbose: bool = True, **kwargs):
        """Run inference on an image path or PIL Image.

        All models use onnx-community HuggingFace format:
          outputs[0] = logits  [1, 300, 80]  (raw, pre-sigmoid)
          outputs[1] = pred_boxes [1, 300, 4] (cx, cy, w, h normalized 0..1)
        """
        import numpy as np
        from PIL import Image

        # Load image
        if isinstance(source, str):
            img = Image.open(source).convert("RGB")
        elif isinstance(source, Image.Image):
            img = source.convert("RGB")
        else:
            # Assuming numpy array from OpenCV (BGR)
            import numpy as np
            if isinstance(source, np.ndarray) and len(source.shape) == 3 and source.shape[2] == 3:
                img = Image.fromarray(source[:, :, ::-1]).convert("RGB")
            else:
                img = Image.fromarray(source).convert("RGB")

        orig_w, orig_h = img.size

        # Letterbox resize to input size
        scale = min(self._input_w / orig_w, self._input_h / orig_h)
        new_w, new_h = int(orig_w * scale), int(orig_h * scale)
        img_resized = img.resize((new_w, new_h), Image.BILINEAR)

        # Pad to input size (center)
        pad_x = (self._input_w - new_w) // 2
        pad_y = (self._input_h - new_h) // 2
        canvas = np.full((self._input_h, self._input_w, 3), 114, dtype=np.uint8)
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = np.array(img_resized)

        # HWC→CHW, normalize, add batch dim
        blob = canvas.transpose(2, 0, 1).astype(np.float32) / 255.0
        blob = np.expand_dims(blob, 0)

        # Run inference
        outputs = self.session.run(None, {self._input_name: blob})
        
        # Handle ultralytics single-tensor output (shape: [1, 84, 8400])
        if len(outputs) == 1:
            # outputs[0] shape: [1, 84, 8400] for YOLOv8 (4 box + 80 class)
            tensor = outputs[0][0] # [84, 8400]
            # Transpose to [8400, 84] to separate boxes and logits
            tensor = tensor.transpose(1, 0)
            pred_boxes = tensor[:, :4] # cx, cy, w, h
            logits = tensor[:, 4:]     # 80 class logits
            # Normalize boxes to 0..1 based on input shape
            pred_boxes[:, 0] /= self._input_w
            pred_boxes[:, 1] /= self._input_h
            pred_boxes[:, 2] /= self._input_w
            pred_boxes[:, 3] /= self._input_h
        else:
            logits = outputs[0][0]      # [300, 80] raw class logits
            pred_boxes = outputs[1][0]  # [300, 4]  cx, cy, w, h (normalized 0..1)

        # Sigmoid → class probabilities
        probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -88.0, 88.0)))

        # Parse detections
        boxes = []
        for i in range(len(pred_boxes)):
            cls_id = int(np.argmax(probs[i]))
            det_conf = float(probs[i][cls_id])
            if det_conf < conf:
                continue

            # cx,cy,w,h (normalized) → x1,y1,x2,y2 (original image pixels)
            cx, cy, bw, bh = pred_boxes[i]
            px_cx = cx * self._input_w
            px_cy = cy * self._input_h
            px_w = bw * self._input_w
            px_h = bh * self._input_h

            x1 = max(0, min((px_cx - px_w / 2 - pad_x) / scale, orig_w))
            y1 = max(0, min((px_cy - px_h / 2 - pad_y) / scale, orig_h))
            x2 = max(0, min((px_cx + px_w / 2 - pad_x) / scale, orig_w))
            y2 = max(0, min((px_cy + px_h / 2 - pad_y) / scale, orig_h))

            boxes.append(_BoxResult(
                xyxy=np.array([[x1, y1, x2, y2]]),
                conf=np.array([det_conf]),
                cls=np.array([cls_id]),
            ))

        return [_DetResult(boxes)]


# ─── Hardware detection ──────────────────────────────────────────────────────

@dataclass
class HardwareEnv:
    """Detected hardware environment with model optimization capabilities."""

    backend: str = "cpu"              # "cuda" | "rocm" | "mps" | "intel" | "cpu"
    device: str = "cpu"               # torch device string
    export_format: str = "onnx"       # optimal export format
    compute_units: str = "all"        # CoreML compute units (Apple only)
    gpu_name: str = ""                # human-readable GPU name
    gpu_memory_mb: int = 0            # GPU memory in MB
    driver_version: str = ""          # GPU driver version
    framework_ok: bool = False        # True if optimized runtime is importable
    coral_detected: bool = False      # True if Google Coral Edge TPU is available
    detection_details: dict = field(default_factory=dict)  # raw detection info

    # Timing (populated by export/load)
    export_ms: float = 0.0
    load_ms: float = 0.0

    @staticmethod
    def detect(preferred_device: str = "auto") -> "HardwareEnv":
        """Probe the system and return a populated HardwareEnv."""
        env = HardwareEnv()

        # Run Coral detection first as it can run alongside CPU/GPU backends
        env.coral_detected = env._check_coral()

        preferred_device = preferred_device.lower()
        if preferred_device == "cpu":
            env._fallback_cpu()
        elif preferred_device == "cuda" and env._try_cuda():
            pass
        elif preferred_device == "rocm" and env._try_rocm():
            pass
        elif preferred_device == "mps" and env._try_mps():
            pass
        elif preferred_device == "intel" and env._try_intel():
            pass
        else:
            # "auto" or force failed/unsupported -> standard auto-detection
            if env._try_cuda():
                pass
            elif env._try_rocm():
                pass
            elif env._try_mps():
                pass
            elif env._try_intel():
                pass
            else:
                env._fallback_cpu()

        # Set export format and compute units from backend spec
        spec = BACKEND_SPECS.get(env.backend, BACKEND_SPECS["cpu"])
        env.export_format = spec.export_format
        if spec.compute_units:
            env.compute_units = spec.compute_units

        # Check if optimized runtime is available
        env.framework_ok = env._check_framework()

        _log(f"Detected: backend={env.backend}, device={env.device}, "
             f"gpu={env.gpu_name or 'none'}, "
             f"format={env.export_format}, "
             f"framework_ok={env.framework_ok}, "
             f"coral_detected={env.coral_detected}")

        return env

    def _check_coral(self) -> bool:
        """Detect Google Coral Edge TPU (USB or PCIe) on Windows, Linux, and macOS."""
        system = platform.system()
        
        # 1. Windows: Query present PnP devices via PowerShell
        if system == "Windows":
            try:
                import json
                gpus = self._detect_windows_gpus_powershell()  # warm up GPU detection as well
                
                args = [
                    "powershell", 
                    "-NoProfile", 
                    "-Command", 
                    "Get-PnpDevice -PresentOnly | Select-Object InstanceId, FriendlyName | ConvertTo-Json -Compress"
                ]
                res = subprocess.run(args, capture_output=True, text=True, timeout=10)
                if res.returncode == 0 and res.stdout.strip():
                    try:
                        devices = json.loads(res.stdout.strip())
                        if isinstance(devices, dict):
                            devices = [devices]
                        for dev in devices:
                            inst_id = str(dev.get("InstanceId", "")).upper()
                            friendly_name = str(dev.get("FriendlyName", "")).upper()
                            if (any(k in inst_id for k in ["VID_1A6E", "VID_18D1", "VEN_1AC1", "DEV_089A", "PID_9302", "PID_089A"]) or
                                    "CORAL" in friendly_name or "EDGE TPU" in friendly_name):
                                _log(f"Found Google Coral Edge TPU on Windows: {dev.get('FriendlyName')}")
                                return True
                    except Exception as je:
                        stdout_upper = res.stdout.upper()
                        if any(k in stdout_upper for k in ["VID_1A6E", "VID_18D1", "VEN_1AC1", "CORAL", "EDGE TPU"]):
                            _log("Found Google Coral Edge TPU on Windows via fallback matching")
                            return True
            except Exception as e:
                _log(f"Coral detection on Windows failed: {e}")

        # 2. Linux: Check /sys/bus/usb and PCIe /dev/apex_*
        elif system == "Linux":
            # Check PCIe first (Apex driver)
            if Path("/dev/apex_0").exists() or Path("/sys/class/apex").is_dir():
                _log("Found Google Coral PCIe Accelerator (/dev/apex_0)")
                return True
            
            # Check USB devices directly in /sys/bus/usb/devices
            usb_dir = Path("/sys/bus/usb/devices")
            if usb_dir.is_dir():
                for path in usb_dir.glob("*/idVendor"):
                    try:
                        vid = path.read_text().strip().lower()
                        pid_path = Path(str(path).replace("idVendor", "idProduct"))
                        if pid_path.exists():
                            pid = pid_path.read_text().strip().lower()
                            if vid in ("1a6e", "18d1") and pid in ("089a", "9302"):
                                _log(f"Found Google Coral USB Accelerator (VID: {vid}, PID: {pid})")
                                return True
                    except Exception:
                        pass

        # 3. macOS: Query system_profiler for USB devices
        elif system == "Darwin":
            try:
                res = subprocess.run(["system_profiler", "SPUSBDataType"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    stdout_upper = res.stdout.upper()
                    if "1A6E" in stdout_upper or "18D1" in stdout_upper or "CORAL" in stdout_upper:
                        _log("Found Google Coral Edge TPU on macOS USB")
                        return True
            except Exception as e:
                _log(f"Coral detection on macOS failed: {e}")

        return False

    def _detect_windows_gpus_powershell(self) -> list:
        """Query active GPUs on Windows via PowerShell (wmic fallback)."""
        gpus = []
        try:
            import json
            args = [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion | ConvertTo-Json -Compress"
            ]
            res = subprocess.run(args, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    data = json.loads(res.stdout.strip())
                    if isinstance(data, dict):
                        data = [data]
                    for item in data:
                        name = item.get("Name", "")
                        ram = item.get("AdapterRAM", 0)
                        driver = item.get("DriverVersion", "")
                        
                        ram_mb = 0
                        if ram:
                            try:
                                val = int(ram)
                                if val < 0:
                                    val = val + 2**32
                                ram_mb = val // (1024 * 1024)
                            except Exception:
                                pass
                        
                        name_upper = name.upper()
                        vendor = "other"
                        if "NVIDIA" in name_upper:
                            vendor = "nvidia"
                        elif "AMD" in name_upper or "RADEON" in name_upper:
                            vendor = "amd"
                        elif "INTEL" in name_upper or "ARC(TM)" in name_upper:
                            vendor = "intel"
                            
                        gpus.append({
                            "Name": name,
                            "AdapterRAM": ram_mb,
                            "DriverVersion": driver,
                            "Vendor": vendor
                        })
                except Exception as je:
                    pass
        except Exception:
            pass
        return gpus

    def _try_cuda(self) -> bool:
        """Detect NVIDIA GPU via nvidia-smi and PowerShell fallback on Windows."""
        nvidia_smi = shutil.which("nvidia-smi")

        # Windows: check well-known paths if not on PATH
        if not nvidia_smi and platform.system() == "Windows":
            for candidate in [
                Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
                / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
                Path(os.environ.get("WINDIR", r"C:\Windows"))
                / "System32" / "nvidia-smi.exe",
            ]:
                if candidate.is_file():
                    nvidia_smi = str(candidate)
                    _log(f"Found nvidia-smi at {nvidia_smi}")
                    break

        if nvidia_smi:
            try:
                result = subprocess.run(
                    [nvidia_smi, "--query-gpu=name,memory.total,driver_version",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    line = result.stdout.strip().split("\n")[0]
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        self.backend = "cuda"
                        self.device = "cuda"
                        self.gpu_name = parts[0]
                        self.gpu_memory_mb = int(float(parts[1]))
                        self.driver_version = parts[2]
                        self.detection_details["nvidia_smi"] = line
                        _log(f"NVIDIA GPU: {self.gpu_name} ({self.gpu_memory_mb}MB, driver {self.driver_version})")
                        return True
            except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
                _log(f"nvidia-smi probe failed: {e}")

        # Windows WMI/PowerShell fallback
        if platform.system() == "Windows":
            gpus = self._detect_windows_gpus_powershell()
            nvidia_gpus = [g for g in gpus if g["Vendor"] == "nvidia"]
            if nvidia_gpus:
                g = nvidia_gpus[0]
                self.backend = "cuda"
                self.device = "cuda"
                self.gpu_name = g["Name"]
                self.gpu_memory_mb = g["AdapterRAM"]
                self.driver_version = g["DriverVersion"]
                self.detection_details["powershell_gpu"] = g
                _log(f"NVIDIA GPU (PowerShell): {self.gpu_name} ({self.gpu_memory_mb}MB)")
                return True

        return False

    def _try_rocm(self) -> bool:
        """Detect AMD GPU via amd-smi (preferred), rocm-smi, or Windows PowerShell."""
        if platform.system() == "Windows":
            gpus = self._detect_windows_gpus_powershell()
            amd_gpus = [g for g in gpus if g["Vendor"] == "amd"]
            if amd_gpus:
                g = amd_gpus[0]
                self.backend = "rocm"
                self.device = "cpu"
                self.gpu_name = g["Name"]
                self.gpu_memory_mb = g["AdapterRAM"]
                self.driver_version = g["DriverVersion"]
                self.detection_details["powershell_gpu"] = g
                _log(f"AMD GPU (PowerShell): {self.gpu_name} ({self.gpu_memory_mb}MB)")
                try:
                    import torch
                    if torch.cuda.is_available():
                        self.device = "cuda"
                except ImportError:
                    pass
                return True

        has_amd_smi = shutil.which("amd-smi") is not None
        has_rocm_smi = shutil.which("rocm-smi") is not None
        has_rocm_dir = Path("/opt/rocm").is_dir()

        if not (has_amd_smi or has_rocm_smi or has_rocm_dir):
            return False

        self.backend = "rocm"
        try:
            import torch
            if torch.cuda.is_available():
                self.device = "cuda"
            else:
                self.device = "cpu"
                _log("PyTorch CUDA/ROCm not available, using CPU for PyTorch fallback")
        except ImportError:
            self.device = "cpu"

        # Strategy 1: amd-smi static --json (ROCm 6.3+/7.x, richest output)
        if has_amd_smi:
            try:
                result = subprocess.run(
                    ["amd-smi", "static", "--json"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    import json as _json
                    data = _json.loads(result.stdout)
                    gpu_list = data.get("gpu_data", data) if isinstance(data, dict) else data
                    if isinstance(gpu_list, list) and len(gpu_list) > 0:
                        def _vram_mb(g):
                            vram = g.get("vram", {}).get("size", {})
                            if isinstance(vram, dict):
                                return int(vram.get("value", 0))
                            return 0

                        best_gpu = max(gpu_list, key=_vram_mb)
                        best_idx = gpu_list.index(best_gpu)
                        asic = best_gpu.get("asic", {})
                        vram = best_gpu.get("vram", {}).get("size", {})

                        self.gpu_name = asic.get("market_name", "AMD GPU")
                        self.gpu_memory_mb = int(vram.get("value", 0)) if isinstance(vram, dict) else 0
                        self.detection_details["amd_smi"] = {
                            "gpu_index": best_idx,
                            "gfx_version": asic.get("target_graphics_version", ""),
                            "total_gpus": len(gpu_list),
                        }

                        if len(gpu_list) > 1:
                            os.environ["HIP_VISIBLE_DEVICES"] = str(best_idx)
                            os.environ["ROCR_VISIBLE_DEVICES"] = str(best_idx)
                            _log(f"Multi-GPU: pinned to GPU {best_idx} ({self.gpu_name})")
            except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, Exception) as e:
                _log(f"amd-smi probe failed: {e}")

        # Strategy 2: rocm-smi fallback (legacy ROCm <6.3)
        if not self.gpu_name and has_rocm_smi:
            try:
                result = subprocess.run(
                    ["rocm-smi", "--showproductname", "--csv"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
                    if len(lines) > 1:
                        self.gpu_name = lines[1].split(",")[0].strip()
                self.detection_details["rocm_smi"] = result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

            try:
                result = subprocess.run(
                    ["rocm-smi", "--showmeminfo", "vram", "--csv"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n")[1:]:
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                self.gpu_memory_mb = int(float(parts[0].strip()) / (1024 * 1024))
                            except ValueError:
                                pass
                            break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        _log(f"AMD ROCm GPU: {self.gpu_name or 'detected'} ({self.gpu_memory_mb}MB)")
        return True

    def _try_mps(self) -> bool:
        """Detect Apple Silicon via uname + sysctl."""
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            return False

        self.backend = "mps"
        self.device = "mps"

        # Get chip name
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                self.gpu_name = result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            self.gpu_name = "Apple Silicon"

        # Get total memory (shared with GPU on Apple Silicon)
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                self.gpu_memory_mb = int(int(result.stdout.strip()) / (1024 * 1024))
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

        _log(f"Apple Silicon: {self.gpu_name} ({self.gpu_memory_mb}MB unified)")
        return True

    def _try_intel(self) -> bool:
        """Detect Intel OpenVINO-capable hardware (CPU, GPU, NPU)."""
        has_intel_gpu = False
        intel_gpu_info = None
        if platform.system() == "Windows":
            gpus = self._detect_windows_gpus_powershell()
            intel_gpus = [g for g in gpus if g["Vendor"] == "intel"]
            if intel_gpus:
                has_intel_gpu = True
                intel_gpu_info = intel_gpus[0]

        has_openvino = False
        try:
            import openvino
            has_openvino = True
        except ImportError:
            has_openvino = Path("/opt/intel/openvino").is_dir()

        if not has_openvino:
            is_intel_cpu = False
            try:
                if platform.system() == "Linux":
                    with open("/proc/cpuinfo") as f:
                        cpuinfo = f.read()
                    if "GenuineIntel" in cpuinfo:
                        is_intel_cpu = True
                elif platform.system() == "Windows":
                    is_intel_cpu = "INTEL" in platform.processor().upper() or "INTEL" in os.environ.get("PROCESSOR_IDENTIFIER", "").upper()
            except Exception:
                pass

            if is_intel_cpu or has_intel_gpu:
                self.backend = "intel"
                self.device = "cpu"
                if has_intel_gpu and intel_gpu_info:
                    self.gpu_name = intel_gpu_info["Name"]
                    self.gpu_memory_mb = intel_gpu_info["AdapterRAM"]
                    self.driver_version = intel_gpu_info["DriverVersion"]
                    self.detection_details["powershell_gpu"] = intel_gpu_info
                else:
                    self.gpu_name = "Intel CPU"
                _log("Intel hardware detected (no OpenVINO installed)")
                return True
            return False

        self.backend = "intel"
        self.device = "cpu"
        self.gpu_name = "Intel (OpenVINO)"

        try:
            from openvino.runtime import Core
            core = Core()
            devices = core.available_devices
            self.detection_details["openvino_devices"] = devices
            if "GPU" in devices:
                self.gpu_name = "Intel GPU (OpenVINO)"
                if has_intel_gpu and intel_gpu_info:
                    self.gpu_name = f"{intel_gpu_info['Name']} (OpenVINO)"
                    self.gpu_memory_mb = intel_gpu_info["AdapterRAM"]
            if "NPU" in devices:
                self.gpu_name = "Intel NPU (OpenVINO)"
            _log(f"OpenVINO devices: {devices}")
        except Exception:
            if has_intel_gpu and intel_gpu_info:
                self.gpu_name = f"{intel_gpu_info['Name']} (OpenVINO)"
                self.gpu_memory_mb = intel_gpu_info["AdapterRAM"]

        _log(f"Intel: {self.gpu_name}")
        return True

    def _fallback_cpu(self):
        """CPU-only fallback."""
        self.backend = "cpu"
        self.device = "cpu"
        self.gpu_name = ""

        # Report CPU info
        try:
            self.detection_details["cpu"] = platform.processor() or "unknown"
        except Exception:
            pass

        _log("No GPU detected, using CPU backend")

    def _check_rocm_runtime(self):
        """Verify onnxruntime has ROCm provider, not just CPU."""
        import onnxruntime
        providers = onnxruntime.get_available_providers()
        if "ROCmExecutionProvider" in providers or "MIGraphXExecutionProvider" in providers:
            _log(f"onnxruntime ROCm providers: {providers}")
            return True
        _log(f"onnxruntime providers: {providers} — ROCmExecutionProvider not found")
        _log("Fix: pip uninstall onnxruntime && pip install onnxruntime-rocm")
        raise ImportError("ROCmExecutionProvider not available")

    def _check_mps_runtime(self):
        """Verify onnxruntime has CoreML provider for Apple GPU/ANE acceleration.

        ONNX Runtime + CoreMLExecutionProvider bypasses the broken
        MPSGraphExecutable MLIR pipeline (macOS 26.x) while still routing
        inference through CoreML to leverage GPU and Neural Engine.
        """
        import onnxruntime
        providers = onnxruntime.get_available_providers()
        if "CoreMLExecutionProvider" in providers:
            _log(f"onnxruntime CoreML provider available: {providers}")
            return True
        _log(f"onnxruntime providers: {providers} — CoreMLExecutionProvider not found")
        _log("Fix: pip install onnxruntime  (arm64 macOS wheel includes CoreML EP)")
        raise ImportError("CoreMLExecutionProvider not available")

    def _check_framework(self) -> bool:
        """Check if the optimized inference runtime is importable and compatible."""
        checks = {
            "cuda": lambda: __import__("torch"),
            "rocm": lambda: self._check_rocm_runtime(),
            "mps": lambda: self._check_mps_runtime(),
            "intel": lambda: __import__("openvino"),
            "cpu": lambda: __import__("onnxruntime"),
        }

        check = checks.get(self.backend)
        if not check:
            return False
        try:
            check()
            return True
        except ImportError:
            _log(f"Optimized runtime not installed for {self.backend}, "
                 f"will use PyTorch fallback")
            return False

    # ─── Model export & loading ──────────────────────────────────────────

    def get_optimized_path(self, model_name: str) -> Path:
        """Get the expected path for the optimized model."""
        spec = BACKEND_SPECS.get(self.backend, BACKEND_SPECS["cpu"])
        return Path(f"{model_name}{spec.model_suffix}")

    def export_model(self, model, model_name: str) -> Optional[Path]:
        """Export PyTorch model to optimal format. Returns path or None."""
        if not self.framework_ok:
            _log(f"Skipping export — {self.backend} runtime not available")
            return None

        spec = BACKEND_SPECS.get(self.backend, BACKEND_SPECS["cpu"])
        optimized_path = self.get_optimized_path(model_name)

        # Already exported
        if optimized_path.exists():
            _log(f"Cached model found: {optimized_path}")
            return optimized_path

        # Guard: numpy 2.x breaks coremltools PyTorch→MIL converter
        # (TypeError: only 0-dimensional arrays can be converted to Python scalars)
        if spec.export_format == "coreml":
            try:
                import numpy as np
                np_major = int(np.__version__.split('.')[0])
                if np_major >= 2:
                    _log(f"numpy {np.__version__} detected — CoreML export "
                         f"requires numpy<2.0.0 (coremltools incompatibility)")
                    _log("Fix: pip install 'numpy>=1.24,<2.0'")
                    return None
            except Exception:
                pass  # If numpy check fails, try export anyway

        try:
            _log(f"Exporting {model_name}.pt → {spec.export_format} "
                 f"(one-time, may take 30-120s)...")
            t0 = time.perf_counter()

            export_kwargs = {
                "format": spec.export_format,
                "half": spec.half,
            }
            export_kwargs.update(spec.extra_export_args)

            exported = model.export(**export_kwargs)
            self.export_ms = (time.perf_counter() - t0) * 1000

            exported_path = Path(exported)
            if exported_path.exists():
                _log(f"Export complete: {exported_path} ({self.export_ms:.0f}ms)")
                return exported_path

            _log(f"Export returned {exported} but path not found")
        except Exception as e:
            _log(f"Export failed ({spec.export_format}): {e}")

        return None

    def _load_coreml_with_compute_units(self, model_path: str):
        """
        Load a CoreML model via YOLO with specific compute_units.

        Monkey-patches coremltools.MLModel to inject compute_units
        (e.g. CPU_AND_NE for Neural Engine) since ultralytics doesn't
        expose this parameter. Patch is scoped and immediately restored.
        """
        from ultralytics import YOLO

        # Map string config → coremltools enum
        _COMPUTE_UNIT_MAP = {
            "all": "ALL",
            "cpu_only": "CPU_ONLY",
            "cpu_and_gpu": "CPU_AND_GPU",
            "cpu_and_ne": "CPU_AND_NE",
        }

        ct_enum_name = _COMPUTE_UNIT_MAP.get(self.compute_units)
        if not ct_enum_name:
            _log(f"Unknown compute_units '{self.compute_units}', using default")
            return YOLO(model_path)

        try:
            import coremltools as ct
            target_units = getattr(ct.ComputeUnit, ct_enum_name, None)
            if target_units is None:
                _log(f"coremltools.ComputeUnit.{ct_enum_name} not available")
                return YOLO(model_path)

            # Temporarily patch MLModel to inject compute_units
            _OrigMLModel = ct.models.MLModel

            class _PatchedMLModel(_OrigMLModel):
                def __init__(self, *args, **kwargs):
                    kwargs.setdefault('compute_units', target_units)
                    super().__init__(*args, **kwargs)

            ct.models.MLModel = _PatchedMLModel
            try:
                model = YOLO(model_path)
            finally:
                ct.models.MLModel = _OrigMLModel  # Always restore

            _log(f"CoreML model loaded with compute_units={ct_enum_name} "
                 f"(Neural Engine preferred)")
            return model

        except ImportError:
            _log("coremltools not available, loading without compute_units")
            return YOLO(model_path)

    # ── ONNX model download from HuggingFace ──────────────────────────

    # Maps model base name → onnx-community HuggingFace repo
    _ONNX_HF_REPOS = {
        "yolo26n": "onnx-community/yolo26n-ONNX",
        "yolo26s": "onnx-community/yolo26s-ONNX",
        "yolo26m": "onnx-community/yolo26m-ONNX",
        "yolo26l": "onnx-community/yolo26l-ONNX",
    }

    def _download_onnx_from_hf(self, model_name: str, dest_path: Path) -> bool:
        """Download pre-built ONNX model from onnx-community on HuggingFace.

        Uses urllib (no extra dependencies). Downloads to dest_path.
        Returns True on success, False on failure.
        """
        repo = self._ONNX_HF_REPOS.get(model_name)
        if not repo:
            _log(f"No HuggingFace repo for {model_name}")
            return False

        url = f"https://huggingface.co/{repo}/resolve/main/onnx/model.onnx"
        names_url = None  # class names not available on HF, use bundled nano names

        _log(f"Downloading {model_name}.onnx from {repo}...")
        try:
            import urllib.request
            import shutil

            # Download ONNX model
            tmp_path = str(dest_path) + ".download"
            with urllib.request.urlopen(url, timeout=60.0) as resp, open(tmp_path, 'wb') as f:
                shutil.copyfileobj(resp, f)

            # Rename to final path
            Path(tmp_path).rename(dest_path)
            size_mb = dest_path.stat().st_size / 1e6
            _log(f"Downloaded {model_name}.onnx ({size_mb:.1f} MB)")

            # Create class names JSON if missing (COCO 80 — same for all YOLO models)
            names_path = Path(str(dest_path).replace('.onnx', '_names.json'))
            if not names_path.exists():
                # Try copying from nano (which is shipped in the repo)
                nano_names = dest_path.parent / "yolo26n_names.json"
                if nano_names.exists():
                    shutil.copy2(str(nano_names), str(names_path))
                    _log(f"Copied class names from yolo26n_names.json")
                else:
                    # Generate default COCO names
                    import json
                    coco_names = {str(i): f"class_{i}" for i in range(80)}
                    with open(str(names_path), 'w') as f:
                        json.dump(coco_names, f)
                    _log("Generated default class names")

            return True
        except Exception as e:
            _log(f"HuggingFace download failed: {e}")
            # Clean up partial download
            for p in [str(dest_path) + ".download", str(dest_path)]:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
            return False

    def _load_onnx(self, onnx_path: str):
        """Load ONNX model with best available execution provider.

        Returns an _OnnxCoreMLModel wrapper that is compatible with the
        ultralytics model(frame_path, conf=...) call pattern.
        """
        import onnxruntime as ort

        providers = ['CPUExecutionProvider']
        if self.backend == 'cuda':
            providers = ['CUDAExecutionProvider'] + providers
        elif self.backend == 'mps':
            providers = ['CoreMLExecutionProvider'] + providers
        elif self.backend == 'directml':
            providers = ['DirectMLExecutionProvider'] + providers
            
        session = ort.InferenceSession(onnx_path, providers=providers)
        active = session.get_providers()
        _log(f"ONNX session providers: {active}")

        # Load class names from companion JSON (avoids torch/ultralytics dep)
        import json
        names_path = onnx_path.replace('.onnx', '_names.json')
        try:
            with open(names_path) as f:
                raw = json.load(f)
            class_names = {int(k): v for k, v in raw.items()}
            _log(f"Loaded {len(class_names)} class names from {Path(names_path).name}")
        except FileNotFoundError:
            _log("WARNING: No class names JSON found, using generic defaults")
            class_names = {i: f"class_{i}" for i in range(80)}

        return _OnnxCoreMLModel(session, class_names)

    def load_optimized(self, model_name: str, use_optimized: bool = True):
        """
        Load the best available model for this hardware.

        Returns:
            (model, format_str) — the YOLO model and its format name
        """
        t0 = time.perf_counter()

        # Check if an ONNX model file exists directly (avoids torch/ultralytics imports if so)
        onnx_path = Path(f"{model_name}.onnx")
        if onnx_path.exists():
            try:
                model = self._load_onnx(str(onnx_path))
                self.load_ms = (time.perf_counter() - t0) * 1000
                _log(f"Loaded ONNX model directly via ONNX Runtime ({self.load_ms:.0f}ms)")
                return model, "onnx"
            except Exception as e:
                _log(f"Direct ONNX load failed: {e}. Falling back to default loading pipeline.")

        if use_optimized and self.framework_ok:
            # Try loading from cache first (no export needed)
            optimized_path = self.get_optimized_path(model_name)
            if optimized_path.exists():
                try:
                    # MPS: use ONNX Runtime + CoreML EP for fast inference
                    if self.backend == "mps" or str(optimized_path).endswith('.onnx'):
                        model = self._load_onnx(str(optimized_path))
                    else:
                        from ultralytics import YOLO
                        model = YOLO(str(optimized_path))
                        if self.export_format == "pytorch":
                            model.to(self.device)
                    self.load_ms = (time.perf_counter() - t0) * 1000
                    _log(f"Loaded {self.export_format} model ({self.load_ms:.0f}ms)")
                    return model, self.export_format
                except Exception as e:
                    _log(f"Failed to load cached model: {e}")

            # Try downloading pre-built ONNX from HuggingFace (no torch needed)
            if self.export_format == "onnx" and self._download_onnx_from_hf(model_name, optimized_path):
                try:
                    if self.backend == "mps" or str(optimized_path).endswith('.onnx'):
                        model = self._load_onnx(str(optimized_path))
                    else:
                        from ultralytics import YOLO
                        model = YOLO(str(optimized_path))
                    self.load_ms = (time.perf_counter() - t0) * 1000
                    _log(f"Loaded HuggingFace ONNX model ({self.load_ms:.0f}ms)")
                    return model, self.export_format
                except Exception as e:
                    _log(f"Failed to load HF-downloaded model: {e}")

            # Try exporting then loading
            from ultralytics import YOLO
            pt_model = YOLO(f"{model_name}.pt")
            exported = self.export_model(pt_model, model_name)
            if exported:
                try:
                    # MPS/ONNX: use ONNX Runtime
                    if self.backend == "mps" or str(exported).endswith('.onnx'):
                        model = self._load_onnx(str(exported))
                    else:
                        from ultralytics import YOLO
                        model = YOLO(str(exported))
                        if self.export_format == "pytorch":
                            model.to(self.device)
                    self.load_ms = (time.perf_counter() - t0) * 1000
                    _log(f"Loaded freshly exported {self.export_format} model ({self.load_ms:.0f}ms)")
                    return model, self.export_format
                except Exception as e:
                    _log(f"Failed to load exported model: {e}")

            # Fallback: use the PT model we already loaded
            _log("Falling back to PyTorch model")
            fallback_device = self.device
            if fallback_device == "cuda":
                try:
                    import torch
                    if not torch.cuda.is_available():
                        fallback_device = "cpu"
                        _log("torch.cuda not available, falling back to CPU")
                except ImportError:
                    fallback_device = "cpu"
            pt_model.to(fallback_device)
            self.device = fallback_device
            self.load_ms = (time.perf_counter() - t0) * 1000
            return pt_model, "pytorch"

        # No optimization requested or framework missing
        from ultralytics import YOLO
        model = YOLO(f"{model_name}.pt")
        fallback_device = self.device
        if fallback_device == "cuda":
            try:
                import torch
                if not torch.cuda.is_available():
                    fallback_device = "cpu"
                    _log("torch.cuda not available, falling back to CPU")
            except ImportError:
                fallback_device = "cpu"
        model.to(fallback_device)
        self.device = fallback_device
        self.load_ms = (time.perf_counter() - t0) * 1000
        return model, "pytorch"

    def to_dict(self) -> dict:
        """Serialize environment info for JSON output."""
        d = {
            "backend": self.backend,
            "device": self.device,
            "export_format": self.export_format,
            "gpu_name": self.gpu_name,
            "gpu_memory_mb": self.gpu_memory_mb,
            "driver_version": self.driver_version,
            "framework_ok": self.framework_ok,
            "coral_detected": self.coral_detected,
            "export_ms": round(self.export_ms, 1),
            "load_ms": round(self.load_ms, 1),
        }
        if self.backend == "mps":
            d["compute_units"] = self.compute_units
        return d


# ─── CLI: run standalone for diagnostics ─────────────────────────────────────

if __name__ == "__main__":
    env = HardwareEnv.detect()
    print(json.dumps(env.to_dict(), indent=2))
