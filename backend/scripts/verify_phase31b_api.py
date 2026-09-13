import urllib.request
import urllib.error
import json

print("=== STEP 4: VERIFY GET /api/cameras ===")
req = urllib.request.Request("http://localhost:8000/api/cameras")
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))

total = data.get("total")
cameras = data.get("cameras", [])
print(f"Total cameras returned: {total}")
print(f"{'ID':<4} | {'Code':<10} | {'Name':<24} | {'Status':<9} | {'Lat':<8} | {'Lon':<8} | Stream URL")
print("-" * 100)

for c in cameras:
    lat = f"{c.get('latitude'):.4f}" if c.get('latitude') is not None else "NULL"
    lon = f"{c.get('longitude'):.4f}" if c.get('longitude') is not None else "NULL"
    print(f"{c.get('id'):<4} | {c.get('camera_code'):<10} | {c.get('name'):<24} | {c.get('status'):<9} | {lat:<8} | {lon:<8} | {c.get('stream_url')}")

# Verify no credentials in stream URLs
for c in cameras:
    surl = c.get("stream_url") or ""
    assert "@" not in surl, f"Credential leak detected in {c.get('camera_code')}: {surl}"
print("\nCredential check passed: ZERO credentials present in any stream_url.")

# Verify no duplicate camera_codes
codes = [c.get("camera_code") for c in cameras]
assert len(codes) == len(set(codes)), "Duplicate camera_code detected!"
print("Uniqueness check passed: No duplicate camera codes.")
print("All 8 government cameras present:", all(f"CAM-00{i}" in codes for i in range(1, 9)))
