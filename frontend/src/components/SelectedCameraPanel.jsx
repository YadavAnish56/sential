import React, { useState, useEffect } from 'react';
import LivePreview from './LivePreview';
import { cameraService } from '../api/cameraService';
import {
  isTestCamera,
  resolveVideoState,
  resolveAiState,
  formatHumanReadableError,
} from '../utils/cameraState';

export default function SelectedCameraPanel({
  camera,
  pipelineStatus,
  onStartPipeline,
  onStopPipeline,
}) {
  const [streamStateOverride, setStreamStateOverride] = useState(null);
  const [streamReasonOverride, setStreamReasonOverride] = useState(null);
  const [isActionPending, setIsActionPending] = useState(false);
  const [actionError, setActionError] = useState(null);

  const testCam = isTestCamera(camera);

  // Reset stream overrides when selected camera changes
  useEffect(() => {
    setStreamStateOverride(null);
    setStreamReasonOverride(null);
    setActionError(null);
  }, [camera?.id]);

  if (!camera) {
    return (
      <div className="selected-camera-panel empty-focus">
        <div className="empty-focus-message">
          <h3>NO ACTIVE CAMERA SELECTED</h3>
          <p>Select a camera node from the GIS Map or Camera Matrix to engage live monitoring.</p>
        </div>
      </div>
    );
  }

  // Resolve distinct Video Stream State and AI Pipeline State
  const isStreaming = streamStateOverride === 'LIVE';
  const previewError = streamReasonOverride;
  const videoState = resolveVideoState(camera, previewError, isStreaming);
  const aiState = resolveAiState(camera, pipelineStatus);

  const isAiRunning = aiState.state === 'RUNNING';
  const isAiStarting = aiState.state === 'STARTING';
  const isAiStopping = aiState.state === 'STOPPING';
  const disableAiActions = isActionPending || isAiStarting || isAiStopping || testCam;

  const handleStart = async () => {
    if (!onStartPipeline || testCam) return;
    setIsActionPending(true);
    setActionError(null);
    try {
      await onStartPipeline(camera.id);
    } catch (err) {
      setActionError(formatHumanReadableError(err.message || 'Failed to start AI pipeline.'));
    } finally {
      setIsActionPending(false);
    }
  };

  const handleStop = async () => {
    if (!onStopPipeline || testCam) return;
    setIsActionPending(true);
    setActionError(null);
    try {
      await onStopPipeline(camera.id);
    } catch (err) {
      setActionError(formatHumanReadableError(err.message || 'Failed to stop AI pipeline.'));
    } finally {
      setIsActionPending(false);
    }
  };

  const handleStreamStatusChange = (status, reason) => {
    setStreamStateOverride(status);
    setStreamReasonOverride(reason);
  };

  // Technical Telemetry
  const streamProps = pipelineStatus?.stream_health?.properties;
  const codec = streamProps?.codec || pipelineStatus?.codec || null;
  const resolution =
    streamProps?.width && streamProps?.height
      ? `${streamProps.width}x${streamProps.height}`
      : pipelineStatus?.resolution || null;
  const lastPtsMs = pipelineStatus?.stream_health?.last_pts_ms ?? pipelineStatus?.last_processed_pts_ms ?? null;
  const locationText =
    camera.location ||
    camera.location_description ||
    (camera.district ? `${camera.district}` : 'Unknown / Unmapped');

  // WHEP proxy endpoint
  const whepUrl = typeof cameraService.getWhepProxyUrl === 'function'
    ? cameraService.getWhepProxyUrl(camera.id)
    : `/api/cameras/${camera.id}/whep`;

  // Determine active error reasons
  const activeErrorReason =
    actionError ||
    (aiState.state === 'ERROR' ? aiState.reason : null) ||
    (videoState.state !== 'LIVE' && videoState.state !== 'STANDBY' ? videoState.reason : null);

  return (
    <div className="selected-camera-panel" data-testid="selected-camera-panel">
      {/* 1. MONITOR HEADER */}
      <div className="panel-header">
        <div className="camera-id-block">
          <span className="camera-code-tag">
            STAGE: {camera.camera_code}
            {testCam && <span className="test-node-badge">TEST NODE</span>}
          </span>
          <span className="camera-name-title">{camera.name || `Camera ${camera.id}`}</span>
        </div>

        <div className="panel-header-meta">
          {/* VIDEO STREAM STATE */}
          <div className="state-badge-container">
            <span className="state-label">VIDEO:</span>
            <span className={`status-pill ${videoState.colorClass}`}>
              <span className="dot"></span>
              {videoState.state}
            </span>
          </div>

          {/* AI PIPELINE STATE */}
          <div className="state-badge-container">
            <span className="state-label">AI PIPELINE:</span>
            <span className={`status-pill ${aiState.colorClass}`}>
              <span className="dot"></span>
              {aiState.state}
            </span>
          </div>
        </div>
      </div>

      {/* 2. SUB-INFO BAR: Location, Coordinates & Vendor */}
      <div className="panel-subbar">
        <span className="meta-item">
          <strong>LOCATION:</strong> {locationText}
        </span>
        {camera.latitude != null && camera.longitude != null ? (
          <span className="meta-item">
            <strong>GPS:</strong> {camera.latitude.toFixed(4)}, {camera.longitude.toFixed(4)}
          </span>
        ) : (
          <span className="meta-item text-muted">GPS: UNAVAILABLE</span>
        )}
        {camera.vendor && (
          <span className="meta-item text-muted">
            <strong>DEPT:</strong> {camera.vendor}
          </span>
        )}
      </div>

      {/* 3. HUMAN-READABLE OPERATIONAL DIAGNOSTIC BANNER */}
      {activeErrorReason && (
        <div className="pipeline-diagnostic-banner" role="alert">
          <span className="diag-tag">
            {aiState.state === 'ERROR' ? '[AI PIPELINE ERROR]' : '[FEED STATUS]'}
          </span>
          <span className="diag-msg">
            <strong>REASON:</strong> {activeErrorReason}
          </span>
        </div>
      )}

      {/* 4. PRIMARY VIDEO VIEWPORT */}
      <div className="monitor-screen">
        <div className="hud-overlay top-left">
          <span className={`hud-live-tag ${videoState.colorClass}`}>
            ● {videoState.state === 'LIVE' ? 'VIDEO: LIVE FEED' : videoState.label}
          </span>
          <span className="hud-cam-id">FEED ID #{camera.camera_code || camera.id}</span>
        </div>
        <div className="hud-overlay top-right">
          {resolution && <span className="hud-spec-tag">{resolution}</span>}
          {codec && <span className="hud-spec-tag">{codec.toUpperCase()}</span>}
          <span className="hud-clock">{new Date().toLocaleTimeString()}</span>
        </div>

        <LivePreview
          webrtcUrl={whepUrl}
          isTestMode={testCam}
          onStreamStatusChange={handleStreamStatusChange}
        />
      </div>

      {/* 5. TELEMETRY & LIVE VEHICLE INTELLIGENCE DECK */}
      <div className="panel-footer">
        <div className="telemetry-stats">
          {/* Frame count */}
          {pipelineStatus?.frames_processed !== undefined && (
            <span className="stat-chip">
              FRAMES: <strong>{pipelineStatus.frames_processed}</strong>
            </span>
          )}

          {/* Detections count */}
          {pipelineStatus?.pipeline_stats?.frames_detected !== undefined && (
            <span className="stat-chip">
              DETECTIONS: <strong>{pipelineStatus.pipeline_stats.frames_detected}</strong>
            </span>
          )}

          {/* Tracked count */}
          {pipelineStatus?.pipeline_stats?.frames_tracked !== undefined && (
            <span className="stat-chip">
              TRACKED: <strong>{pipelineStatus.pipeline_stats.frames_tracked}</strong>
            </span>
          )}

          {/* ANPR reads count */}
          {pipelineStatus?.pipeline_stats?.frames_anpr !== undefined && (
            <span className="stat-chip">
              ANPR: <strong>{pipelineStatus.pipeline_stats.frames_anpr}</strong>
            </span>
          )}

          {/* Stream PTS */}
          {lastPtsMs !== null && (
            <span className="stat-chip">
              PTS: <strong>{typeof lastPtsMs === 'number' ? `${lastPtsMs.toFixed(0)} ms` : lastPtsMs}</strong>
            </span>
          )}

          {resolution && (
            <span className="stat-chip">
              RES: <strong>{resolution}</strong>
            </span>
          )}
          {codec && (
            <span className="stat-chip">
              CODEC: <strong>{codec.toUpperCase()}</strong>
            </span>
          )}
        </div>

        {/* AI Action Buttons */}
        <div className="pipeline-controls">
          <button
            type="button"
            className="btn btn-sm btn-primary btn-engage-ai"
            onClick={handleStart}
            disabled={disableAiActions || isAiRunning}
            title={testCam ? 'AI inference unavailable for test cameras' : 'Start YOLO + ANPR pipeline'}
          >
            {isActionPending && !isAiRunning ? 'Starting...' : 'Engage AI'}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-danger btn-stop-ai"
            onClick={handleStop}
            disabled={disableAiActions || !isAiRunning}
            title="Stop AI inference pipeline"
          >
            {isActionPending && isAiRunning ? 'Stopping...' : 'Stop AI'}
          </button>
        </div>
      </div>

      {/* 6. OPERATIONAL DETECTION INTELLIGENCE STRIP */}
      <div className="live-intelligence-strip">
        <div className="intelligence-header">
          <span className="intel-title">LATEST VEHICLE INTELLIGENCE</span>
          <span className="intel-sub">POSTGRESQL AUDIT PIPELINE</span>
        </div>
        <div className="intelligence-grid">
          <div className="intel-field">
            <span className="intel-label">RECOGNIZED PLATE:</span>
            <span className="intel-value plate-val">
              {pipelineStatus?.last_anpr?.plate_number || 'NONE DETECTED'}
            </span>
          </div>
          <div className="intel-field">
            <span className="intel-label">VEHICLE TYPE:</span>
            <span className="intel-value">
              {pipelineStatus?.last_anpr?.vehicle_type || 'VEHICLE'}
            </span>
          </div>
          <div className="intel-field">
            <span className="intel-label">CONFIDENCE:</span>
            <span className="intel-value">
              {pipelineStatus?.last_anpr?.confidence
                ? `${(pipelineStatus.last_anpr.confidence * 100).toFixed(1)}%`
                : 'NOT AVAILABLE'}
            </span>
          </div>
          <div className="intel-field">
            <span className="intel-label">COLOR:</span>
            <span className="intel-value text-muted">NOT AVAILABLE</span>
          </div>
          <div className="intel-field">
            <span className="intel-label">MAKE:</span>
            <span className="intel-value text-muted">NOT AVAILABLE</span>
          </div>
          <div className="intel-field">
            <span className="intel-label">MODEL:</span>
            <span className="intel-value text-muted">NOT AVAILABLE</span>
          </div>
        </div>
      </div>
    </div>
  );
}
