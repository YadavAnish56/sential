import React, { useState, useEffect, useRef, useCallback } from 'react';
import { cameraService } from '../api/cameraService';
import { healthService } from '../api/healthService';

const RUNNING_STATES = new Set(['running', 'starting']);

function isRunning(pipeline) {
  if (!pipeline) return false;
  if (typeof pipeline.is_running === 'boolean') return pipeline.is_running;
  return RUNNING_STATES.has(String(pipeline.status || '').toLowerCase());
}

// Telemetry belongs to a live pipeline. A stopped one reports a dash rather
// than its last reading, so an operator never mistakes stale counters for now.
function liveValue(pipeline, value, fallback = '—') {
  return isRunning(pipeline) ? value : fallback;
}

function formatClock(date) {
  if (!date) return null;
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function HealthDashboard({ lightTheme = false }) {
  const [pipelineHealth, setPipelineHealth] = useState({});
  const [systemHealth, setSystemHealth] = useState(null);
  const [registeredCameras, setRegisteredCameras] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [error, setError] = useState(null);

  const pollInProgress = useRef(false);
  const mounted = useRef(true);

  // `force` is set by the Refresh button: a manual refresh must never be
  // dropped just because the background poll happens to be in flight.
  const fetchHealth = useCallback(async ({ force = false } = {}) => {
    if (pollInProgress.current && !force) return;
    pollInProgress.current = true;
    if (force) setRefreshing(true);
    try {
      const camPromise = typeof cameraService.getCameras === 'function'
        ? cameraService.getCameras().catch(() => [])
        : Promise.resolve([]);

      const [pipeData, sysData, camData] = await Promise.all([
        cameraService.getPipelinesStatus(),
        healthService.getSystemHealth(),
        camPromise,
      ]);
      if (!mounted.current) return;
      setPipelineHealth(pipeData || {});
      setSystemHealth(sysData || null);
      const rawCams = Array.isArray(camData) ? camData : (camData?.cameras || []);
      setRegisteredCameras(rawCams);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      if (mounted.current) setError(err.message || 'Could not reach the server.');
    } finally {
      if (mounted.current) {
        setLoading(false);
        setRefreshing(false);
      }
      pollInProgress.current = false;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    fetchHealth();
    const interval = setInterval(fetchHealth, 5000);
    return () => {
      mounted.current = false;
      clearInterval(interval);
    };
  }, [fetchHealth]);

  if (loading && Object.keys(pipelineHealth).length === 0) {
    return <div className="loading" aria-busy="true">Loading telemetry...</div>;
  }

  const pipelines = Object.values(pipelineHealth);
  const runningCount = pipelines.filter(isRunning).length;
  const plateRecognition = systemHealth?.plate_recognition || null;

  // Every registered camera gets a row, whether or not a pipeline was ever
  // started for it, so a newly added camera shows up here immediately.
  const byCode = new Map();
  registeredCameras.forEach((cam) => {
    const code = cam.camera_code || cam.camera_id || String(cam.id);
    byCode.set(code, { code, camera: cam, pipeline: null });
  });
  pipelines.forEach((p, idx) => {
    const code = p.camera_id || p.camera_code || `CAM-${idx}`;
    const existing = byCode.get(code);
    if (existing) existing.pipeline = p;
    else byCode.set(code, { code, camera: null, pipeline: p });
  });
  const rows = Array.from(byCode.values());
  const totalRegistered = registeredCameras.length > 0 ? registeredCameras.length : pipelines.length;

  return (
    <div
      className="health-dashboard health-dashboard-c2"
      data-testid="health-dashboard"
      style={lightTheme ? {} : { background: '#0a0f18', color: '#f0f6fc' }}
    >
      <div className="health-title-deck">
        <div>
          <h2>System Telemetry &amp; Health</h2>
          <span className="health-sub">Live status of cameras and detection engines</span>
        </div>
        <div className="health-refresh-group" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {lastUpdated && (
            <span className="health-updated" style={{ fontSize: '0.78rem', opacity: 0.7 }}>
              Updated {formatClock(lastUpdated)}
            </span>
          )}
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => fetchHealth({ force: true })}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      {error && (
        <div className="error-banner" role="alert">
          <strong>API Error:</strong> {error}
        </div>
      )}

      {/* Detection and tracking keep running without plate recognition, so the
          system looks busy while recording nothing. Say so outright. */}
      {plateRecognition && plateRecognition.ready === false && (
        <div className="warning-banner" role="alert" data-testid="plate-recognition-warning">
          <strong>Plate recognition unavailable.</strong>{' '}
          {plateRecognition.reason || 'Plates cannot be read on this machine.'}
        </div>
      )}

      <div className="health-overview-grid">
        <div className="health-card" style={lightTheme ? {} : { background: '#0f172a', color: '#f0f6fc' }}>
          <div className="health-card-header">
            <span className="card-tag">System</span>
            <span className="card-title">Server</span>
          </div>
          <div className="health-card-body">
            <div className="health-stat-line">
              <span className="stat-name">Status</span>
              {error ? (
                <span className="stat-val text-offline">Unavailable</span>
              ) : (
                <span className="stat-val text-online">Online</span>
              )}
            </div>
            <div className="health-stat-line">
              <span className="stat-name">Cameras</span>
              {error && totalRegistered === 0 ? (
                <span className="stat-val text-muted">{'—'}</span>
              ) : (
                <span className="stat-val">{totalRegistered}</span>
              )}
            </div>
            <div className="health-stat-line">
              <span className="stat-name">AI running</span>
              {error && pipelines.length === 0 ? (
                <span className="stat-val text-muted">{'—'}</span>
              ) : (
                <span className="stat-val">{runningCount} of {totalRegistered}</span>
              )}
            </div>
            <div className="health-stat-line">
              <span className="stat-name">Refreshes every</span>
              <span className="stat-val">5 seconds</span>
            </div>
          </div>
        </div>

        <div className="health-card system-health-section" style={lightTheme ? {} : { background: '#0f172a', color: '#f0f6fc' }}>
          <div className="health-card-header">
            <span className="card-tag">Worker</span>
            <span className="card-title">Saving detections</span>
          </div>
          <div className="health-card-body">
            {systemHealth && systemHealth.anpr_persistence_worker ? (
              <>
                <div className="health-stat-line">
                  <span className="stat-name">Status</span>
                  <span className={`stat-val ${systemHealth.anpr_persistence_worker.is_alive ? 'text-online' : 'text-offline'}`}>
                    {systemHealth.anpr_persistence_worker.is_alive ? 'Alive / Running' : 'Stopped / Dead'}
                  </span>
                </div>
                <div className="health-stat-line">
                  <span className="stat-name">Waiting to save</span>
                  <span className={`stat-val queue-badge ${systemHealth.anpr_persistence_worker.queue_size > 100 ? 'text-warning' : 'text-online'}`}>
                    {systemHealth.anpr_persistence_worker.queue_size} items
                  </span>
                </div>
                <div className="health-stat-line">
                  <span className="stat-name">Database</span>
                  <span className="stat-val">Connected</span>
                </div>
              </>
            ) : (
              <div className="text-muted">Persistence worker data not available.</div>
            )}
          </div>
        </div>
      </div>

      <div className="pipelines-health-section">
        <div className="section-header-c2">
          <span className="c2-label">Camera Status</span>
          <span className="meta-tag">{rows.length} total</span>
        </div>

        {rows.length === 0 ? (
          <p className="empty-state">No cameras registered yet.</p>
        ) : (
          <div className="health-table-wrapper">
            <table className="health-telemetry-table">
              <thead>
                <tr>
                  <th>Camera</th>
                  <th>Stream</th>
                  <th>AI Status</th>
                  <th>Plates read</th>
                  <th>Stream time</th>
                  <th>Vehicles seen</th>
                  <th>Errors</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ code, camera, pipeline }) => {
                  const stream = (pipeline && pipeline.stream_health) || {};
                  const stats = (pipeline && pipeline.pipeline_stats) || {};
                  const running = isRunning(pipeline);
                  const online = stream.status === 'online';
                  const streamStatusText = (stream.status || (camera ? 'offline' : 'unknown')).toUpperCase();
                  const lastPts = running && stream.last_pts_ms ? stream.last_pts_ms.toFixed(0) : '—';
                  const aiState = pipeline ? String(pipeline.status || 'stopped').toLowerCase() : 'not started';

                  return (
                    <tr key={code}>
                      <td className="cell-cam">
                        <strong>{code}</strong>
                        {camera?.name && <div className="sub-meta">{camera.name}</div>}
                      </td>

                      <td className="cell-stream">
                        <span className={`status-pill ${online ? 'pill-live' : 'pill-offline'}`}>
                          <span className="dot"></span>
                          {streamStatusText}
                        </span>
                        <div className="sub-meta">
                          {stream.properties?.codec || 'No Codec'} / {stream.properties?.width ? `${stream.properties.width}x${stream.properties.height}` : 'No Res'}
                        </div>
                      </td>

                      <td className="cell-ai">
                        <span className={`status-badge status-${aiState.replace(/\s+/g, '-')}`}>
                          {aiState}
                        </span>
                        <div className="sub-meta">
                          {running
                            ? `R: ${stats.frames_total || 0} / S: ${stats.frames_skipped || 0} / D: ${stats.frames_detected || 0} / T: ${stats.frames_tracked || 0}`
                            : '—'}
                        </div>
                      </td>

                      <td className="cell-anpr">
                        <strong>{liveValue(pipeline, `${stats.frames_anpr || 0} reads`)}</strong>
                      </td>

                      <td className="cell-pts font-mono">
                        {lastPts}
                      </td>

                      <td className="cell-det">
                        {running
                          ? <>Det: <strong>{stats.frames_detected || 0}</strong> | Track: <strong>{stats.frames_tracked || 0}</strong></>
                          : '—'}
                      </td>

                      <td className="cell-errors">
                        <span className={(stats.total_errors || 0) > 0 ? 'text-offline font-bold' : 'text-online'}>
                          {liveValue(pipeline, stats.total_errors || 0)}
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
