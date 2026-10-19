"""Bumper OCR device choice (live/ocr.py cuda_device) and the CUDA -> CPU fallback while reading.
No GPU needed: the CUDA provider and a failing CUDA session are faked."""
import os
import sys
import types

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "live"))

import ocr as OCRM  # noqa: E402


@pytest.fixture
def cuda_listed(monkeypatch):
    import onnxruntime as ort
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr(ort, "preload_dlls", lambda *a, **k: None, raising=False)
    for k in ("OCR_CUDA", "FORCE_CPU", "OCR_GPU", "GPU_DEVICE"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_separate_process_stays_on_cpu(cuda_listed):
    cuda_listed.setitem(sys.modules, "detector", types.SimpleNamespace(CUDA=False))
    assert OCRM.cuda_device() is None                       # OCR worker / recorder: no CUDA detector here
    cuda_listed.delitem(sys.modules, "detector")
    assert OCRM.cuda_device(1) is None


def test_shares_a_cuda_detector(cuda_listed):
    cuda_listed.setitem(sys.modules, "detector", types.SimpleNamespace(CUDA=True))
    cuda_listed.setenv("GPU_DEVICE", "1")
    assert OCRM.cuda_device() == 1
    cuda_listed.setenv("OCR_GPU", "0")
    assert OCRM.cuda_device() == 0
    assert OCRM.cuda_device(2) == 2
    assert OCRM.cuda_device("cpu") is None
    cuda_listed.setenv("OCR_CUDA", "0")
    assert OCRM.cuda_device() is None
    cuda_listed.setenv("OCR_CUDA", "auto"); cuda_listed.setenv("FORCE_CPU", "1")
    assert OCRM.cuda_device() is None


def test_ocr_cuda_1_in_any_process(cuda_listed):
    cuda_listed.delitem(sys.modules, "detector", raising=False)
    cuda_listed.setenv("OCR_CUDA", "1")
    assert OCRM.cuda_device() == 0


def test_no_cuda_provider(monkeypatch):
    import onnxruntime as ort
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    monkeypatch.setenv("OCR_CUDA", "1")
    assert OCRM.cuda_device() is None


def test_cuda_failure_falls_back_to_cpu(monkeypatch):
    monkeypatch.setenv("FORCE_CPU", "1")
    o = OCRM.BumperOCR()
    assert o.device == "CPUExecutionProvider" and o.fallback is None
    crop = np.full((60, 120, 3), 255, np.uint8)
    want = o.read_many([crop], ["254", "1678"])

    class Broken:                                           # a CUDA session that runs out of GPU memory
        def run(self, *a, **k):
            raise RuntimeError("[ONNXRuntimeError] : 6 : RUNTIME_EXCEPTION :\nCUDA failure 2: out of memory")
    o.det, o.rec, o.device = Broken(), Broken(), "CUDAExecutionProvider"
    got = o.read_many([crop], ["254", "1678"])
    assert o.device == "CPUExecutionProvider"
    assert "out of memory" in o.fallback
    assert [len(r) for r in got] == [len(r) for r in want]
    X = np.zeros((1, 3, 192, 192), np.float32)
    assert o._run(False, X).shape[-2:] == (192, 192)        # CPU sessions from now on

    o.det = Broken()                                        # on the CPU an error is an error
    with pytest.raises(RuntimeError):
        o._run(False, X)


def test_cuda_that_fails_to_start_means_cpu(monkeypatch):
    import onnxruntime as ort
    real = ort.InferenceSession

    def fake(path, so, providers, **kw):
        if any((p[0] if isinstance(p, tuple) else p) == "CUDAExecutionProvider" for p in providers):
            assert kw.get("enable_fallback") == 0          # no ORT "EP Error" print + silent retry
            raise RuntimeError("CUDA failure 2: out of memory ; GPU=0 ; expr=cudaSetDevice(info_.device_id);")
        return real(path, so, providers=providers)
    monkeypatch.setattr(ort, "InferenceSession", fake)
    monkeypatch.setattr(OCRM, "cuda_device", lambda device=None: 0)
    o = OCRM.BumperOCR()
    assert o.device == "CPUExecutionProvider" and "out of memory" in o.fallback
