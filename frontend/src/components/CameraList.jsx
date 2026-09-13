import React, { useState, useEffect, useRef } from 'react';
import { cameraService } from '../api/cameraService';
import CameraCard from './CameraCard';

export default function CameraList({
  selectedCameraId = null,
  onSelectCamera = null,
  compactMode = false,
  cameras: propCameras = null,
  pipelinesStatus: propPipelinesStatus = null,
}) {
  const isControlled = Array.isArray(propCameras);
  const [cameras, setCameras] = useState([]);
  const [pipelinesStatus, setPipelinesStatus] = useState({});
  const [loading, setLoading] = useState(!isControlled);
  const [error, setError] = useState(null);

  const fetchPipelinesInProgress = useRef(false);

  const fetchCamerasAndStatus = async () => {
    if (isControlled) return;
    try {
      setLoading(true);
      setError(null);
      const data = await cameraService.getCameras();
      const rawList = Array.isArray(data) ? data : (data?.cameras || []);
      const cams = rawList.map((c) => ({
        ...c,
        id: c.id ?? c.camera_id,
        camera_code: c.camera_code ?? c.camera_id,
        status: (c.status || c.connectivity_status || 'offline').toLowerCase(),
      }));
      setCameras(cams);
      
      await fetchPipelinesStatus();
    } catch (err) {
      setError(err.message || 'Failed to load cameras.');
    } finally {
      setLoading(false);
    }
  };

  const fetchPipelinesStatus = async () => {
    if (propPipelinesStatus !== null) return;
    if (fetchPipelinesInProgress.current) return;
    
    fetchPipelinesInProgress.current = true;
    try {
      const statusData = await cameraService.getPipelinesStatus();
      setPipelinesStatus(statusData || {});
    } catch (err) {
      // Preserve last known state; do not overwrite with empty or error out entirely for polling.
      console.error('Failed to poll pipeline status:', err);
    } finally {
      fetchPipelinesInProgress.current = false;
    }
  };

  useEffect(() => {
    fetchCamerasAndStatus();

    const intervalId = setInterval(() => {
      fetchPipelinesStatus();
    }, 5000);

    return () => clearInterval(intervalId);
  }, []);

  const handleStartPipeline = async (id) => {
    await cameraService.startPipeline(id);
    await fetchPipelinesStatus();
  };

  const handleStopPipeline = async (id) => {
    await cameraService.stopPipeline(id);
    await fetchPipelinesStatus();
  };

  const finalCameras = isControlled ? propCameras : cameras;
  const finalPipelines = propPipelinesStatus !== null ? propPipelinesStatus : pipelinesStatus;

  if (loading && !isControlled) {
    return <div className="loading">Loading cameras...</div>;
  }

  if (error && !isControlled && finalCameras.length === 0) {
    return <div className="error-banner">Error: {error}</div>;
  }

  return (
    <div className="camera-list-container">
      {finalCameras.length === 0 ? (
        <p className="empty-state">No cameras registered in the system.</p>
      ) : (
        <div className={compactMode ? "camera-wall-grid" : "camera-grid"}>
          {finalCameras.map((camera) => (
            <CameraCard
              key={camera.id}
              camera={camera}
              pipelineStatus={finalPipelines[camera.camera_code]}
              onStart={handleStartPipeline}
              onStop={handleStopPipeline}
              isSelected={camera.id === selectedCameraId || camera.camera_code === selectedCameraId}
              onSelect={onSelectCamera}
              compact={compactMode}
            />
          ))}
        </div>
      )}
    </div>
  );
}
