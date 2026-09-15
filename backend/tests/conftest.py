import sys
from pathlib import Path

# Ensure Sentinel root and backend root are on sys.path for all pytest runs
sentinel_root = Path(__file__).resolve().parent.parent.parent
backend_dir = sentinel_root / "backend"

for path_str in [str(sentinel_root), str(backend_dir)]:
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

import os
os.environ.setdefault("RTSP_USER", "test_user")
os.environ.setdefault("RTSP_PASSWORD", "test_pass")
