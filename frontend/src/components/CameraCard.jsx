import React, { useState } from 'react';
import PipelineStatusBadge from './PipelineStatusBadge';
import LivePreview from './LivePreview';
import { cameraService } from '../api/cameraService';
import { isTestCamera, resolveVideoState, resolveAiState } from '../utils/cameraState';

export default function CameraCard({
  camera,
  pipelineStatus,
  onStart,
  onStop,
  isSelected = false,
  onSelect = null,
  compact = false,
}) {
  const [isPending, setIsPending] = useState(false);
  const [localError, setLocalError] = useState(null);

  const [showPreview, setShowPreview] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);

  const testCam = isTestCamera(camera);
  const video = resolveVideoState(camera, previewError, false);
  const ai = resolveAiState(camera, pipelineStatus);

  const isRunning = ai.state === 'RUNNING';
  const isStarting = ai.state === 'STARTING';
  const isStopping = ai.state === 'STOPPING';
  const disableActions = isPending || isStarting || isStopping || testCam;
  const isError = ai.state === 'ERROR';
  const pipelineError = pipelineStatus?.error_message;

  const handleStart = async () => {
    if (testCam) return;
    setIsPending(true);
    setLocalError(null);
    try {
      await onStart(camera.id);
    } catch (err) {
      setLocalError(err.message);
    } finally {
      setIsPending(false);
    }
  };

  const handleStop = async () => {
    if (testCam) return;
    setIsPending(true);
    setLocalError(null);
    try {
      await onStop(camera.id);
    } catch (err) {
      setLocalError(err.message);
    } finally {
      setIsPending(false);
    }
  };

  const handleViewLive = async () => {
    setShowPreview(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewUrl(null);
    try {
      const data = await cameraService.getPreviewUrl(camera.id);
      if (data.webrtc_url) {
        setPreviewUrl(data.webrtc_url);
      } else {
        setPreviewError('Live preview unavailable (no WebRTC URL)');
      }
    } catch (err) {
      setPreviewError(err.message || 'Failed to fetch preview details');
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleStopPreview = () => {
    setShowPreview(false);
    setPreviewUrl(null);
    setPreviewError(null);
  };

  if (compact) {
    const isOnline = video.state === 'LIVE';
    let videoDisplay = `VIDEO: ${video.state}`;
    let aiDisplay = isRunning ? 'AI ON' : (testCam ? 'AI UNAVAILABLE' : (isError ? 'AI ERR' : 'AI OFF'));
    
    // For test cameras
    if (testCam) {
      videoDisplay = 'VIDEO: OFFLINE';
      aiDisplay = 'AI UNAVAILABLE';
    }

    return (
      <div 
        className={`camera-tile ${isSelected ? 'selected' : ''} ${testCam ? 'tile-test-cam' : (isOnline ? 'online' : 'offline')}`}
        onClick={() => onSelect && onSelect(camera.id)}
        data-testid={`camera-tile-${camera.id}`}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect && onSelect(camera.id); }}
      >
        <div className="tile-top">
          <span className="tile-code">
            {camera.camera_code}
            {testCam && <span className="tile-tag-test">TEST</span>}
          </span>
          <span className={`tile-dot ${isOnline ? 'dot-online' : (video.state === 'AUTH REQUIRED' ? 'dot-warning' : 'dot-offline')}`}></span>
        </div>
        <div className="tile-name" title={camera.name || camera.camera_code}>
          {camera.name || `Camera ${camera.id}`}
        </div>
        <div className="tile-bottom">
          <span className={`tile-status-tag ${video.colorClass}`}>
            {videoDisplay}
          </span>
          <span className={`tile-ai-tag ${ai.colorClass}`}>
            {aiDisplay}
          </span>
        </div>
      </div>
    );
  }

  // Safe display for errors
  const displayError = localError || (isError ? pipelineError : null);

  return (
    <div className={`camera-card ${isSelected ? 'selected-card' : ''}`}>
      <div className="camera-header">
        <div className="camera-info">
          <h3>{camera.name || 'Unnamed Camera'}</h3>
          <span className="camera-code">{camera.camera_code}</span>
          {testCam && <span className="badge-test-cam">TEST CAMERA</span>}
        </div>
        <div className="camera-badges">
          <span className={`status-pill ${video.colorClass}`}>
            {video.label}
          </span>
          <PipelineStatusBadge status={pipelineStatus?.status || 'unknown'} />
        </div>
      </div>

      <div className="camera-details">
        <p><strong>Location:</strong> {camera.location || 'Unknown'}</p>
        <p><strong>RTSP Stream:</strong> {camera.stream_url ? 'Configured' : 'Missing'}</p>
        {camera.vendor && <p><strong>Department:</strong> {camera.vendor}</p>}
      </div>

      {displayError && (
        <div className="error-message" role="alert">
          {displayError}
        </div>
      )}

      {showPreview && (
        <div className="preview-container">
          {previewLoading && <div className="preview-loading">Connecting...</div>}
          {previewError && <div className="preview-error">{previewError}</div>}
          {previewUrl && !previewError && <LivePreview webrtcUrl={previewUrl} isTestMode={testCam} />}
          <button type="button" className="btn btn-secondary btn-sm" onClick={handleStopPreview}>
            Stop Preview
          </button>
        </div>
      )}

      <div className="camera-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={handleStart}
          disabled={disableActions || isRunning}
        >
          {isPending && !isRunning ? 'Processing...' : 'Start'}
        </button>
        <button
          type="button"
          className="btn btn-danger btn-sm"
          onClick={handleStop}
          disabled={disableActions || !isRunning}
        >
          {isPending && isRunning ? 'Processing...' : 'Stop'}
        </button>
        {!showPreview && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleViewLive}
            disabled={disableActions}
          >
            View Live
          </button>
        )}
      </div>
    </div>
  );
}
