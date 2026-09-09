import React, { useState, useEffect, useRef } from 'react';
import { alertService } from '../api/alertService';

export default function AlertsInbox() {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [acknowledgingIds, setAcknowledgingIds] = useState(new Set());
  
  const isMounted = useRef(true);

  const fetchAlerts = async () => {
    try {
      const data = await alertService.getAlerts();
      if (!isMounted.current) return;
      
      // Prioritize 'new' alerts by sorting them to the top, then by timestamp desc
      const sortedAlerts = (data.alerts || []).sort((a, b) => {
        if (a.status === 'new' && b.status !== 'new') return -1;
        if (a.status !== 'new' && b.status === 'new') return 1;
        return new Date(b.timestamp) - new Date(a.timestamp);
      });
      
      setAlerts(sortedAlerts);
      setError(null);
    } catch (err) {
      if (!isMounted.current) return;
      setError('Failed to load alerts.');
    } finally {
      if (isMounted.current) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    isMounted.current = true;
    fetchAlerts();

    const intervalId = setInterval(() => {
      fetchAlerts();
    }, 5000);

    return () => {
      isMounted.current = false;
      clearInterval(intervalId);
    };
  }, []);

  const handleAcknowledge = async (alertId) => {
    if (acknowledgingIds.has(alertId)) return;

    setAcknowledgingIds(prev => new Set(prev).add(alertId));

    try {
      const updatedAlert = await alertService.acknowledgeAlert(alertId);
      
      setAlerts(prevAlerts => {
        const newAlerts = prevAlerts.map(a => 
          a.id === alertId ? updatedAlert : a
        );
        // Re-sort so acknowledged alerts drop down
        return newAlerts.sort((a, b) => {
          if (a.status === 'new' && b.status !== 'new') return -1;
          if (a.status !== 'new' && b.status === 'new') return 1;
          return new Date(b.timestamp) - new Date(a.timestamp);
        });
      });
    } catch (err) {
      alert('Failed to acknowledge alert. Please try again.');
    } finally {
      setAcknowledgingIds(prev => {
        const next = new Set(prev);
        next.delete(alertId);
        return next;
      });
    }
  };

  const getSeverityStyle = (severity) => {
    switch (severity?.toLowerCase()) {
      case 'critical': return { background: '#ff4d4f', color: '#fff' };
      case 'high': return { background: '#faad14', color: '#fff' };
      case 'medium': return { background: '#1890ff', color: '#fff' };
      case 'low': return { background: '#52c41a', color: '#fff' };
      default: return { background: '#888', color: '#fff' };
    }
  };

  const getStatusStyle = (status) => {
    switch (status?.toLowerCase()) {
      case 'new': return { border: '1px solid #ff4d4f', color: '#ff4d4f' };
      case 'acknowledged': return { border: '1px solid #faad14', color: '#faad14' };
      case 'resolved': return { border: '1px solid #52c41a', color: '#52c41a' };
      default: return { border: '1px solid #888', color: '#888' };
    }
  };

  return (
    <div className="alerts-inbox" style={{ background: '#1f1f1f', padding: '1rem', borderRadius: '8px' }}>
      <h2 style={{ marginTop: 0, marginBottom: '1rem', borderBottom: '1px solid #333', paddingBottom: '0.5rem' }}>Security Alerts</h2>
      
      {loading && alerts.length === 0 && (
        <div style={{ color: '#888' }}>Loading alerts...</div>
      )}

      {error && !loading && alerts.length === 0 && (
        <div style={{ color: '#ff4d4f' }}>Error: {error}</div>
      )}

      {!loading && !error && alerts.length === 0 && (
        <div style={{ color: '#52c41a' }}>No active alerts. System is secure.</div>
      )}

      {alerts.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: 0, maxHeight: '400px', overflowY: 'auto' }}>
          {alerts.map(alert => (
            <li key={alert.id} style={{ 
              padding: '1rem', 
              background: alert.status === 'new' ? '#2c1e1e' : '#2c2c2c', 
              marginBottom: '0.5rem', 
              borderRadius: '4px',
              borderLeft: alert.status === 'new' ? '4px solid #ff4d4f' : '4px solid #888'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                <div style={{ fontWeight: 'bold', fontSize: '1.1rem' }}>{alert.alert_type}</div>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <span style={{ padding: '0.2rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 'bold', ...getSeverityStyle(alert.severity) }}>
                    {alert.severity?.toUpperCase()}
                  </span>
                  <span style={{ padding: '0.2rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 'bold', ...getStatusStyle(alert.status) }}>
                    {alert.status?.toUpperCase()}
                  </span>
                </div>
              </div>
              
              {alert.message && (
                <div style={{ marginBottom: '0.5rem', color: '#eee' }}>{alert.message}</div>
              )}
              
              <div style={{ fontSize: '0.85rem', color: '#aaa', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  {new Date(alert.timestamp).toLocaleString()} 
                  {alert.vehicle_id && ` • Vehicle ID: ${alert.vehicle_id}`}
                </div>
                
                {alert.status === 'new' && (
                  <button 
                    className="btn btn-primary"
                    onClick={() => handleAcknowledge(alert.id)}
                    disabled={acknowledgingIds.has(alert.id)}
                    style={{ padding: '0.3rem 0.8rem', fontSize: '0.85rem' }}
                  >
                    {acknowledgingIds.has(alert.id) ? 'Acknowledging...' : 'Acknowledge'}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
