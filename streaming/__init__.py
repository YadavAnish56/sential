"""
Sentinel Streaming Foundation (Phase 4).

Modular RTSP ingestion, presentation timing isolation, automatic reconnect,
and operational health monitoring.
"""

from streaming.camera_catalog import CameraCatalog, CameraCatalogItem, CatalogFetchResult
from streaming.frame_reader import FramePacket, FrameReader
from streaming.health import StreamHealth, StreamHealthTracker, StreamStatus
from streaming.pts import PTSInfo, PTSTracker
from streaming.reconnect import ReconnectManager
from streaming.rtsp_client import RTSPClient
from streaming.stream_manager import CameraStreamSession, StreamManager

__all__ = [
    "CameraCatalog",
    "CameraCatalogItem",
    "CatalogFetchResult",
    "FramePacket",
    "FrameReader",
    "StreamHealth",
    "StreamHealthTracker",
    "StreamStatus",
    "PTSInfo",
    "PTSTracker",
    "ReconnectManager",
    "RTSPClient",
    "CameraStreamSession",
    "StreamManager",
]
