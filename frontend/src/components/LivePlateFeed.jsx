import React, { useState, useEffect, useRef, useCallback } from 'react';
import { analyticsService } from '../api/analyticsService';
import { cameraService } from '../api/cameraService';
import { watchlistService } from '../api/watchlistService';

export default function LivePlateFeed({
  onSelectPlate = null,
  selectedCameraId = null,
  compactMode = false,
}) {
  const [events, setEvents] = useState([]);
  const [cameras, setCameras] = useState({});
  const [watchlistSet, setWatchlistSet] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [cameraFilter, setCameraFilter] = useState('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [isPolling, setIsPolling] = useState(true);
  const isFetchingRef = useRef(false);

  // Load camera metadata and active watchlist for enrichment
  const loadMetadata = useCallback(async () => {
    try {
      const [camsData, watchlistData] = await Promise.all([
        cameraService.getCameras().catch(() => ({ cameras: [] })),
        watchlistService.getWatchlist().catch(() => []),
      ]);

      const camList = Array.isArray(camsData) ? camsData : (camsData?.cameras || []);
      const camMap = {};
      camList.forEach((c) => {
        const id = c.id ?? c.camera_id;
        camMap[id] = {
          code: c.camera_code ?? `cam${id}`,
          name: c.name ?? `Camera ${id}`,
          location: c.location || c.location_description || null,
        };
      });
      setCameras(camMap);

      const activeSet = new Set(
        (watchlistData || [])
          .filter((w) => w.is_active !== false)
          .map((w) => (w.plate_number || '').trim().toUpperCase())
      );
      setWatchlistSet(activeSet);
    } catch (_) {
      // Non-blocking metadata failure
    }
  }, []);

  // Fetch recent detection events from PostgreSQL
  const fetchLiveEvents = useCallback(async () => {
    if (isFetchingRef.current) return;
    isFetchingRef.current = true;
    try {
      const data = await analyticsService.getRecentEvents(50);
      const rawEvents = Array.isArray(data?.events) ? data.events : (Array.isArray(data) ? data : []);
      // Filter out events that do not have a real plate or are empty
      setEvents(rawEvents);
      setError(null);
    } catch (err) {
      setError(err.message || 'Failed to retrieve live detection events.');
    } finally {
      isFetchingRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMetadata();
    fetchLiveEvents();

    if (!isPolling) return;
    const interval = setInterval(() => {
      fetchLiveEvents();
    }, 4000);
    return () => clearInterval(interval);
  }, [loadMetadata, fetchLiveEvents, isPolling]);

  // Sync cameraFilter if selectedCameraId changes and filter is scoped
  useEffect(() => {
    if (selectedCameraId && cameraFilter !== 'ALL') {
      setCameraFilter(String(selectedCameraId));
    }
  }, [selectedCameraId]);

  // Client-side filtering of detections
  const filteredEvents = events.filter((evt) => {
    if (cameraFilter !== 'ALL') {
      const matchId = String(evt.camera_id) === String(cameraFilter);
      const camMeta = cameras[evt.camera_id];
      const matchCode = camMeta && String(camMeta.code) === String(cameraFilter);
      if (!matchId && !matchCode) return false;
    }

    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      const plate = (evt.plate_number || '').toLowerCase();
      const camMeta = cameras[evt.camera_id];
      const code = (camMeta?.code || '').toLowerCase();
      const name = (camMeta?.name || '').toLowerCase();
      const loc = (camMeta?.location || '').toLowerCase();
      const type = (evt.vehicle_type || evt.object_type || '').toLowerCase();
      return plate.includes(q) || code.includes(q) || name.includes(q) || loc.includes(q) || type.includes(q);
    }

    return true;
  });

  const handleInvestigate = (plate) => {
    if (onSelectPlate && plate) {
      onSelectPlate(plate.trim().toUpperCase());
    }
  };

  return (
    <div className="live-plate-feed-root" data-testid="live-plate-feed">
      {/* Feed Control Bar */}
      <div className="feed-toolbar">
        <div className="feed-toolbar-left">
          <span className="feed-title">REAL-TIME ANPR DETECTION STREAM</span>
          <span className="feed-status-pill">
            <span className={`dot ${isPolling ? 'dot-online' : 'dot-offline'}`}></span>
            {isPolling ? 'LIVE INGESTION' : 'PAUSED'}
          </span>
          <span className="feed-count-badge">{filteredEvents.length} DETECTIONS</span>
        </div>

        <div className="feed-toolbar-right">
          {/* Camera Filter Selector */}
          <select
            className="c2-select feed-camera-select"
            value={cameraFilter}
            onChange={(e) => setCameraFilter(e.target.value)}
            aria-label="Filter detections by camera"
          >
            <option value="ALL">All Registered Cameras</option>
            {Object.entries(cameras).map(([id, cam]) => (
              <option key={id} value={id}>
                {cam.code} — {cam.name}
              </option>
            ))}
          </select>

          {/* Quick Search */}
          <input
            type="text"
            className="c2-input feed-search-input"
            placeholder="Filter plate, camera..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Filter detections"
          />

          {/* Pause / Resume & Refresh */}
          <button
            type="button"
            className="btn btn-xs btn-secondary"
            onClick={() => setIsPolling((prev) => !prev)}
            title={isPolling ? 'Pause real-time stream' : 'Resume real-time stream'}
          >
            {isPolling ? 'Pause' : 'Resume'}
          </button>
          <button
            type="button"
            className="btn btn-xs btn-secondary"
            onClick={fetchLiveEvents}
            title="Refresh detections"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Feed Content */}
      {loading && events.length === 0 && (
        <div className="feed-loading-state">Loading live detection stream...</div>
      )}

      {error && (
        <div className="feed-error-banner" role="alert">
          <strong>Ingestion Error:</strong> {error}
        </div>
      )}

      {!loading && !error && filteredEvents.length === 0 && (
        <div className="feed-empty-state">
          <div className="empty-title">NO REAL-TIME DETECTIONS RECORDED</div>
          <div className="empty-subtitle">
            Engage AI on camera nodes to process video frames and log vehicle registration plates.
          </div>
        </div>
      )}

      {!loading && filteredEvents.length > 0 && (
        <div className="feed-items-container">
          {filteredEvents.map((evt) => {
            const camMeta = cameras[evt.camera_id] || {
              code: `CAM-${evt.camera_id}`,
              name: `Camera ${evt.camera_id}`,
              location: null,
            };
            const plateStr = (evt.plate_number || '').trim().toUpperCase();
            const isWatchlistMatch = plateStr && watchlistSet.has(plateStr);
            const ptsVal = evt.pts ?? evt.pts_ms ?? null;
            const confPct = evt.confidence != null ? (evt.confidence * 100).toFixed(1) : null;
            const vehicleType = (evt.vehicle_type || evt.object_type || 'VEHICLE').toUpperCase();
            const locationStr = camMeta.location || 'UNMAPPED';

            // Real backend attributes only — otherwise NOT AVAILABLE
            const makeStr = evt.make ? String(evt.make).toUpperCase() : 'NOT AVAILABLE';
            const modelStr = evt.model ? String(evt.model).toUpperCase() : 'NOT AVAILABLE';
            const colorStr = evt.color ? String(evt.color).toUpperCase() : 'NOT AVAILABLE';

            const timeFormatted = evt.timestamp
              ? new Date(evt.timestamp).toLocaleTimeString()
              : 'N/A';
            const dateFormatted = evt.timestamp
              ? new Date(evt.timestamp).toLocaleDateString()
              : '';

            return (
              <div
                key={evt.id || `${evt.camera_id}-${evt.timestamp}-${plateStr}`}
                className={`feed-detection-card ${isWatchlistMatch ? 'card-watchlist-hit' : ''}`}
                data-testid={`detection-card-${evt.id || plateStr}`}
              >
                {/* Plate & Match Banner */}
                <div className="detection-card-top">
                  <div className="plate-block">
                    {plateStr ? (
                      <span className="tactical-plate-badge" data-testid="plate-badge">
                        {plateStr}
                      </span>
                    ) : (
                      <span className="tactical-plate-badge plate-unknown">UNRESOLVED</span>
                    )}
                    {isWatchlistMatch && (
                      <span className="watchlist-hit-pill" data-testid="watchlist-match-badge">
                        WATCHLIST MATCH
                      </span>
                    )}
                  </div>

                  <div className="timestamp-block">
                    <span className="time-primary">{timeFormatted}</span>
                    {dateFormatted && <span className="date-secondary">{dateFormatted}</span>}
                  </div>
                </div>

                {/* Primary Telemetry Grid */}
                <div className="detection-telemetry-grid">
                  <div className="telemetry-cell">
                    <span className="cell-label">CAMERA:</span>
                    <span className="cell-value cell-highlight">
                      {camMeta.code} ({camMeta.name})
                    </span>
                  </div>

                  <div className="telemetry-cell">
                    <span className="cell-label">LOCATION:</span>
                    <span className="cell-value">{locationStr}</span>
                  </div>

                  <div className="telemetry-cell">
                    <span className="cell-label">VEHICLE TYPE:</span>
                    <span className="cell-value">{vehicleType}</span>
                  </div>

                  <div className="telemetry-cell">
                    <span className="cell-label">CONFIDENCE:</span>
                    <span className="cell-value">
                      {confPct ? `${confPct}%` : 'N/A'}
                    </span>
                  </div>

                  <div className="telemetry-cell">
                    <span className="cell-label">PTS:</span>
                    <span className="cell-value">
                      {ptsVal != null ? `${ptsVal} ms` : 'N/A'}
                    </span>
                  </div>

                  <div className="telemetry-cell">
                    <span className="cell-label">MAKE:</span>
                    <span className={`cell-value ${makeStr === 'NOT AVAILABLE' ? 'text-muted' : ''}`}>
                      {makeStr}
                    </span>
                  </div>

                  <div className="telemetry-cell">
                    <span className="cell-label">MODEL:</span>
                    <span className={`cell-value ${modelStr === 'NOT AVAILABLE' ? 'text-muted' : ''}`}>
                      {modelStr}
                    </span>
                  </div>

                  <div className="telemetry-cell">
                    <span className="cell-label">COLOR:</span>
                    <span className={`cell-value ${colorStr === 'NOT AVAILABLE' ? 'text-muted' : ''}`}>
                      {colorStr}
                    </span>
                  </div>
                </div>

                {/* Action Bar */}
                <div className="detection-card-actions">
                  {plateStr && (
                    <button
                      type="button"
                      className="btn btn-xs btn-primary btn-investigate-target"
                      onClick={() => handleInvestigate(plateStr)}
                      title={`Investigate target plate ${plateStr}`}
                    >
                      INVESTIGATE TARGET
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
