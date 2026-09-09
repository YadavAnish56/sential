import React, { useState } from 'react';
import PipelineStatusBadge from './PipelineStatusBadge';
import LivePreview from './LivePreview';
import { cameraService } from '../api/cameraService';

export default function CameraCard({ camera, pipelineStatus, onStart, onStop }) {
  const [isPending, setIsPending] = useState(false);
  const [localError, setLocalError] = useState(null);

  const [showPreview, setShowPreview] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);

  const statusStr = pipelineStatus?.status || 'unregistered';
  const isRunning = statusStr.toLowerCase() === 'running';
  const isStarting = statusStr.toLowerCase() === 'starting';
  const isStopping = statusStr.toLowerCase() === 'stopping';

  const disableActions = isPending || isStarting || isStopping;
  const isError = statusStr.toLowerCase() === 'error';
  const pipelineError = pipelineStatus?.error_message;

  const handleStart = async () => {
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

  // Safe display for errors
  const displayError = localError || (isError ? pipelineError : null);

  return (
    <div className="camera-card">
      <div className="camera-header">
        <div className="camera-info">
          <h3>{camera.name || 'Unnamed Camera'}</h3>
          <span className="camera-code">{camera.camera_code}</span>
          {camera.location && <span className="camera-location">{camera.location}</span>}
        </div>
        <PipelineStatusBadge status={statusStr} />
      </div>

      <div className="camera-stats">
        {pipelineStatus?.frames_processed !== undefined && (
          <p>Frames Processed: {pipelineStatus.frames_processed}</p>
        )}
      </div>

      {displayError && (
        <div className="camera-error">
          <p>{displayError}</p>
        </div>
      )}

      {showPreview && (
        <div className="camera-preview-section">
          {previewLoading && <div className="preview-status">Fetching preview details...</div>}
          {previewError && <div className="preview-error" style={{ color: '#ff4d4f', padding: '0.5rem 0' }}>{previewError}</div>}
          {previewUrl && <LivePreview webrtcUrl={previewUrl} />}
        </div>
      )}

      <div className="camera-actions" style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
        <button
          className="btn btn-primary"
          onClick={handleStart}
          disabled={disableActions || isRunning}
        >
          {isPending && !isRunning ? 'Processing...' : 'Start'}
        </button>
        <button
          className="btn btn-danger"
          onClick={handleStop}
          disabled={disableActions || (!isRunning && !isError)}
        >
          Stop
        </button>

        {!showPreview ? (
          <button className="btn btn-secondary" onClick={handleViewLive}>
            View Live
          </button>
        ) : (
          <button className="btn btn-secondary" onClick={handleStopPreview}>
            Stop Preview
          </button>
        )}
      </div>
    </div>
  );
}
