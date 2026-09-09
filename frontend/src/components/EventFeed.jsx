import React, { useState, useEffect, useRef } from 'react';
import { analyticsService } from '../api/analyticsService';
import { cameraService } from '../api/cameraService';

export default function EventFeed({ onPlateSelect }) {
  const [events, setEvents] = useState([]);
  const [vehicles, setVehicles] = useState({});
  const [cameras, setCameras] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchInProgress = useRef(false);

  const fetchMetadata = async () => {
    try {
      const [camerasData, vehiclesData] = await Promise.all([
        cameraService.getCameras(),
        analyticsService.getVehicles(500)
      ]);

      const cameraMap = {};
      (camerasData.cameras || []).forEach(cam => {
        cameraMap[cam.id] = cam.name || cam.camera_code;
      });
      setCameras(cameraMap);

      const vehicleMap = {};
      (vehiclesData || []).forEach(v => {
        vehicleMap[v.id] = v.plate_number;
      });
      setVehicles(vehicleMap);
    } catch (err) {
      console.error("Failed to load metadata for events", err);
    }
  };

  const fetchEvents = async () => {
    if (fetchInProgress.current) return;
    fetchInProgress.current = true;
    try {
      const data = await analyticsService.getRecentEvents(20);
      setEvents(data.events || []);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to load events.');
    } finally {
      fetchInProgress.current = false;
    }
  };

  useEffect(() => {
    const init = async () => {
      setLoading(true);
      await fetchMetadata();
      await fetchEvents();
      setLoading(false);
    };
    init();

    const intervalId = setInterval(() => {
      fetchEvents();
      // Optionally periodically fetch vehicles if new ones appear
    }, 5000);

    return () => clearInterval(intervalId);
  }, []);

  if (loading) {
    return <div className="event-feed-loading">Loading recent events...</div>;
  }

  if (error) {
    return <div className="event-feed-error" style={{ color: '#ff4d4f', padding: '1rem', background: '#2b0000' }}>Error: {error}</div>;
  }

  if (events.length === 0) {
    return <div className="event-feed-empty" style={{ padding: '1rem' }}>No recent events found.</div>;
  }

  return (
    <div className="event-feed" style={{ background: '#1f1f1f', padding: '1rem', borderRadius: '8px' }}>
      <h2 style={{ marginTop: 0, marginBottom: '1rem', borderBottom: '1px solid #333', paddingBottom: '0.5rem' }}>Recent Detections</h2>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, maxHeight: '600px', overflowY: 'auto' }}>
        {events.map((evt) => {
          const cameraName = cameras[evt.camera_id] || `Camera ${evt.camera_id}`;
          const plateNumber = evt.vehicle_id ? vehicles[evt.vehicle_id] : null;
          
          return (
            <li key={evt.id} style={{ padding: '0.75rem', borderBottom: '1px solid #333', marginBottom: '0.5rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', color: '#888' }}>
                <span>{new Date(evt.timestamp).toLocaleString()}</span>
                <span>{cameraName}</span>
              </div>
              <div style={{ marginTop: '0.5rem', fontWeight: 'bold' }}>
                {evt.event_type} {evt.object_type ? `- ${evt.object_type}` : ''} 
                {evt.confidence && <span style={{ marginLeft: '0.5rem', fontWeight: 'normal', color: '#aaa' }}>({(evt.confidence * 100).toFixed(0)}%)</span>}
              </div>
              {plateNumber && (
                <div style={{ marginTop: '0.5rem' }}>
                  <button 
                    className="btn btn-secondary" 
                    style={{ padding: '0.2rem 0.5rem', fontSize: '0.8rem' }}
                    onClick={() => onPlateSelect(plateNumber)}
                  >
                    Plate: {plateNumber}
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
