import urllib.request
import urllib.error
import json

# Standard WebRTC SDP offer
DUMMY_SDP = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96 97\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtcp:9 IN IP4 0.0.0.0\r\n"
    "a=ice-ufrag:test\r\n"
    "a=ice-pwd:testpassword0123456789\r\n"
    "a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
    "a=setup:actpass\r\n"
    "a=mid:0\r\n"
    "a=sendrecv\r\n"
    "a=rtpmap:96 H264/90000\r\n"
    "a=rtpmap:97 H265/90000\r\n"
)

# Standard H264-only offer (representing standard web browsers without HEVC)
H264_ONLY_SDP = (
    "v=0\r\n"
    "o=- 0 0 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtcp:9 IN IP4 0.0.0.0\r\n"
    "a=ice-ufrag:test\r\n"
    "a=ice-pwd:testpassword0123456789\r\n"
    "a=fingerprint:sha-256 00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00\r\n"
    "a=setup:actpass\r\n"
    "a=mid:0\r\n"
    "a=sendrecv\r\n"
    "a=rtpmap:96 H264/90000\r\n"
)

cameras_to_test = [f"CAM-00{i}" for i in range(1, 9)] + ["CAM-CRUD"]

print("=== STEP 5: VERIFY WHEP PROXY RESOLUTION ===")
print(f"{'Camera':<10} | {'WHEP Status':<12} | {'SDP Offer':<12} | Resolution / Upstream Result")
print("-" * 80)

for code in cameras_to_test:
    url = f"http://localhost:8000/api/cameras/{code}/whep"
    req = urllib.request.Request(
        url,
        data=DUMMY_SDP.encode("utf-8"),
        headers={"Content-Type": "application/sdp"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            answer = resp.read()
            print(f"{code:<10} | {resp.status:<12} | {'H264+H265':<12} | Negotiated (SDP Answer: {len(answer)} bytes)")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace").strip()
        print(f"{code:<10} | HTTP {e.code:<7} | {'H264+H265':<12} | {body}")
    except Exception as e:
        print(f"{code:<10} | ERROR        | {'H264+H265':<12} | {e}")

# Specifically test CAM-006 with standard H264-only browser offer
print("\n--- Testing CAM-006 Specific Codec Behavior (H.264 vs H.265) ---")
req_h264 = urllib.request.Request(
    "http://localhost:8000/api/cameras/CAM-006/whep",
    data=H264_ONLY_SDP.encode("utf-8"),
    headers={"Content-Type": "application/sdp"},
    method="POST"
)
try:
    with urllib.request.urlopen(req_h264, timeout=5.0) as resp:
        print(f"CAM-006 (H264-only offer): {resp.status} Negotiated ({len(resp.read())} B)")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace").strip()
    print(f"CAM-006 (H264-only offer): HTTP {e.code} ({body})")
    print("  -> Expected: Upstream MediaMTX stream is HEVC/H.265; SDP without H.265 produces clean HTTP 400 mapped to HTTP 502 without credential leak.")
