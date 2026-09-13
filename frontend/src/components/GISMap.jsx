import React, { useState, useEffect, useRef } from 'react';
import * as ReactLeaflet from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { cameraService } from '../api/cameraService';

const { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } = ReactLeaflet;

// Fix Leaflet default icon path in bundlers
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

// Dynamic bounds controller
function MapBounds({ markers, routePoints, selectedCamera }) {
  const map = useMap();
  useEffect(() => {
    try {
      map.invalidateSize();
    } catch (_) {}
    const timer = setTimeout(() => {
      try {
        map.invalidateSize();
      } catch (_) {}
    }, 200);

    if (routePoints && routePoints.length > 0) {
      const bounds = L.latLngBounds(routePoints.map((p) => [p.latitude, p.longitude]));
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
    } else if (selectedCamera && selectedCamera.latitude != null && selectedCamera.longitude != null) {
      map.setView([selectedCamera.latitude, selectedCamera.longitude], Math.max(map.getZoom(), 14));
    } else if (markers && markers.length > 0) {
      const bounds = L.latLngBounds(markers.map((m) => [m.latitude, m.longitude]));
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
    }

    return () => clearTimeout(timer);
  }, [markers, routePoints, selectedCamera, map]);
  return null;
}

// Tactical CCTV node marker icon
function getCameraMarkerIcon(isSelected, isOnline, code) {
  const bg = isSelected ? '#2563EB' : isOnline ? '#15803D' : '#64748B';
  const border = '#FFFFFF';
  const shadow = isSelected ? '0 2px 6px rgba(37, 99, 235, 0.4)' : '0 1px 3px rgba(0,0,0,0.2)';
  const label = code ? code.replace(/[^0-9]/g, '').slice(-2) || 'C' : 'C';

  if (typeof L.divIcon === 'function') {
    return L.divIcon({
      className: `tactical-camera-marker ${isSelected ? 'selected' : ''}`,
      html: `<div style="background:${bg};border:2px solid ${border};box-shadow:${shadow};width:${isSelected ? 24 : 20}px;height:${isSelected ? 24 : 20}px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#FFFFFF;font-size:${isSelected ? 10 : 9}px;font-weight:700;font-family:monospace;">${label}</div>`,
      iconSize: isSelected ? [24, 24] : [20, 20],
      iconAnchor: isSelected ? [12, 12] : [10, 10],
      popupAnchor: [0, -10],
    });
  }
  return undefined;
}

// Numbered investigation route checkpoint marker icon
function getCheckpointMarkerIcon(index) {
  if (typeof L.divIcon === 'function') {
    return L.divIcon({
      className: 'investigation-checkpoint-marker',
      html: `<div style="background:#2563EB;border:2px solid #FFFFFF;box-shadow:0 2px 5px rgba(0,0,0,0.25);width:22px;height:22px;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#FFFFFF;font-size:10px;font-weight:800;font-family:sans-serif;">${index}</div>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
      popupAnchor: [0, -11],
    });
  }
  return undefined;
}

const SafePolyline = Polyline || (({ children }) => null);

export default function GISMap({
  selectedCameraId = null,
  onSelectCamera = null,
  investigationPath = null,
  height = '100%',
}) {
  const [cameras, setCameras] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const camerasRef = useRef([]);

  useEffect(() => {
    let active = true;
    let retryTimer = null;
    let retryCount = 0;
    const MAX_RETRIES = 10;

    const fetchCameras = async (isRetry = false) => {
      if (!isRetry && camerasRef.current.length === 0) {
        setLoading(true);
        setError(null);
      }
      try {
        const data = await cameraService.getCamerasMap(true);
        if (active) {
          const safeCameras = (data || []).filter(
            (c) =>
              c.latitude !== null &&
              c.longitude !== null &&
              c.latitude >= -90 &&
              c.latitude <= 90 &&
              c.longitude >= -180 &&
              c.longitude <= 180
          );
          camerasRef.current = safeCameras;
          setCameras(safeCameras);
          setError(null); // Clear error automatically after successful recovery
          setLoading(false);
          retryCount = 0;
        }
      } catch (err) {
        if (active) {
          // Never wipe previously loaded camera/map data
          if (camerasRef.current.length === 0) {
            setLoading(false);
            if (retryCount < MAX_RETRIES) {
              setError(err.message || 'Failed to load map data.');
              retryCount += 1;
              const delay = Math.min(retryCount * 1000, 5000);
              retryTimer = setTimeout(() => {
                if (active) fetchCameras(true);
              }, delay);
            } else {
              setError('Map service temporarily unavailable. Backend unreachable after multiple attempts.');
            }
          } else {
            setLoading(false);
          }
        }
      }
    };

    fetchCameras(false);

    return () => {
      active = false;
      if (retryTimer) {
        clearTimeout(retryTimer);
      }
    };
  }, []);

  if (loading) {
    return (
      <div className="gis-loading" aria-busy="true" style={{ padding: '2rem', textAlign: 'center', color: '#52657A' }}>
        Loading GIS Map...
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="gis-error"
        role="alert"
        style={{ padding: '1.5rem', background: '#FEF2F2', border: '1px solid #FECACA', color: '#DC2626', borderRadius: '6px' }}
      >
        Error: {error}
      </div>
    );
  }

  if (cameras.length === 0) {
    return (
      <div className="gis-empty" style={{ padding: '3rem', textAlign: 'center', color: '#52657A' }}>
        <h3 style={{ color: '#172B4D', marginBottom: '0.5rem' }}>No Mapped Cameras</h3>
        <p>No mapped camera coordinates are available.</p>
      </div>
    );
  }

  const selectedCamera = cameras.find((c) => c.id === selectedCameraId || c.camera_code === selectedCameraId);
  const defaultCenter = selectedCamera
    ? [selectedCamera.latitude, selectedCamera.longitude]
    : [cameras[0].latitude, cameras[0].longitude];

  // Robust investigationPath normalization: accepts [lat, lon] or { latitude, longitude, ... }
  const normalizedRoutePoints = (investigationPath || [])
    .map((p, idx) => {
      if (Array.isArray(p) && p.length >= 2) {
        const lat = typeof p[0] === 'number' ? p[0] : parseFloat(p[0]);
        const lon = typeof p[1] === 'number' ? p[1] : parseFloat(p[1]);
        return {
          latitude: lat,
          longitude: lon,
          index: idx + 1,
          camera_id: null,
          camera_code: null,
          camera_name: null,
          timestamp: null,
          confidence: null,
          vehicle_type: null,
        };
      }
      if (p && typeof p === 'object') {
        const rawLat = p.latitude ?? p.lat;
        const rawLon = p.longitude ?? p.lon ?? p.lng;
        const lat = typeof rawLat === 'number' ? rawLat : parseFloat(rawLat);
        const lon = typeof rawLon === 'number' ? rawLon : parseFloat(rawLon);
        return {
          ...p,
          latitude: lat,
          longitude: lon,
          index: p.index ?? idx + 1,
          camera_id: p.camera_id ?? null,
          camera_code: p.camera_code ?? null,
          camera_name: p.camera_name ?? null,
          timestamp: p.timestamp ?? null,
          confidence: p.confidence ?? null,
          vehicle_type: p.vehicle_type ?? null,
        };
      }
      return null;
    })
    .filter(
      (p) =>
        p &&
        p.latitude != null &&
        p.longitude != null &&
        !isNaN(p.latitude) &&
        !isNaN(p.longitude) &&
        p.latitude >= -90 &&
        p.latitude <= 90 &&
        p.longitude >= -180 &&
        p.longitude <= 180
    );

  const routeCoords = normalizedRoutePoints.map((p) => [p.latitude, p.longitude]);

  return (
    <div className="gis-map-container" style={{ height, width: '100%', position: 'relative' }}>
      <MapContainer
        center={defaultCenter}
        zoom={13}
        style={{ height: '100%', width: '100%', borderRadius: '6px', zIndex: 1, background: '#EAF0F6' }}
      >
        <TileLayer
          attribution='Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ'
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"
          maxZoom={16}
        />
        <TileLayer
          attribution='Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ'
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
          maxZoom={16}
        />
        <MapBounds markers={cameras} routePoints={normalizedRoutePoints} selectedCamera={selectedCamera} />

        {/* Restrained Investigation Route Line */}
        {routeCoords.length > 1 && (
          <SafePolyline
            positions={routeCoords}
            color="#2563EB"
            weight={3}
            opacity={0.8}
            dashArray="5, 6"
          />
        )}

        {/* Investigation Checkpoint Waypoints */}
        {normalizedRoutePoints.map((pt) => {
          const cpIcon = getCheckpointMarkerIcon(pt.index);
          const hasSightingData = pt.camera_code || pt.camera_name || pt.timestamp;

          return (
            <Marker
              key={`checkpoint-${pt.index}-${pt.latitude}-${pt.longitude}`}
              position={[pt.latitude, pt.longitude]}
              {...(cpIcon ? { icon: cpIcon } : {})}
              data-testid={`checkpoint-marker-${pt.index}`}
            >
              <Popup>
                <div className="checkpoint-popup" style={{ color: '#172B4D', minWidth: '180px', fontSize: '0.85rem' }}>
                  <div style={{ fontWeight: '700', borderBottom: '1px solid #D5DEE8', paddingBottom: '0.25rem', marginBottom: '0.4rem' }}>
                    CHECKPOINT #{pt.index}
                  </div>
                  {pt.camera_code && (
                    <div style={{ marginBottom: '0.2rem' }}>
                      <strong>CAMERA:</strong> {pt.camera_code} {pt.camera_name ? `(${pt.camera_name})` : ''}
                    </div>
                  )}
                  {pt.timestamp && (
                    <div style={{ marginBottom: '0.2rem', color: '#52657A' }}>
                      <strong>TIME:</strong> {new Date(pt.timestamp).toLocaleString()}
                    </div>
                  )}
                  {pt.confidence != null && (
                    <div style={{ marginBottom: '0.2rem' }}>
                      <strong>CONFIDENCE:</strong> {(pt.confidence * 100).toFixed(1)}%
                    </div>
                  )}
                  {pt.vehicle_type && (
                    <div style={{ marginBottom: '0.4rem' }}>
                      <strong>VEHICLE TYPE:</strong> {pt.vehicle_type.toUpperCase()}
                    </div>
                  )}
                  {pt.camera_id && onSelectCamera && (
                    <button
                      type="button"
                      className="btn btn-xs btn-primary"
                      style={{ width: '100%', marginTop: '0.3rem' }}
                      onClick={() => onSelectCamera(pt.camera_id)}
                    >
                      Focus Camera
                    </button>
                  )}
                </div>
              </Popup>
            </Marker>
          );
        })}

        {/* Camera Markers */}
        {cameras.map((cam) => {
          const isSelected = cam.id === selectedCameraId || cam.camera_code === selectedCameraId;
          const isOnline = cam.status === 'online';
          const markerIcon = getCameraMarkerIcon(isSelected, isOnline, cam.camera_code);

          return (
            <Marker
              key={cam.id}
              position={[cam.latitude, cam.longitude]}
              {...(markerIcon ? { icon: markerIcon } : {})}
              eventHandlers={{
                click: () => {
                  if (onSelectCamera) onSelectCamera(cam.id);
                },
              }}
            >
              <Popup>
                <div className="camera-popup" style={{ color: '#172B4D', minWidth: '160px', fontSize: '0.85rem' }}>
                  <div style={{ fontWeight: '700', marginBottom: '0.25rem' }}>
                    {cam.name} ({cam.camera_code})
                  </div>
                  {cam.location && (
                    <div style={{ fontSize: '0.8rem', color: '#52657A', marginBottom: '0.25rem' }}>
                      Location: {cam.location}
                    </div>
                  )}
                  <div style={{ fontSize: '0.8rem', marginBottom: '0.5rem' }}>
                    Status:{' '}
                    <span
                      style={{
                        textTransform: 'uppercase',
                        fontWeight: '700',
                        color: isOnline ? '#15803D' : '#DC2626',
                      }}
                    >
                      {cam.status}
                    </span>
                  </div>
                  {onSelectCamera && (
                    <button
                      type="button"
                      onClick={() => onSelectCamera(cam.id)}
                      className="btn btn-xs btn-primary"
                      style={{ width: '100%' }}
                    >
                      {isSelected ? '✓ Active Focus' : 'Focus Camera'}
                    </button>
                  )}
                </div>
              </Popup>
            </Marker>
          );
        })}
      </MapContainer>
    </div>
  );
}
