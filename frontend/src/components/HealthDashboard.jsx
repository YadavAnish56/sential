import React, { useState, useEffect, useRef } from 'react';
import { cameraService } from '../api/cameraService';
import { healthService } from '../api/healthService';

export default function HealthDashboard({ lightTheme = false }) {
  const [pipelineHealth, setPipelineHealth] = useState({});
  const [systemHealth, setSystemHealth] = useState(null);
  const [registeredCameras, setRegisteredCameras] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const pollInProgress = useRef(false);

  const fetchHealth = async () => {
    if (pollInProgress.current) return;
    pollInProgress.current = true;
    try {
      const camPromise = typeof cameraService.getCameras === 'function'
        ? cameraService.getCameras().catch(() => [])
        : Promise.resolve([]);

      const [pipeData, sysData, camData] = await Promise.all([
        cameraService.getPipelinesStatus(),
        healthService.getSystemHealth(),
        camPromise,
      ]);
      setPipelineHealth(pipeData || {});
      setSystemHealth(sysData || null);
      const rawCams = Array.isArray(camData) ? camData : (camData?.cameras || []);
      setRegisteredCameras(rawCams);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to load telemetry data.');
    } finally {
      setLoading(false);
      pollInProgress.current = false;
    }
  };

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 5000);
    return () => clearInterval(interval);
  }, []);

  if (loading && Object.keys(pipelineHealth).length === 0) {
    return <div className="loading" aria-busy="true">Loading telemetry...</div>;
  }

  const pipelines = Object.values(pipelineHealth);
  const cameras = pipelines;
  const totalRegistered = registeredCameras.length > 0
    ? registeredCameras.length
    : pipelines.length;

  return (
    <div
      className="health-dashboard health-dashboard-c2"
      data-testid="health-dashboard"
      style={lightTheme ? {} : { background: '#0a0f18', color: '#f0f6fc' }}
    >
      <div className="health-title-deck">
        <div>
          <h2>System Telemetry & Health</h2>
          <span className="health-sub">SENTINEL AI & BACKEND PERSISTENCE RUNTIME METRICS</span>
        </div>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={fetchHealth}
          disabled={loading}
        >
          {loading ? 'Refreshing...' : 'Refresh Health'}
        </button>
      </div>

      {error && (
        <div className="error-banner" role="alert">
          <strong>API Error:</strong> {error}
        </div>
      )}

      {/* TOP ROW: SYSTEM & WORKER STATUS */}
      <div className="health-overview-grid">
        {/* SYSTEM STATUS */}
        <div className="health-card" style={lightTheme ? {} : { background: '#0f172a', color: '#f0f6fc' }}>
          <div className="health-card-header">
            <span className="card-tag">SYSTEM</span>
            <span className="card-title">BACKEND HOST & ENVIRONMENT</span>
          </div>
          <div className="health-card-body">
            <div className="health-stat-line">
              <span className="stat-name">API STATUS:</span>
              {error ? (
                <span className="stat-val text-offline">UNAVAILABLE (API ERROR)</span>
              ) : (
                <span className="stat-val text-online">OPERATIONAL (HEALTHY)</span>
              )}
            </div>
            <div className="health-stat-line">
              <span className="stat-name">REGISTERED NODES:</span>
              {error && totalRegistered === 0 ? (
                <span className="stat-val text-muted">DATA UNAVAILABLE</span>
              ) : (
                <span className="stat-val">{totalRegistered} CAMERAS</span>
              )}
            </div>
            <div className="health-stat-line">
              <span className="stat-name">ACTIVE PIPELINES:</span>
              {error && pipelines.length === 0 ? (
                <span className="stat-val text-muted">DATA UNAVAILABLE</span>
              ) : (
                <span className="stat-val">{pipelines.length} RUNNING</span>
              )}
            </div>
            <div className="health-stat-line">
              <span className="stat-name">TELEMETRY POLLING:</span>
              <span className="stat-val">5000 MS ACTIVE INTERVAL</span>
            </div>
          </div>
        </div>

        {/* WORKER / PERSISTENCE STATUS */}
        <div className="health-card system-health-section" style={lightTheme ? {} : { background: '#0f172a', color: '#f0f6fc' }}>
          <div className="health-card-header">
            <span className="card-tag">WORKER</span>
            <span className="card-title">Global Persistence Worker</span>
          </div>
          <div className="health-card-body">
            {systemHealth && systemHealth.anpr_persistence_worker ? (
              <>
                <div className="health-stat-line">
                  <span className="stat-name">WORKER THREAD:</span>
                  <span className={`stat-val ${systemHealth.anpr_persistence_worker.is_alive ? 'text-online' : 'text-offline'}`}>
                    {systemHealth.anpr_persistence_worker.is_alive ? 'Alive / Running' : 'Stopped / Dead'}
                  </span>
                </div>
                <div className="health-stat-line">
                  <span className="stat-name">BACKLOG QUEUE:</span>
                  <span className={`stat-val queue-badge ${systemHealth.anpr_persistence_worker.queue_size > 100 ? 'text-warning' : 'text-online'}`}>
                    {systemHealth.anpr_persistence_worker.queue_size} items
                  </span>
                </div>
                <div className="health-stat-line">
                  <span className="stat-name">STORAGE BACKEND:</span>
                  <span className="stat-val">POSTGRESQL AUDIT STORE</span>
                </div>
              </>
            ) : (
              <div className="text-muted">Persistence worker data not available.</div>
            )}
          </div>
        </div>
      </div>

      {/* CAMERAS & AI PIPELINES TELEMETRY */}
      <div className="pipelines-health-section">
        <div className="section-header-c2">
          <span className="c2-label">Camera Pipelines & Stream Health</span>
          <span className="meta-tag">{cameras.length} MONITORED NODES</span>
        </div>

        {cameras.length === 0 ? (
          <p className="empty-state">No camera pipelines registered in system.</p>
        ) : (
          <div className="health-table-wrapper">
            <table className="health-telemetry-table">
              <thead>
                <tr>
                  <th>CAMERA</th>
                  <th>STREAM</th>
                  <th>AI PIPELINE</th>
                  <th>ANPR ENGINE</th>
                  <th>FPS / PTS</th>
                  <th>DETECTIONS / TRACKS</th>
                  <th>ERRORS</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map((cam, idx) => {
                  const stream = cam.stream_health || {};
                  const stats = cam.pipeline_stats || {};
                  const isOnline = stream.status === 'online';
                  const streamStatusText = (stream.status || 'UNKNOWN').toUpperCase();
                  const lastPts = stream.last_pts_ms ? stream.last_pts_ms.toFixed(0) : 'N/A';
                  const camCode = cam.camera_id || cam.camera_code || `CAM-${cam.id || idx}`;
                  const isRunning = (cam.status || '').toUpperCase() === 'RUNNING';

                  return (
                    <tr key={camCode}>
                      {/* CAMERA */}
                      <td className="cell-cam">
                        <strong>{camCode}</strong>
                      </td>

                      {/* STREAM */}
                      <td className="cell-stream">
                        <span className={`status-pill ${isOnline ? 'pill-live' : 'pill-offline'}`}>
                          <span className="dot"></span>
                          {streamStatusText}
                        </span>
                        <div className="sub-meta">
                          {stream.properties?.codec || 'No Codec'} / {stream.properties?.width ? `${stream.properties.width}x${stream.properties.height}` : 'No Res'}
                        </div>
                      </td>

                      {/* AI PIPELINE */}
                      <td className="cell-ai">
                        <span className={`status-badge status-${(cam.status || '').toLowerCase()}`}>
                          {(cam.status || 'stopped').toLowerCase()}
                        </span>
                        <div className="sub-meta">
                          R: {stats.frames_total || 0} / S: {stats.frames_skipped || 0} / D: {stats.frames_detected || 0} / T: {stats.frames_tracked || 0}
                        </div>
                      </td>

                      {/* ANPR ENGINE */}
                      <td className="cell-anpr">
                        <strong>{stats.frames_anpr || 0} reads</strong>
                      </td>

                      {/* FPS / PTS */}
                      <td className="cell-pts font-mono">
                        {lastPts}
                      </td>

                      {/* DETECTIONS / TRACKS */}
                      <td className="cell-det">
                        Det: <strong>{stats.frames_detected || 0}</strong> | Track: <strong>{stats.frames_tracked || 0}</strong>
                      </td>

                      {/* ERRORS */}
                      <td className="cell-errors">
                        <span className={(stats.total_errors || 0) > 0 ? 'text-offline font-bold' : 'text-online'}>
                          {stats.total_errors || 0}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
