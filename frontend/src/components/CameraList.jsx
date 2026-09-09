import React, { useState, useEffect, useRef } from 'react';
import { cameraService } from '../api/cameraService';
import CameraCard from './CameraCard';

export default function CameraList() {
  const [cameras, setCameras] = useState([]);
  const [pipelinesStatus, setPipelinesStatus] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchPipelinesInProgress = useRef(false);

  const fetchCamerasAndStatus = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await cameraService.getCameras();
      setCameras(data.cameras || []);
      
      await fetchPipelinesStatus();
    } catch (err) {
      setError(err.message || 'Failed to load cameras.');
    } finally {
      setLoading(false);
    }
  };

  const fetchPipelinesStatus = async () => {
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

  if (loading) {
    return <div className="loading">Loading cameras...</div>;
  }

  if (error) {
    return <div className="error-banner">Error: {error}</div>;
  }

  return (
    <div className="camera-list-container">
      {cameras.length === 0 ? (
        <p className="empty-state">No cameras registered in the system.</p>
      ) : (
        <div className="camera-grid">
          {cameras.map((camera) => (
            <CameraCard
              key={camera.id}
              camera={camera}
              pipelineStatus={pipelinesStatus[camera.camera_code]}
              onStart={handleStartPipeline}
              onStop={handleStopPipeline}
            />
          ))}
        </div>
      )}
    </div>
  );
}
