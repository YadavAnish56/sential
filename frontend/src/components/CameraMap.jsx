import React, { useEffect, useMemo, useState } from 'react';
import { MapContainer, Marker, Popup, TileLayer, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { cameraService } from '../api/cameraService';
import VehicleMovementLayer from './VehicleMovementLayer';
import {
  DEFAULT_CENTER,
  DEFAULT_ZOOM,
  toCameraPoints,
  toLatLngTuples,
  toMovementPoints,
} from '../utils/geo';

const STATUS_COLORS = {
  active: '#52c41a',
  online: '#52c41a',
  running: '#52c41a',
  offline: '#8c8c8c',
  error: '#ff4d4f',
};

function cameraIcon(status) {
  const color = STATUS_COLORS[String(status || '').toLowerCase()] || '#8c8c8c';
  return L.divIcon({
    className: 'sentinel-camera-wrapper',
    html: `<span class="sentinel-camera-pin" style="background:${color}"></span>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

/**
 * Keeps the viewport in step with the data.
 *
 * Leaflet needs a laid-out container to measure against; under jsdom it has
 * none, so view changes are attempted defensively and skipped when impossible.
 */
function MapViewController({ bounds, focus }) {
  const map = useMap();
  const focusLat = focus ? focus[0] : null;
  const focusLng = focus ? focus[1] : null;
  const boundsKey = JSON.stringify(bounds);

  useEffect(() => {
    if (focusLat === null || focusLng === null) return;
    try {
      map.setView([focusLat, focusLng], Math.max(map.getZoom(), 14));
    } catch {
      /* container not measurable — leave the current view in place */
    }
  }, [map, focusLat, focusLng]);

  useEffect(() => {
    if (focusLat !== null && focusLng !== null) return;
    if (!bounds || bounds.length === 0) return;
    try {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
    } catch {
      /* container not measurable — leave the current view in place */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, boundsKey, focusLat, focusLng]);

  return null;
}

/**
 * Operator map: every geolocated camera, plus the movement path of the
 * currently selected vehicle.
 *
 * Cameras without survey coordinates are omitted rather than guessed at.
 */
export default function CameraMap({
  plateNumber,
  timeline,
  selectedEventId,
  onSelectEvent,
}) {
  const [cameras, setCameras] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;

    const fetchCameras = async () => {
      try {
        const data = await cameraService.getCameras();
        if (!active) return;
        setCameras(data?.cameras || []);
        setError(null);
      } catch (err) {
        if (active) setError(err.message || 'Failed to load cameras.');
      } finally {
        if (active) setLoading(false);
      }
    };

    fetchCameras();

    return () => {
      active = false;
    };
  }, []);

  const cameraPoints = useMemo(() => toCameraPoints(cameras), [cameras]);
  const movementPoints = useMemo(() => toMovementPoints(timeline), [timeline]);

  const bounds = useMemo(() => {
    const source = movementPoints.length > 0 ? movementPoints : cameraPoints;
    return toLatLngTuples(source);
  }, [movementPoints, cameraPoints]);

  const focus = useMemo(() => {
    if (!selectedEventId) return null;
    const entry = movementPoints.find((point) => point.event_id === selectedEventId);
    return entry ? [entry.latitude, entry.longitude] : null;
  }, [movementPoints, selectedEventId]);

  const plottedSummary = `${cameraPoints.length} of ${cameras.length} cameras mapped`;
  const pathSummary =
    plateNumber && movementPoints.length > 0
      ? `${plateNumber}: ${movementPoints.length} stop${movementPoints.length === 1 ? '' : 's'}`
      : null;

  const showEmptyOverlay =
    !loading && !error && cameraPoints.length === 0 && movementPoints.length === 0;

  return (
    <div className="camera-map-panel">
      <div className="camera-map-header">
        <h2>Camera Map</h2>
        <span className="camera-map-meta">
          {plottedSummary}
          {pathSummary ? ` — ${pathSummary}` : ''}
        </span>
      </div>

      {loading && <div className="camera-map-status">Loading map...</div>}

      {error && !loading && (
        <div className="camera-map-error">Map error: {error}</div>
      )}

      {plateNumber && !loading && movementPoints.length === 0 && (
        <div className="camera-map-status">
          No mapped detections for this vehicle.
        </div>
      )}

      <div className="camera-map">
        <MapContainer
          center={DEFAULT_CENTER}
          zoom={DEFAULT_ZOOM}
          scrollWheelZoom
          className="camera-map-canvas"
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          <MapViewController bounds={bounds} focus={focus} />

          {cameraPoints.map((camera) => (
            <Marker
              key={camera.id}
              position={[camera.latitude, camera.longitude]}
              icon={cameraIcon(camera.status)}
            >
              <Popup>
                <div className="sentinel-popup">
                  <strong>{camera.name || 'Unnamed Camera'}</strong>
                  <div>{`Code: ${camera.camera_code}`}</div>
                  {camera.location && <div>{`Location: ${camera.location}`}</div>}
                  <div>{`Status: ${camera.status || 'unknown'}`}</div>
                </div>
              </Popup>
            </Marker>
          ))}

          <VehicleMovementLayer
            timeline={timeline}
            selectedEventId={selectedEventId}
            onSelectEvent={onSelectEvent}
          />
        </MapContainer>

        {showEmptyOverlay && (
          <div className="camera-map-empty">
            No camera coordinates available to plot.
          </div>
        )}
      </div>
    </div>
  );
}
