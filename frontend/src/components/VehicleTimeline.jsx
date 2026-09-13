import React, { useState, useEffect, useRef } from 'react';
import { analyticsService } from '../api/analyticsService';
import { MapContainer, TileLayer, Marker, Polyline, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';

function RouteMapBounds({ points }) {
  const map = useMap();
  useEffect(() => {
    if (points && points.length > 0) {
      const bounds = L.latLngBounds(points);
      map.fitBounds(bounds, { padding: [20, 20], maxZoom: 16 });
    }
  }, [points, map]);
  return null;
}

export default function VehicleTimeline({
  plateNumber,
  onClose,
  onTimelineLoaded,
  selectedEventId,
  onSelectEvent,
}) {
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Held in a ref so the map can share this fetch without the callback
  // identity re-triggering it — the timeline is still fetched once per plate.
  const onTimelineLoadedRef = useRef(onTimelineLoaded);

  useEffect(() => {
    onTimelineLoadedRef.current = onTimelineLoaded;
  }, [onTimelineLoaded]);

  const publishTimeline = (entries) => {
    if (onTimelineLoadedRef.current) {
      onTimelineLoadedRef.current(entries);
    }
  };

  useEffect(() => {
    if (!plateNumber) return;

    let active = true;
    const fetchTimeline = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await analyticsService.getVehicleTimeline(plateNumber);
        if (active) {
          // The backend guarantees chronological order (asc) based on the actual API contract
          const entries = data.timeline || [];
          setTimeline(entries);
          publishTimeline(entries);
        }
      } catch (err) {
        if (active) {
          setError(err.message || 'Failed to fetch timeline.');
          setTimeline([]);
          publishTimeline([]);
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    };

    fetchTimeline();

    return () => {
      active = false;
    };
  }, [plateNumber]);

  if (!plateNumber) {
    return (
      <div className="vehicle-timeline" style={{ background: '#1f1f1f', padding: '1rem', borderRadius: '8px' }}>
        <h2 style={{ marginTop: 0, marginBottom: '1rem', borderBottom: '1px solid #333', paddingBottom: '0.5rem' }}>Vehicle Timeline</h2>
        <div style={{ color: '#888' }}>Select a plate from the event feed to view its cross-camera timeline.</div>
      </div>
    );
  }

  const validEntries = timeline.filter(entry => 
    entry.latitude !== null && entry.latitude !== undefined &&
    entry.longitude !== null && entry.longitude !== undefined &&
    entry.latitude >= -90 && entry.latitude <= 90 &&
    entry.longitude >= -180 && entry.longitude <= 180
  );
  
  const mapPoints = validEntries.map(entry => [entry.latitude, entry.longitude]);

  return (
    <div className="vehicle-timeline" style={{ background: '#1f1f1f', padding: '1rem', borderRadius: '8px', position: 'relative' }}>
      <button 
        onClick={onClose} 
        style={{ position: 'absolute', top: '1rem', right: '1rem', background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '1.2rem', zIndex: 1000 }}
        aria-label="Close timeline"
      >
        &times;
      </button>
      
      <h2 style={{ marginTop: 0, marginBottom: '1rem', borderBottom: '1px solid #333', paddingBottom: '0.5rem' }}>Timeline: {plateNumber}</h2>
      
      {loading && <div className="timeline-loading">Loading timeline...</div>}
      
      {error && !loading && (
        <div className="timeline-error" style={{ color: '#ff4d4f', padding: '1rem', background: '#2b0000' }}>
          Error: {error}
        </div>
      )}
      
      {!loading && !error && timeline.length === 0 && (
        <div className="timeline-empty" style={{ padding: '1rem' }}>
          No timeline entries found for this vehicle.
        </div>
      )}

      {!loading && !error && timeline.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {/* Map Section */}
          {mapPoints.length > 0 && (
            <div className="timeline-map" style={{ height: '300px', width: '100%', borderRadius: '8px', overflow: 'hidden' }}>
              <MapContainer 
                center={mapPoints[0]} 
                zoom={13} 
                style={{ height: '100%', width: '100%', zIndex: 1 }}
              >
                <TileLayer
                  attribution='&amp;copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                <RouteMapBounds points={mapPoints} />
                <Polyline positions={mapPoints} color="#1890ff" weight={4} opacity={0.7} />
                {validEntries.map((entry, index) => (
                  <Marker key={`marker-${entry.event_id}-${index}`} position={[entry.latitude, entry.longitude]}>
                    <Popup>
                      <div style={{ fontSize: '0.85rem' }}>
                        <strong>{entry.camera_name || entry.camera_code}</strong><br/>
                        {new Date(entry.timestamp).toLocaleString()}<br/>
                        Event: {entry.event_type}
                      </div>
                    </Popup>
                  </Marker>
                ))}
              </MapContainer>
            </div>
          )}

          {/* Entries Section */}
          <div className="timeline-entries" style={{ display: 'flex', flexDirection: 'column', gap: '1rem', maxHeight: '400px', overflowY: 'auto' }}>
            {timeline.map((entry) => (
              <div
                key={entry.event_id}
                onClick={() => onSelectEvent && onSelectEvent(entry.event_id)}
                style={{
                  padding: '1rem',
                  background: entry.event_id === selectedEventId ? '#123a5c' : '#2c2c2c',
                  borderRadius: '4px',
                  borderLeft: `4px solid ${entry.event_id === selectedEventId ? '#40a9ff' : '#1890ff'}`,
                  cursor: onSelectEvent ? 'pointer' : 'default',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', color: '#aaa', fontSize: '0.85rem' }}>
                  <span>{new Date(entry.timestamp).toLocaleString()}</span>
                  <span>{entry.camera_name || entry.camera_code}</span>
                </div>
                <div style={{ fontWeight: 'bold' }}>
                  {entry.event_type}
                  {entry.confidence && <span style={{ marginLeft: '0.5rem', fontWeight: 'normal', color: '#aaa' }}>({(entry.confidence * 100).toFixed(0)}%)</span>}
                </div>
                {entry.location && <div style={{ fontSize: '0.85rem', color: '#888', marginTop: '0.25rem' }}>Location: {entry.location}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
