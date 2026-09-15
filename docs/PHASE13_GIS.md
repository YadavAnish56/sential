# Phase 13 — GIS / Mapping

Operator-facing map showing where cameras are and how a vehicle moved between
them. Built as a proof of concept on the data Sentinel already records.

## What this phase adds

- A camera map with one marker per geolocated camera, colour-coded by status.
- A vehicle movement path: the ordered sequence of cameras that detected a
  selected plate, drawn as a numbered polyline.
- Two-way interaction between the map and the vehicle timeline.

## What it deliberately does not add

**No database changes.** No new tables, no new columns, no migration, no PostGIS,
no geometry/geography types.

The reason is that the data already existed:

- `cameras.latitude` / `cameras.longitude` have been in the schema since the
  initial migration (`8e1deeb6a900`) — they were simply never read by the UI.
- An event has no position of its own. Its position **is** the position of the
  camera that recorded it, reached through `events.camera_id`.
- Cross-camera movement needs no adjacency table: the order comes from
  `events.timestamp`, which the timeline endpoint already sorts by.

PostGIS would become justified only for work this phase does not do — geofence
polygons, radius search ("cameras within 2 km"), route snapping, or spatial
aggregation.

## API change

One additive change, to `GET /api/vehicles/{plate_number}/timeline`.

Each entry of `timeline` gains two nullable fields:

```jsonc
{
  "event_id": 11,
  "camera_id": 101,
  "camera_code": "CAM-A",
  "camera_name": "Adajan Gate",
  "location": "Adajan",
  "latitude": 21.1959,     // new — inherited from the recording camera
  "longitude": 72.7933,    // new — inherited from the recording camera
  "event_type": "anpr_detection",
  "confidence": 0.95,
  "timestamp": "2026-09-10T09:00:00",
  "snapshot_path": null
}
```

No field was removed or changed, no new endpoint was added, and the SQL is
unchanged — the camera row was already joined, so the coordinates cost nothing.

A camera without survey coordinates yields `null`, never `0`. `(0, 0)` is a real
location in the Gulf of Guinea, and coercing missing data to it would draw a
sighting that never happened.

## Data flow

```
PostgreSQL          cameras(latitude, longitude)   events(camera_id, vehicle_id, timestamp)
                                    |
FastAPI             GET /api/cameras          GET /api/vehicles/{plate}/timeline
                    CameraResponse            Event JOIN Camera ORDER BY timestamp ASC
                                    |
React               CameraMap                 VehicleTimeline
                    (fetches cameras)         (fetches timeline, shares it upward)
                                    |
                            App holds selectedPlate / timeline / selectedEventId
                                    |
Leaflet             camera markers  +  VehicleMovementLayer (polyline + numbered stops)
```

The timeline is fetched **once** per plate selection. `VehicleTimeline` reports
the loaded entries to `App` through `onTimelineLoaded`, and `App` passes them to
the map — the map never issues a second request for the same data.

## Files

| File | Role |
|---|---|
| `frontend/src/components/CameraMap.jsx` | Map, tiles, camera markers, viewport control |
| `frontend/src/components/VehicleMovementLayer.jsx` | Polyline and numbered stops |
| `frontend/src/utils/geo.js` | Coordinate validation and point extraction |
| `frontend/src/App.jsx` | Shared `selectedPlate` / `timeline` / `selectedEventId` state |
| `frontend/src/components/VehicleTimeline.jsx` | Publishes its fetch upward; entries are selectable |
| `backend/app/schemas/vehicle.py` | `latitude` / `longitude` on `VehicleTimelineEntry` |
| `backend/app/routes/vehicles.py` | Populates them from the joined camera |

## Populating camera coordinates

Only cameras with **both** coordinates appear on the map. Use the existing
camera API — no new endpoint is needed:

```bash
curl -X PATCH http://localhost:8000/api/cameras/1 \
  -H "Content-Type: application/json" \
  -d '{"latitude": 21.1702, "longitude": 72.8311}'
```

`backend/scripts/seed_camera.py` already seeds `CAM-001` at Surat
(21.1702, 72.8311). Until at least one camera has coordinates, the map renders
centred on Gujarat with an explicit "No camera coordinates available to plot."
message rather than an empty grey square.

## Known limitations of this PoC

- **Tile dependency.** Tiles come from the public OpenStreetMap servers, which
  require internet access and are subject to OSM's tile usage policy. An
  air-gapped deployment needs a self-hosted tile source.
- **Detection order, not a route.** Event timestamps are recorded at persistence
  time (`datetime.utcnow()`), not derived from stream PTS — the ANPR pipeline is
  explicit that `pts_ms` is stream timing, not wall-clock. Under lag or clock
  skew the ordering can misstate true travel order. The straight segments between
  stops are visual links, not roads travelled.
- **Unpaginated timeline.** `/timeline` returns every detection for a plate. A
  very busy plate will produce a long polyline; add a time window or limit before
  using this at scale.
- **No authentication.** The camera and vehicle endpoints are unauthenticated, as
  they were before this phase. Camera coordinates and vehicle movement histories
  are sensitive; access control should be settled before any real deployment.
- **Coordinates are not validated on write.** The API still accepts any float, as
  it always has. Invalid values are filtered out at render time instead, so
  existing write behaviour is unchanged.

## Running the tests

Backend (SQLite is used for tests, so no PostgreSQL is required):

```bash
DATABASE_URL="sqlite:///:memory:" python -m pytest backend/tests -q
```

Frontend:

```bash
cd frontend && npm test
```

GIS coverage lives in `backend/tests/test_gis_endpoints.py`,
`frontend/src/utils/geo.test.js`, `frontend/src/components/CameraMap.test.jsx`,
and `frontend/src/components/MapIntegration.test.jsx`.
