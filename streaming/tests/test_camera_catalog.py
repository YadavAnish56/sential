import json
import pytest
from unittest.mock import patch, MagicMock
import urllib.error

from streaming.camera_catalog import CameraCatalog, CameraCatalogItem


SAMPLE_CATALOG_LIST = json.dumps([
    {
        "camera_id": "cam-1",
        "name": "Junction A",
        "location": "Ahmedabad",
        "codec": "H.264",
        "live": True,
        "rtsp_url": "rtsp://live.corp8.cloud:8554/stream/1",
        "webrtc_url": "https://live.corp8.cloud/whep/1",
        "hls_url": "https://live.corp8.cloud/hls/1/index.m3u8",
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
    },
    {
        "id": "cam-7",
        "camera_name": "Highway Gate 7",
        "location": "Surat",
        "video_codec": "HEVC",
        "status": "online",
        "stream_url": "rtsp://live.corp8.cloud:8554/stream/7",
        "properties": {
            "width": 1280,
            "height": 720,
            "fps": 25.0,
        }
    }
])

SAMPLE_CATALOG_DICT = json.dumps({
    "cameras": [
        {
            "camera_code": "CAM-03",
            "name": "Toll Plaza",
            "rtsp": "rtsp://camera.internal/stream",
            "live": False,
        }
    ]
})


def test_parse_catalog_list():
    items = CameraCatalog.parse_catalog_json(SAMPLE_CATALOG_LIST)
    assert len(items) == 2

    cam1 = items[0]
    assert cam1.camera_id == "cam-1"
    assert cam1.name == "Junction A"
    assert cam1.location == "Ahmedabad"
    assert cam1.codec == "H.264"
    assert cam1.is_live is True
    assert cam1.rtsp_url == "rtsp://live.corp8.cloud:8554/stream/1"
    assert cam1.webrtc_url == "https://live.corp8.cloud/whep/1"
    assert cam1.hls_url == "https://live.corp8.cloud/hls/1/index.m3u8"
    assert cam1.width == 1920
    assert cam1.height == 1080
    assert cam1.fps_hint == 30.0

    cam2 = items[1]
    assert cam2.camera_id == "cam-7"
    assert cam2.name == "Highway Gate 7"
    assert cam2.location == "Surat"
    assert cam2.codec == "HEVC"
    assert cam2.is_live is True
    assert cam2.rtsp_url == "rtsp://live.corp8.cloud:8554/stream/7"
    assert cam2.width == 1280
    assert cam2.height == 720
    assert cam2.fps_hint == 25.0


def test_parse_catalog_dict_wrapper():
    items = CameraCatalog.parse_catalog_json(SAMPLE_CATALOG_DICT)
    assert len(items) == 1
    assert items[0].camera_id == "CAM-03"
    assert items[0].rtsp_url == "rtsp://camera.internal/stream"
    assert items[0].is_live is False


def test_parse_invalid_json():
    items = CameraCatalog.parse_catalog_json("invalid json string {}")
    assert items == []


@patch("urllib.request.urlopen")
def test_fetch_catalog_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.geturl.return_value = "http://mock-host/api/ingest"
    mock_resp.read.return_value = SAMPLE_CATALOG_LIST.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    catalog = CameraCatalog(catalog_url="http://mock-host/api/ingest")
    result = catalog.fetch()

    assert result.success is True
    assert result.status_code == 200
    assert len(result.cameras) == 2


@patch("urllib.request.urlopen")
def test_fetch_catalog_auth_required_redirect(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.getcode.return_value = 200
    mock_resp.headers = {"Content-Type": "text/html"}
    mock_resp.geturl.return_value = "https://cctv.corp8.cloud/auth/login"
    mock_resp.read.return_value = b"<html><title>Sign In</title></html>"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    catalog = CameraCatalog(catalog_url="http://live.corp8.cloud/api/ingest")
    result = catalog.fetch()

    assert result.success is False
    assert result.requires_auth is True
    assert result.redirect_url == "https://cctv.corp8.cloud/auth/login"


@patch("urllib.request.urlopen")
def test_fetch_catalog_http_403_forbidden(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="http://live.corp8.cloud/api/ingest",
        code=403,
        msg="Forbidden",
        hdrs={},
        fp=None,
    )

    catalog = CameraCatalog(catalog_url="http://live.corp8.cloud/api/ingest")
    result = catalog.fetch()

    assert result.success is False
    assert result.status_code == 403
    assert result.requires_auth is True
