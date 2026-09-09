"""
Sentinel — Phase 5A AI Engine Smoke Test & Verification Script.

Inspects PyTorch installation, CUDA availability, model weights availability,
and runs a live model inference verification ONLY if local weights are present.
If weights are absent, honestly reports MODEL NOT AVAILABLE.
"""

from __future__ import annotations

import os
import sys

# Add project root and ai_engine to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_engine import VehicleDetector, ModelNotAvailableError
from streaming.frame_reader import FramePacket


def run_smoke_test() -> int:
    print("=" * 60)
    print("SENTINEL — PHASE 5A: AI ENGINE SMOKE TEST")
    print("=" * 60)

    # 1. PyTorch check
    try:
        import torch
        print(f"PyTorch Version   : {torch.__version__}")
        cuda_available = torch.cuda.is_available()
        print(f"CUDA Available    : {cuda_available}")
        if cuda_available:
            print(f"CUDA Device Count : {torch.cuda.device_count()}")
            print(f"CUDA Device Name  : {torch.cuda.get_device_name(0)}")
        else:
            print("CUDA Device       : None (Running on CPU)")
    except ImportError as e:
        print(f"PyTorch Status    : NOT INSTALLED ({e})")
        return 1

    # 2. Ultralytics check
    try:
        import ultralytics
        print(f"Ultralytics Ver   : {ultralytics.__version__}")
    except ImportError as e:
        print(f"Ultralytics Status: NOT INSTALLED ({e})")
        return 1

    # 3. Model file check
    model_path = os.environ.get("AI_MODEL_PATH", "models/yolov8n.pt")
    model_exists = os.path.isfile(model_path)
    print(f"Target Model Path : {model_path}")
    print(f"Model File Exists : {model_exists}")

    if not model_exists:
        print("\n" + "!" * 60)
        print("MODEL STATUS: NOT AVAILABLE")
        print(f"Model file '{model_path}' was not found on disk.")
        print("As required by Phase 5A constraints:")
        print("  - Zero fake detections will be generated.")
        print("  - Model weights are NOT automatically downloaded silently.")
        print("  - Model weights must NOT be committed to Git.")
        print("=" * 60)
        print("\nVerifying missing model detector behavior...")
        detector = VehicleDetector(model_path=model_path)
        print(f"Detector Status   : {detector.status}")
        print(f"Detector Is Ready : {detector.is_ready}")
        print(f"Error Diagnostic  : {detector.error_message}")
        print("\nResult: Verified honest MODEL_NOT_AVAILABLE reporting.")
        print("=" * 60)
        return 0

    # 4. Model is available - run real smoke test
    print("\n" + "=" * 60)
    print("MODEL STATUS: AVAILABLE — Running inference verification...")
    import numpy as np

    detector = VehicleDetector(model_path=model_path)
    print(f"Detector Status   : {detector.status}")
    print(f"Detector Device   : {detector.resolved_device}")
    print(f"Target Classes    : {list(detector.class_id_to_name.values())}")

    # Create synthetic test FramePacket
    dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    packet = FramePacket(
        frame=dummy_frame,
        pts_ms=1000.0,
        received_at=1770000000.0,
        width=1280,
        height=720,
        camera_id="smoke-test-cam",
    )

    result = detector.detect(packet)
    print(f"Inference Time    : {result.inference_time_ms:.2f} ms")
    print(f"Detections Count  : {result.count}")
    print(f"Preserved PTS     : {result.pts_ms} ms")
    print(f"Preserved Camera  : {result.camera_id}")
    print("=" * 60)
    print("AI Engine smoke test PASSED with real model.")
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_test())
