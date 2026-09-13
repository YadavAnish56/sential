import React, { useState, useEffect, useCallback, useRef } from 'react';
import CameraList from './components/CameraList';
import SelectedCameraPanel from './components/SelectedCameraPanel';
import GISMap from './components/GISMap';
import LivePlateFeed from './components/LivePlateFeed';
import InvestigationWorkspace from './components/InvestigationWorkspace';
import RecordsWorkspace from './components/RecordsWorkspace';
import WatchlistManager from './components/WatchlistManager';
import HealthDashboard from './components/HealthDashboard';
import AddCameraModal from './components/AddCameraModal';
import { cameraService } from './api/cameraService';
import { alertService } from './api/alertService';
import './App.css';

export default function App() {
  const [mode, setMode] = useState('surveillance'); // 'surveillance' | 'investigation' | 'records' | 'watchlist' | 'health'
  const [surveillanceView, setSurveillanceView] = useState('wall'); // 'wall' | 'detections'
  const [cameras, setCameras] = useState([]);
  const [cameraError, setCameraError] = useState(null);
  const [selectedCameraId, setSelectedCameraId] = useState(null);
  const [pipelinesStatus, setPipelinesStatus] = useState({});
  const [selectedPlate, setSelectedPlate] = useState('');
  const [investigationRoute, setInvestigationRoute] = useState([]);
  const [alertsCount, setAlertsCount] = useState(0);

  // Modal states
  const [isAddCameraOpen, setIsAddCameraOpen] = useState(false);

  // References for bounded retry control and unmount cleanup
  const camerasRef = useRef([]);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef(null);

  // Fetch camera catalog with bounded retry (max 10 attempts with backoff)
  const loadCameras = useCallback(async () => {
    try {
      const data = await cameraService.getCameras();
      const rawList = Array.isArray(data) ? data : (data?.cameras || []);
      const cams = rawList.map((c) => ({
        ...c,
        id: c.id ?? c.camera_id,
        camera_code: c.camera_code ?? c.camera_id,
        status: (c.status || c.connectivity_status || 'offline').toLowerCase(),
      }));
      camerasRef.current = cams;
      setCameras(cams);
      setCameraError(null);
      retryCountRef.current = 0;
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      if (cams.length > 0) {
        setSelectedCameraId((prev) => (prev === null ? cams[0].id : prev));
      }
      return true;
    } catch (err) {
      console.error('Failed to load cameras in App:', err);
      // Retain existing camera state; never wipe previously loaded cameras
      if (camerasRef.current.length === 0) {
        const MAX_RETRIES = 10;
        if (retryCountRef.current < MAX_RETRIES) {
          retryCountRef.current += 1;
          const delay = Math.min(retryCountRef.current * 1000, 5000);
          if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
          retryTimerRef.current = setTimeout(() => {
            loadCameras();
          }, delay);
        } else {
          setCameraError('Camera service temporarily unavailable. Backend unreachable after multiple attempts.');
        }
      }
      return false;
    }
  }, []);

  // Poll pipelines status
  const pollPipelineStatus = useCallback(async () => {
    try {
      const data = await cameraService.getPipelinesStatus();
      setPipelinesStatus(data || {});
    } catch (err) {
      // Preserve last known telemetry on polling error
    }
  }, []);

  // Poll alerts count
  const pollAlerts = useCallback(async () => {
    try {
      const data = await alertService.getAlerts(1, 0);
      setAlertsCount(data?.total || 0);
    } catch (err) {
      // Ignored
    }
  }, []);

  useEffect(() => {
    loadCameras();
    pollPipelineStatus();
    pollAlerts();

    const interval = setInterval(() => {
      pollPipelineStatus();
      pollAlerts();
    }, 5000);

    return () => {
      clearInterval(interval);
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [loadCameras, pollPipelineStatus, pollAlerts]);

  const handleSelectCamera = (id) => {
    setSelectedCameraId(id);
  };

  const handlePlateSelect = (plate) => {
    setSelectedPlate(plate);
    setMode('investigation');
  };

  const handleStartPipeline = async (id) => {
    await cameraService.startPipeline(id);
    await pollPipelineStatus();
  };

  const handleStopPipeline = async (id) => {
    await cameraService.stopPipeline(id);
    await pollPipelineStatus();
  };

  const selectedCamera = cameras.find((c) => c.id === selectedCameraId) || cameras[0] || null;
  const selectedPipelineStatus = selectedCamera ? pipelinesStatus[selectedCamera.camera_code] : null;

  const onlineCount = cameras.filter((c) => c.status === 'online').length;
  const runningPipelines = Object.values(pipelinesStatus).filter(
    (p) => p.status === 'running' || p.status === 'RUNNING'
  ).length;

  return (
    <div className="command-center-root">
      {/* 1. TOP C2 COMMAND & STATUS BAR */}
      <header className="command-bar">
        <div className="brand-section">
          <div>
            <div className="brand-title">GUJARAT POLICE</div>
            <div className="brand-subtitle">SENTINEL COMMAND CENTER</div>
          </div>
        </div>

        {/* Operational Telemetry Chips */}
        <div className="telemetry-bar">
          <div className="telemetry-chip">
            <span className="dot dot-online"></span>
            <span>SYSTEM: <strong>ONLINE</strong></span>
          </div>
          <div className="telemetry-chip">
            <span>CAMERAS: <strong>{cameras.length} REG</strong> ({onlineCount} LIVE)</span>
          </div>
          <div className="telemetry-chip">
            <span>AI ENGINES: <strong>{runningPipelines} RUNNING</strong></span>
          </div>
          <button
            type="button"
            className="telemetry-chip alert-chip-clickable"
            onClick={() => setMode('records')}
            title="View Security Alerts in Records"
          >
            <span className={`dot ${alertsCount > 0 ? 'dot-warning' : 'dot-online'}`}></span>
            <span>ALERTS: <strong>{alertsCount} LOGGED</strong></span>
          </button>
        </div>

        {/* Unified Top Navigation */}
        <div className="mode-controls">
          <div className="nav-button-group" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'surveillance'}
              className={`nav-btn ${mode === 'surveillance' ? 'active' : ''}`}
              onClick={() => setMode('surveillance')}
            >
              SURVEILLANCE
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'investigation'}
              className={`nav-btn ${mode === 'investigation' ? 'active' : ''}`}
              onClick={() => setMode('investigation')}
            >
              INVESTIGATION
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'records'}
              className={`nav-btn ${mode === 'records' ? 'active' : ''}`}
              onClick={() => setMode('records')}
            >
              RECORDS
            </button>
            <button
              type="button"
              className={`nav-btn ${mode === 'watchlist' ? 'active' : ''}`}
              onClick={() => setMode('watchlist')}
            >
              WATCHLIST
            </button>
            <button
              type="button"
              className={`nav-btn ${mode === 'health' ? 'active' : ''}`}
              onClick={() => setMode('health')}
            >
              HEALTH
            </button>
          </div>

          <button
            type="button"
            className="btn btn-sm btn-primary add-cam-btn"
            onClick={() => setIsAddCameraOpen(true)}
            title="Register new camera node"
          >
            + ADD CAMERA
          </button>
        </div>
      </header>

      {/* 2. MAIN OPERATIONAL WORKSPACE */}
      <main className="command-main">
        {mode === 'surveillance' && (
          <>
            {/* LEFT COLUMN: PERSISTENT GIS WORKSTATION (~35% width) */}
            <section className="left-map-column">
              <div className="section-header">
                <span className="section-title">GIS TACTICAL SURVEILLANCE MAP</span>
                <span className="meta-tag">{cameras.length} REGISTERED NODES</span>
              </div>
              <div className="map-wrapper">
                <GISMap
                  selectedCameraId={selectedCameraId}
                  onSelectCamera={handleSelectCamera}
                  investigationPath={null}
                  height="100%"
                />
              </div>
            </section>

            {/* RIGHT COLUMN: CCTV WORKSPACE (~65% width) */}
            <section className="right-cctv-column">
              <div className="surveillance-console">
                {/* Selected Focus Monitor (Dominant Viewport ~68% height) */}
                <div className="focus-monitor-section">
                  <div className="section-header">
                    <span className="section-title">ACTIVE TARGET FOCUS MONITOR</span>
                    <span className="meta-hint">SECURE WHEP EGRESS</span>
                  </div>
                  <SelectedCameraPanel
                    camera={selectedCamera}
                    pipelineStatus={selectedPipelineStatus}
                    onStartPipeline={handleStartPipeline}
                    onStopPipeline={handleStopPipeline}
                  />
                </div>

                {/* Camera Wall & Live Detections Switcher */}
                <div className="camera-wall-section">
                  <div className="section-header section-header-with-switcher">
                    <span className="section-title">GRID MATRIX • CAMERA WALL ({cameras.length} NODES)</span>
                    <div className="surveillance-intel-switcher" role="tablist">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={surveillanceView === 'wall'}
                        className={`intel-tab-btn ${surveillanceView === 'wall' ? 'active' : ''}`}
                        onClick={() => setSurveillanceView('wall')}
                      >
                        CAMERA WALL
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={surveillanceView === 'detections'}
                        className={`intel-tab-btn ${surveillanceView === 'detections' ? 'active' : ''}`}
                        onClick={() => setSurveillanceView('detections')}
                      >
                        LIVE DETECTIONS
                      </button>
                    </div>
                  </div>
                  <div className="camera-wall-scroll">
                    {surveillanceView === 'wall' ? (
                      cameraError && cameras.length === 0 ? (
                        <div className="camera-service-error" role="alert" style={{ padding: '2rem', textAlign: 'center', color: '#EF4444' }}>
                          <p style={{ fontWeight: 600, marginBottom: '0.5rem' }}>{cameraError}</p>
                          <button
                            type="button"
                            className="btn btn-sm btn-outline"
                            onClick={() => {
                              retryCountRef.current = 0;
                              setCameraError(null);
                              loadCameras();
                            }}
                          >
                            Retry Connection
                          </button>
                        </div>
                      ) : (
                        <CameraList
                          compactMode={true}
                          selectedCameraId={selectedCameraId}
                          onSelectCamera={handleSelectCamera}
                          cameras={cameras}
                          pipelinesStatus={pipelinesStatus}
                        />
                      )
                    ) : (
                      <LivePlateFeed
                        onSelectPlate={handlePlateSelect}
                        selectedCameraId={selectedCameraId}
                        compactMode={true}
                      />
                    )}
                  </div>
                </div>
              </div>
            </section>
          </>
        )}

        {mode === 'investigation' && (
          <>
            {/* LEFT COLUMN: GIS MAP WITH RECONSTRUCTED ROUTE (~35% width) */}
            <section className="left-map-column">
              <div className="section-header">
                <span className="section-title">INCIDENT GIS RECONSTRUCTION</span>
                <span className="meta-tag">
                  {investigationRoute.length > 0 ? `${investigationRoute.length} CHECKPOINTS` : 'NO ROUTE'}
                </span>
              </div>
              <div className="map-wrapper">
                <GISMap
                  selectedCameraId={selectedCameraId}
                  onSelectCamera={handleSelectCamera}
                  investigationPath={investigationRoute.length > 0 ? investigationRoute : null}
                  height="100%"
                />
              </div>
            </section>

            {/* RIGHT COLUMN: DEDICATED INVESTIGATION WORKSPACE (~65% width) */}
            <section className="right-cctv-column investigation-stage-column">
              <InvestigationWorkspace
                cameras={cameras}
                selectedCameraId={selectedCameraId}
                onSelectCamera={handleSelectCamera}
                pipelinesStatus={pipelinesStatus}
                onInvestigationPathChange={setInvestigationRoute}
                onRefreshPipelines={pollPipelineStatus}
                initialPlate={selectedPlate}
              />

              {/* Checkpoint Focus Monitor when a camera is selected */}
              {selectedCamera && (
                <div className="checkpoint-monitor-dock">
                  <div className="section-header">
                    <span className="section-title">CHECKPOINT SURVEILLANCE STAGE • {selectedCamera.camera_code}</span>
                    <span className="meta-hint">{selectedCamera.name}</span>
                  </div>
                  <SelectedCameraPanel
                    camera={selectedCamera}
                    pipelineStatus={selectedPipelineStatus}
                    onStartPipeline={handleStartPipeline}
                    onStopPipeline={handleStopPipeline}
                  />
                </div>
              )}
            </section>
          </>
        )}

        {mode === 'records' && (
          <section className="full-workspace-column">
            <RecordsWorkspace
              onSelectPlate={handlePlateSelect}
              onSelectCamera={(camId) => {
                setSelectedCameraId(camId);
                setMode('surveillance');
              }}
            />
          </section>
        )}

        {mode === 'watchlist' && (
          <section className="full-workspace-column">
            <WatchlistManager />
          </section>
        )}

        {mode === 'health' && (
          <section className="full-workspace-column">
            <HealthDashboard lightTheme={true} />
          </section>
        )}
      </main>

      {/* 3. MODALS: ADD CAMERA */}
      {isAddCameraOpen && (
        <AddCameraModal
          isOpen={isAddCameraOpen}
          onClose={() => setIsAddCameraOpen(false)}
          onCameraCreated={() => {
            loadCameras();
          }}
        />
      )}
    </div>
  );
}
