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
  // An investigation is only "running" once a plate has actually been searched.
  // Until then the investigator sees no camera feed, so nothing on screen
  // implies a sighting that was never looked up.
  const [investigationActive, setInvestigationActive] = useState(false);
  const [investigationCameraId, setInvestigationCameraId] = useState(null);
  // Which stop on the route the investigator has pinned, shared by the
  // sightings list and the map so the two stay in step.
  const [selectedSightingId, setSelectedSightingId] = useState(null);
  const [alertsCount, setAlertsCount] = useState(0);

  // Modal states
  const [isAddCameraOpen, setIsAddCameraOpen] = useState(false);

  // References for bounded retry control and unmount cleanup
  const camerasRef = useRef([]);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef(null);
  const loadCamerasRef = useRef(null);

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
            if (loadCamerasRef.current) loadCamerasRef.current();
          }, delay);
        } else {
          setCameraError('Camera service temporarily unavailable. Backend unreachable after multiple attempts.');
        }
      }
      return false;
    }
  }, []);

  // Kept in a ref so the bounded retry above can call back into the current
  // loadCameras without referencing it before it is initialised.
  useEffect(() => {
    loadCamerasRef.current = loadCameras;
  }, [loadCameras]);

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

  // Investigation keeps its own camera choice. The surveillance wall defaults to
  // the first camera on load, and inheriting that made a feed appear in
  // Investigation before the investigator had chosen anything.
  const handleSelectInvestigationCamera = useCallback((id) => {
    setInvestigationCameraId(id);
    setSelectedCameraId(id);
  }, []);

  const handleInvestigationSearchState = useCallback((active) => {
    setInvestigationActive(active);
    if (!active) {
      setInvestigationCameraId(null);
      setSelectedSightingId(null);
    }
  }, []);

  const handleSelectSighting = useCallback((sightingId) => {
    setSelectedSightingId(sightingId);
  }, []);

  // InvestigationWorkspace lists this in an effect's dependencies, so it has to
  // keep a stable identity. An inline arrow here re-ran that effect on every
  // render, which set state, which rendered again: the page locked up.
  const handleInvestigationPathChange = useCallback((route) => {
    setInvestigationRoute(route);
    // A fresh route invalidates any pinned stop from the previous one.
    setSelectedSightingId(null);
  }, []);

  const handlePlateSelect = (plate) => {
    setSelectedPlate(plate);
    setMode('investigation');
  };

  // After a removal the registry has changed: drop the selection if it pointed
  // at that camera and reload, so the wall and map stop showing it.
  const handleCameraRemoved = useCallback(async (removed) => {
    setSelectedCameraId((current) => (current === removed?.id ? null : current));
    await loadCameras();
    await pollPipelineStatus();
  }, [loadCameras, pollPipelineStatus]);

  const handleStartPipeline = async (id) => {
    await cameraService.startPipeline(id);
    await pollPipelineStatus();
  };

  const handleStopPipeline = async (id) => {
    await cameraService.stopPipeline(id);
    await pollPipelineStatus();
  };

  const selectedCamera = cameras.find((c) => c.id === selectedCameraId) || cameras[0] || null;
  const investigationCamera = investigationCameraId != null
    ? cameras.find((c) => c.id === investigationCameraId) || null
    : null;
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
                <span className="section-title">Camera Map</span>
                <span className="meta-tag">{cameras.length} cameras</span>
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
                    <span className="section-title">Live View</span>
                    <span className="meta-hint"></span>
                  </div>
                  <SelectedCameraPanel
                    camera={selectedCamera}
                    pipelineStatus={selectedPipelineStatus}
                    onStartPipeline={handleStartPipeline}
                    onStopPipeline={handleStopPipeline}
                    onCameraRemoved={handleCameraRemoved}
                    targetPlate={selectedPlate}
                  />
                </div>

                {/* Camera Wall & Live Detections Switcher */}
                <div className="camera-wall-section">
                  <div className="section-header section-header-with-switcher">
                    <span className="section-title">All Cameras ({cameras.length})</span>
                    <div className="surveillance-intel-switcher" role="tablist">
                      <button
                        type="button"
                        role="tab"
                        aria-selected={surveillanceView === 'wall'}
                        className={`intel-tab-btn ${surveillanceView === 'wall' ? 'active' : ''}`}
                        onClick={() => setSurveillanceView('wall')}
                      >
                        Cameras
                      </button>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={surveillanceView === 'detections'}
                        className={`intel-tab-btn ${surveillanceView === 'detections' ? 'active' : ''}`}
                        onClick={() => setSurveillanceView('detections')}
                      >
                        Detections
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
                <span className="section-title">Vehicle Route</span>
                <span className="meta-tag">
                  {investigationRoute.length > 0 ? `${investigationRoute.length} ${investigationRoute.length === 1 ? 'stop' : 'stops'}` : 'No route'}
                </span>
              </div>
              <div className="map-wrapper">
                <GISMap
                  selectedCameraId={selectedCameraId}
                  onSelectCamera={handleSelectCamera}
                  investigationPath={investigationRoute.length > 0 ? investigationRoute : null}
                  selectedSightingId={selectedSightingId}
                  onSelectSighting={handleSelectSighting}
                  height="100%"
                />
              </div>
            </section>

            {/* RIGHT COLUMN: DEDICATED INVESTIGATION WORKSPACE (~65% width) */}
            <section className="right-cctv-column investigation-stage-column">
              <InvestigationWorkspace
                cameras={cameras}
                selectedCameraId={investigationCameraId}
                onSelectCamera={handleSelectInvestigationCamera}
                pipelinesStatus={pipelinesStatus}
                onInvestigationPathChange={handleInvestigationPathChange}
                onRefreshPipelines={pollPipelineStatus}
                initialPlate={selectedPlate}
                onSearchStateChange={handleInvestigationSearchState}
                selectedSightingId={selectedSightingId}
                onSelectSighting={handleSelectSighting}
              />

              {/* Only after a search has run and a checkpoint has been picked */}
              {investigationActive && investigationCameraId != null && investigationCamera && (
                <div className="checkpoint-monitor-dock">
                  <div className="section-header">
                    <span className="section-title">Checkpoint • {investigationCamera.camera_code}</span>
                    <span className="meta-hint">{investigationCamera.name}</span>
                  </div>
                  <SelectedCameraPanel
                    camera={investigationCamera}
                    pipelineStatus={selectedPipelineStatus}
                    onStartPipeline={handleStartPipeline}
                    onStopPipeline={handleStopPipeline}
                    onCameraRemoved={handleCameraRemoved}
                    targetPlate={selectedPlate}
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
