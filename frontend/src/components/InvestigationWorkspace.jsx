import React, { useState, useEffect, useMemo } from 'react';
import { analyticsService } from '../api/analyticsService';
import { cameraService } from '../api/cameraService';
import { isTestCamera } from '../utils/cameraState';

export default function InvestigationWorkspace({
  cameras = [],
  selectedCameraId = null,
  onSelectCamera = null,
  pipelinesStatus = {},
  onInvestigationPathChange = null,
  onRefreshPipelines = null,
  initialPlate = '',
}) {
  const [targetPlate, setTargetPlate] = useState(initialPlate || '');
  const [timeWindow, setTimeWindow] = useState('all'); // '1h' | '2h' | '6h' | '24h' | 'custom' | 'all'
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');

  // Camera scope checkboxes (set of camera IDs)
  const [selectedCameraIds, setSelectedCameraIds] = useState(new Set());

  // Search & Result state
  const [searchExecuted, setSearchExecuted] = useState(false);
  const [searchedPlate, setSearchedPlate] = useState('');
  const [timelineData, setTimelineData] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [searchError, setSearchError] = useState(null);

  // Batch AI action state
  const [isAiBatchPending, setIsAiBatchPending] = useState(false);
  const [aiActionMessage, setAiActionMessage] = useState(null);

  // Sync initialPlate if provided as prop
  useEffect(() => {
    if (initialPlate && initialPlate.trim() !== '') {
      setTargetPlate(initialPlate.trim().toUpperCase());
    }
  }, [initialPlate]);

  // Initialize selected camera IDs with all registered cameras if empty
  useEffect(() => {
    if (cameras.length > 0 && selectedCameraIds.size === 0) {
      setSelectedCameraIds(new Set(cameras.map((c) => c.id)));
    }
  }, [cameras]);

  const toggleCameraSelection = (id) => {
    setSelectedCameraIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleSelectAllCameras = () => {
    setSelectedCameraIds(new Set(cameras.map((c) => c.id)));
  };

  const handleClearAllCameras = () => {
    setSelectedCameraIds(new Set());
  };

  // Perform search against existing PostgreSQL-backed API
  const handleSearchRecords = async (e) => {
    if (e) e.preventDefault();
    const cleanPlate = targetPlate.trim().toUpperCase();
    if (!cleanPlate) {
      setSearchError('Please enter a target vehicle registration plate.');
      return;
    }

    setIsLoading(true);
    setSearchError(null);
    setSearchExecuted(true);
    setSearchedPlate(cleanPlate);

    try {
      const data = await analyticsService.getVehicleTimeline(cleanPlate);
      setTimelineData(data);
    } catch (err) {
      // 404 means vehicle record not found in database
      if (err.status === 404 || (err.message && err.message.includes('404'))) {
        setTimelineData({ plate_number: cleanPlate, timeline: [], total_detections: 0 });
      } else {
        setSearchError(err.message || 'Error querying vehicle detection timeline.');
        setTimelineData(null);
      }
    } finally {
      setIsLoading(false);
    }
  };

  // Client-side filtering of sightings by time window and camera scope
  const filteredSightings = useMemo(() => {
    const rawList = timelineData?.timeline || timelineData?.sightings;
    if (!rawList || !Array.isArray(rawList)) return [];

    let list = rawList;

    // Filter by selected camera scope
    if (selectedCameraIds.size > 0 && selectedCameraIds.size < cameras.length) {
      list = list.filter((item) => selectedCameraIds.has(item.camera_id));
    }

    // Filter by time window (clearly client-side)
    if (timeWindow !== 'all') {
      const now = new Date().getTime();
      let windowMs = 0;
      if (timeWindow === '1h') windowMs = 1 * 60 * 60 * 1000;
      else if (timeWindow === '2h') windowMs = 2 * 60 * 60 * 1000;
      else if (timeWindow === '6h') windowMs = 6 * 60 * 60 * 1000;
      else if (timeWindow === '24h') windowMs = 24 * 60 * 60 * 1000;

      if (windowMs > 0) {
        list = list.filter((item) => {
          const itemTime = new Date(item.timestamp).getTime();
          return now - itemTime <= windowMs;
        });
      } else if (timeWindow === 'custom') {
        const startMs = customStart ? new Date(customStart).getTime() : 0;
        const endMs = customEnd ? new Date(customEnd).getTime() : Infinity;
        list = list.filter((item) => {
          const itemTime = new Date(item.timestamp).getTime();
          return itemTime >= startMs && itemTime <= endMs;
        });
      }
    }

    return list;
  }, [timelineData, selectedCameraIds, timeWindow, customStart, customEnd, cameras.length]);

  // Propagate enriched checkpoint waypoint objects to map
  useEffect(() => {
    if (!onInvestigationPathChange) return;
    if (filteredSightings.length === 0) {
      onInvestigationPathChange([]);
      return;
    }
    const waypoints = filteredSightings
      .filter((s) => s.latitude != null && s.longitude != null)
      .map((s, idx) => ({
        latitude: s.latitude,
        longitude: s.longitude,
        camera_id: s.camera_id,
        camera_code: s.camera_code,
        camera_name: s.camera_name,
        timestamp: s.timestamp,
        confidence: s.confidence,
        vehicle_type: s.vehicle_type,
        index: idx + 1,
      }));
    onInvestigationPathChange(waypoints);
  }, [filteredSightings, onInvestigationPathChange]);

  // Batch Engage AI on selected cameras only
  const handleBatchEngageAi = async () => {
    const targets = Array.from(selectedCameraIds);
    if (targets.length === 0) {
      setAiActionMessage({ type: 'warning', text: 'No cameras selected in camera scope.' });
      return;
    }

    const operationalTargets = targets.filter((id) => {
      const cam = cameras.find((c) => c.id === id);
      return !isTestCamera(cam);
    });

    const skippedTestCount = targets.length - operationalTargets.length;

    if (operationalTargets.length === 0) {
      setAiActionMessage({
        type: 'warning',
        text: 'Selected cameras are test nodes with no active RTSP stream. AI inference cannot be engaged.',
      });
      return;
    }

    setIsAiBatchPending(true);
    setAiActionMessage(null);
    let successCount = 0;
    let failCount = 0;

    for (const camId of operationalTargets) {
      try {
        await cameraService.startPipeline(camId);
        successCount++;
      } catch (err) {
        failCount++;
      }
    }

    if (onRefreshPipelines) {
      await onRefreshPipelines();
    }
    setIsAiBatchPending(false);
    setAiActionMessage({
      type: failCount === 0 ? 'success' : 'warning',
      text: `AI engaged on ${successCount} selected camera(s)${skippedTestCount > 0 ? ` (${skippedTestCount} test node(s) skipped)` : ''}${failCount > 0 ? `, ${failCount} failed` : ''}.`,
    });
  };

  // Batch Stop AI on selected cameras only
  const handleBatchStopAi = async () => {
    const targets = Array.from(selectedCameraIds);
    if (targets.length === 0) {
      setAiActionMessage({ type: 'warning', text: 'No cameras selected in camera scope.' });
      return;
    }

    setIsAiBatchPending(true);
    setAiActionMessage(null);
    let stoppedCount = 0;

    for (const camId of targets) {
      try {
        await cameraService.stopPipeline(camId);
        stoppedCount++;
      } catch (err) {
        // Ignored if already stopped
      }
    }

    if (onRefreshPipelines) {
      await onRefreshPipelines();
    }
    setIsAiBatchPending(false);
    setAiActionMessage({
      type: 'success',
      text: `AI inference stopped on ${stoppedCount} selected camera(s).`,
    });
  };

  const targetVehicle = timelineData?.vehicle || null;

  return (
    <div className="investigation-workspace-container" data-testid="investigation-workspace">
      {/* 1. QUERY BUILDER & SCOPE DECK */}
      <div className="investigation-control-deck">
        <form onSubmit={handleSearchRecords} className="investigation-form-grid">
          {/* Target Plate Input */}
          <div className="c2-field-group">
            <label htmlFor="target-plate-input">TARGET REGISTRATION PLATE *</label>
            <div className="input-action-row">
              <input
                id="target-plate-input"
                type="text"
                value={targetPlate}
                onChange={(e) => setTargetPlate(e.target.value.toUpperCase())}
                placeholder="e.g. GJ05AB1234 or KA02MM9091"
                className="c2-input"
                autoComplete="off"
              />
              <button
                type="submit"
                disabled={isLoading}
                className="btn btn-primary btn-search-records"
              >
                {isLoading ? 'Querying...' : 'SEARCH EXISTING RECORDS'}
              </button>
            </div>
          </div>

          {/* Time Window Dropdown */}
          <div className="c2-field-group time-window-group">
            <label htmlFor="time-window-select">TIME WINDOW</label>
            <select
              id="time-window-select"
              value={timeWindow}
              onChange={(e) => setTimeWindow(e.target.value)}
              className="c2-select"
            >
              <option value="all">Full Sighting History (All Time)</option>
              <option value="1h">Last 1 Hour</option>
              <option value="2h">Last 2 Hours</option>
              <option value="6h">Last 6 Hours</option>
              <option value="24h">Last 24 Hours</option>
              <option value="custom">Custom Range</option>
            </select>
          </div>

          {/* Custom Date Pickers if selected */}
          {timeWindow === 'custom' && (
            <div className="c2-field-group custom-time-group">
              <label>CUSTOM TIME RANGE</label>
              <div className="custom-inputs-row">
                <input
                  type="datetime-local"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  className="c2-input c2-date"
                  placeholder="Start Time"
                />
                <span className="date-sep">to</span>
                <input
                  type="datetime-local"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  className="c2-input c2-date"
                  placeholder="End Time"
                />
              </div>
            </div>
          )}
        </form>

        {/* Camera Scope Multi-select & Batch AI Controls */}
        <div className="camera-scope-deck">
          <div className="scope-header">
            <div className="scope-title">
              <strong>Cameras in scope</strong>
              <span className="scope-counter">
                {selectedCameraIds.size} of {cameras.length} cameras
              </span>
            </div>
            <div className="scope-actions">
              <button type="button" className="btn btn-xs btn-secondary" onClick={handleSelectAllCameras}>
                Select All
              </button>
              <button type="button" className="btn btn-xs btn-secondary" onClick={handleClearAllCameras}>
                Clear
              </button>
              <button
                type="button"
                className="btn btn-xs btn-success"
                onClick={handleBatchEngageAi}
                disabled={isAiBatchPending || selectedCameraIds.size === 0}
              >
                Engage AI on Selected
              </button>
              <button
                type="button"
                className="btn btn-xs btn-danger"
                onClick={handleBatchStopAi}
                disabled={isAiBatchPending || selectedCameraIds.size === 0}
              >
                Stop AI
              </button>
            </div>
          </div>

          {/* Camera Chips Checklist */}
          <div className="camera-chips-grid">
            {cameras.map((cam) => {
              const isSelected = selectedCameraIds.has(cam.id);
              const isTest = isTestCamera(cam);
              const pStatus = (pipelinesStatus[cam.camera_code]?.status || 'OFF').toUpperCase();
              const isAiRunning = pStatus === 'RUNNING';

              return (
                <label
                  key={cam.id}
                  className={`camera-scope-chip ${isSelected ? 'selected' : ''} ${isAiRunning ? 'ai-active' : ''} ${isTest ? 'test-scope-chip' : ''}`}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleCameraSelection(cam.id)}
                  />
                  <span className="chip-code">{cam.camera_code}</span>
                  {isTest ? (
                    <span className="chip-ai-tag tag-test">TEST</span>
                  ) : (
                    <span className={`chip-ai-tag ${isAiRunning ? 'tag-on' : 'tag-off'}`}>
                      {isAiRunning ? 'AI ON' : 'AI OFF'}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        </div>

        {/* AI Action Feedback Banner */}
        {aiActionMessage && (
          <div
            className={`action-feedback-banner ${aiActionMessage.type === 'success' ? 'feedback-success' : 'feedback-warning'}`}
            role="status"
          >
            {aiActionMessage.text}
          </div>
        )}

        {/* Error Banner */}
        {searchError && (
          <div className="search-error-banner" role="alert">
            <strong>Search Error:</strong> {searchError}
          </div>
        )}
      </div>

      {/* 2. RECONSTRUCTED TARGET METADATA & SUMMARY */}
      {searchExecuted && !isLoading && (
        <div className="target-summary-strip">
          <div className="target-meta-header">
            <span className="target-label">INVESTIGATION TARGET:</span>
            <span className="target-plate-badge">{searchedPlate}</span>
            <span className="target-detections-tag">
              {filteredSightings.length} SIGHTINGS DETECTED
            </span>
          </div>

          <div className="target-specs-grid">
            <div className="spec-card">
              <span className="spec-key">VEHICLE TYPE</span>
              <span className="spec-val">{targetVehicle?.vehicle_type || 'VEHICLE'}</span>
            </div>
            <div className="spec-card">
              <span className="spec-key">MAKE</span>
              <span className={`spec-val ${targetVehicle?.make ? '' : 'text-muted'}`}>
                {targetVehicle?.make || 'NOT AVAILABLE'}
              </span>
            </div>
            <div className="spec-card">
              <span className="spec-key">MODEL</span>
              <span className={`spec-val ${targetVehicle?.model ? '' : 'text-muted'}`}>
                {targetVehicle?.model || 'NOT AVAILABLE'}
              </span>
            </div>
            <div className="spec-card">
              <span className="spec-key">COLOR</span>
              <span className={`spec-val ${targetVehicle?.color ? '' : 'text-muted'}`}>
                {targetVehicle?.color || 'NOT AVAILABLE'}
              </span>
            </div>
            <div className="spec-card">
              <span className="spec-key">FIRST SIGHTING</span>
              <span className="spec-val">
                {targetVehicle?.first_seen
                  ? new Date(targetVehicle.first_seen).toLocaleString()
                  : (filteredSightings[0] ? new Date(filteredSightings[0].timestamp).toLocaleString() : 'N/A')}
              </span>
            </div>
            <div className="spec-card">
              <span className="spec-key">LAST SIGHTING</span>
              <span className="spec-val">
                {targetVehicle?.last_seen
                  ? new Date(targetVehicle.last_seen).toLocaleString()
                  : (filteredSightings[filteredSightings.length - 1]
                      ? new Date(filteredSightings[filteredSightings.length - 1].timestamp).toLocaleString()
                      : 'N/A')}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* 3. INCIDENT SIGHTINGS LOG (CHRONOLOGICAL CHECKPOINTS) */}
      <div className="investigation-results-deck">
        <div className="results-header">
          <div className="results-title">
            <strong>Sightings</strong>
            {timeWindow !== 'all' && (
              <span className="filter-hint">
                [Client-Side Time Filter: {timeWindow.toUpperCase()}]
              </span>
            )}
          </div>
          <span className="results-sub">
            Recorded sightings
          </span>
        </div>

        {!isLoading && !searchExecuted && (
          <div className="empty-search-hint">
            <span>Enter a registration plate above and click <strong>SEARCH EXISTING RECORDS</strong> to query timeline checkpoints.</span>
          </div>
        )}

        {isLoading && (
          <div className="loading-stage">
            <span>Querying PostgreSQL detection logs...</span>
          </div>
        )}

        {!isLoading && searchExecuted && filteredSightings.length === 0 && (
          <div className="no-sightings-stage">
            <h4>NO SIGHTINGS RECORDED FOR {searchedPlate}</h4>
            <p>
              No detection records match the current camera scope and time filter. Try expanding the
              time window or selecting additional camera nodes.
            </p>
          </div>
        )}

        {!isLoading && filteredSightings.length > 0 && (
          <div className="sightings-timeline-list">
            {filteredSightings.map((sighting, idx) => {
              const isSelected = sighting.camera_id === selectedCameraId;
              const hasGps = sighting.latitude != null && sighting.longitude != null;
              const ptsVal = sighting.pts ?? sighting.pts_ms ?? null;
              const makeVal = sighting.make ? String(sighting.make).toUpperCase() : 'NOT AVAILABLE';
              const modelVal = sighting.model ? String(sighting.model).toUpperCase() : 'NOT AVAILABLE';
              const colorVal = sighting.color ? String(sighting.color).toUpperCase() : 'NOT AVAILABLE';

              return (
                <div
                  key={sighting.id || sighting.event_id || idx}
                  className={`sighting-row ${isSelected ? 'selected-checkpoint' : ''}`}
                  onClick={() => onSelectCamera && onSelectCamera(sighting.camera_id)}
                >
                  <div className="sighting-idx">#{idx + 1}</div>

                  <div className="sighting-cam-block">
                    <span className="sighting-code">
                      {sighting.camera_code || `CAM-${sighting.camera_id}`}
                    </span>
                    <span className="sighting-name">
                      {sighting.camera_name || `Camera ${sighting.camera_id}`}
                    </span>
                  </div>

                  <div className="sighting-location-block">
                    <span className="sighting-loc-text">
                      {sighting.location || 'Location Not Cataloged'}
                    </span>
                    <span className="sighting-gps-text">
                      {hasGps
                        ? `GPS: ${sighting.latitude.toFixed(4)}, ${sighting.longitude.toFixed(4)}`
                        : 'GPS: Not Mapped'}
                    </span>
                  </div>

                  <div className="sighting-time-block">
                    <span className="sighting-time">
                      {new Date(sighting.timestamp).toLocaleTimeString()}
                    </span>
                    <span className="sighting-date">
                      {new Date(sighting.timestamp).toLocaleDateString()}
                    </span>
                  </div>

                  <div className="sighting-meta-block">
                    <span className="sighting-conf">
                      CONF: {sighting.confidence != null ? `${(sighting.confidence * 100).toFixed(1)}%` : 'N/A'}
                    </span>
                    {ptsVal != null && <span className="sighting-pts">PTS: {ptsVal} ms</span>}
                  </div>

                  <div className="sighting-attrs-block">
                    <span className="sighting-attr">TYPE: {(sighting.vehicle_type || 'VEHICLE').toUpperCase()}</span>
                    <span className="sighting-attr text-muted">MAKE: {makeVal}</span>
                    <span className="sighting-attr text-muted">MODEL: {modelVal}</span>
                    <span className="sighting-attr text-muted">COLOR: {colorVal}</span>
                  </div>

                  <div className="sighting-action-block">
                    <button
                      type="button"
                      className={`btn btn-xs ${isSelected ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        if (onSelectCamera) onSelectCamera(sighting.camera_id);
                      }}
                    >
                      {isSelected ? 'Focusing' : 'Focus Cam'}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
