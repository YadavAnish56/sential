import React, { useState, useEffect } from 'react';
import { analyticsService } from '../api/analyticsService';

export default function VehicleTimeline({ plateNumber, onClose }) {
  const [timeline, setTimeline] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

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
          setTimeline(data.timeline || []);
        }
      } catch (err) {
        if (active) {
          setError(err.message || 'Failed to fetch timeline.');
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

  return (
    <div className="vehicle-timeline" style={{ background: '#1f1f1f', padding: '1rem', borderRadius: '8px', position: 'relative' }}>
      <button 
        onClick={onClose} 
        style={{ position: 'absolute', top: '1rem', right: '1rem', background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '1.2rem' }}
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
        <div className="timeline-entries" style={{ display: 'flex', flexDirection: 'column', gap: '1rem', maxHeight: '600px', overflowY: 'auto' }}>
          {timeline.map((entry) => (
            <div key={entry.event_id} style={{ padding: '1rem', background: '#2c2c2c', borderRadius: '4px', borderLeft: '4px solid #1890ff' }}>
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
      )}
    </div>
  );
}
