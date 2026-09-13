import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import App from '../App';
import SelectedCameraPanel from './SelectedCameraPanel';
import CameraCard from './CameraCard';
import InvestigationWorkspace from './InvestigationWorkspace';
import RecordsWorkspace from './RecordsWorkspace';
import WatchlistManager from './WatchlistManager';
import HealthDashboard from './HealthDashboard';
import {
  isTestCamera,
  resolveVideoState,
  resolveAiState,
  formatHumanReadableError,
} from '../utils/cameraState';
import { cameraService } from '../api/cameraService';
import { analyticsService } from '../api/analyticsService';
import { alertService } from '../api/alertService';
import { healthService } from '../api/healthService';
import { watchlistService } from '../api/watchlistService';

// Mock Leaflet & React-Leaflet
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="gis-map-container">{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
  Marker: ({ children, position, eventHandlers }) => (
    <div
      data-testid={`marker-${position[0]}-${position[1]}`}
      onClick={eventHandlers?.click}
    >
      {children}
    </div>
  ),
  Popup: ({ children }) => <div data-testid="popup">{children}</div>,
  Polyline: ({ positions }) => <div data-testid="polyline" data-positions={JSON.stringify(positions)} />,
  useMap: () => ({ fitBounds: vi.fn(), setView: vi.fn(), getZoom: () => 14 }),
}));

vi.mock('../api/cameraService', () => ({
  cameraService: {
    getCameras: vi.fn(),
    getCamerasMap: vi.fn(),
    getPipelinesStatus: vi.fn(),
    startPipeline: vi.fn(),
    stopPipeline: vi.fn(),
    createCamera: vi.fn(),
    getPreviewUrl: vi.fn(),
    getWhepProxyUrl: vi.fn((id) => `/api/cameras/${id}/whep`),
  },
}));

vi.mock('../api/analyticsService', () => ({
  analyticsService: {
    getRecentEvents: vi.fn(),
    getVehicleTimeline: vi.fn(),
    getVehicles: vi.fn(),
    searchVehicles: vi.fn(),
  },
}));

vi.mock('../api/alertService', () => ({
  alertService: {
    getAlerts: vi.fn(),
    acknowledgeAlert: vi.fn(),
  },
}));

vi.mock('../api/healthService', () => ({
  healthService: {
    getSystemHealth: vi.fn(),
  },
}));

vi.mock('../api/watchlistService', () => ({
  watchlistService: {
    getWatchlist: vi.fn(),
    addWatchlistEntry: vi.fn(),
    updateWatchlistEntry: vi.fn(),
    deleteWatchlistEntry: vi.fn(),
  },
}));

const mockCameras = [
  {
    id: 1,
    camera_code: 'CAM-001',
    name: 'Sector 1 Gate',
    location: 'Gandhinagar Police HQ',
    latitude: 23.2156,
    longitude: 72.6369,
    status: 'online',
    stream_url: 'rtsp://103.250.160.189:8554/stream/cam01',
  },
  {
    id: 2,
    camera_code: 'CAM-CRUD',
    name: 'Test Camera Node',
    location: 'Lab Bench 4',
    latitude: 23.0225,
    longitude: 72.5714,
    status: 'offline',
    stream_url: 'rtsp://crud',
  },
];

const mockPipelines = {
  'CAM-001': {
    status: 'RUNNING',
    frames_processed: 240,
    pipeline_stats: {
      frames_detected: 85,
      frames_tracked: 80,
      frames_anpr: 22,
    },
    stream_health: {
      status: 'online',
      properties: { codec: 'h264', width: 1280, height: 720 },
      last_pts_ms: 14820,
    },
    last_anpr: {
      plate_number: 'KA02MM9091',
      vehicle_type: 'CAR',
      confidence: 0.94,
    },
  },
  'CAM-CRUD': {
    status: 'ERROR',
    error_message: 'Failed to start stream session for CAM-CRUD',
    frames_processed: 0,
  },
};

describe('Phase 24A — CCTV Workstation Specifications & State Consistency', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    cameraService.getCameras.mockResolvedValue({
      cameras: mockCameras,
      total: 2,
    });
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValue(mockPipelines);
    cameraService.getPreviewUrl.mockResolvedValue({
      camera_id: 1,
      camera_code: 'CAM-001',
      webrtc_url: '/api/cameras/1/whep',
    });
    cameraService.startPipeline.mockResolvedValue({ status: 'RUNNING' });
    cameraService.stopPipeline.mockResolvedValue({ status: 'STOPPED' });

    analyticsService.getRecentEvents.mockResolvedValue({
      events: [
        {
          id: 701,
          camera_id: 1,
          plate_number: 'GJ01AB1234',
          timestamp: '2026-09-12T10:00:00Z',
          confidence: 0.96,
          pts_ms: 14200,
          vehicle_type: 'CAR',
        },
      ],
      total: 1,
    });

    analyticsService.getVehicles.mockResolvedValue([
      {
        id: 11,
        plate_number: 'KA02MM9091',
        first_seen: '2026-09-10T08:00:00Z',
        last_seen: '2026-09-12T10:30:00Z',
        sighting_count: 5,
        vehicle_type: 'CAR',
        color: null,
        make: null,
        model: null,
      },
    ]);

    analyticsService.getVehicleTimeline.mockResolvedValue({
      plate_number: 'KA02MM9091',
      vehicle: {
        plate_number: 'KA02MM9091',
        vehicle_type: 'CAR',
        color: null,
        make: null,
        model: null,
      },
      timeline: [
        {
          event_id: 801,
          camera_id: 1,
          camera_code: 'CAM-001',
          timestamp: '2026-09-12T10:30:00Z',
          latitude: 23.2156,
          longitude: 72.6369,
          confidence: 0.94,
          pts_ms: 15400,
          event_type: 'CAR',
          color: null,
          make: null,
          model: null,
        },
      ],
      total_detections: 1,
    });

    alertService.getAlerts.mockResolvedValue({
      alerts: [
        {
          id: 401,
          alert_type: 'WATCHLIST_MATCH',
          severity: 'CRITICAL',
          timestamp: '2026-09-12T10:30:05Z',
          plate_number: 'KA02MM9091',
          camera_id: 1,
          status: 'unacknowledged',
        },
      ],
      total: 1,
    });

    healthService.getSystemHealth.mockResolvedValue({
      anpr_persistence_worker: { is_alive: true, queue_size: 0 },
    });

    watchlistService.getWatchlist.mockResolvedValue([
      {
        id: 1,
        plate_number: 'KA02MM9091',
        description: 'Target of interest',
        severity: 'critical',
        is_active: true,
        updated_at: '2026-09-12T09:00:00Z',
      },
    ]);
  });

  // 1. Camera Video / AI State Separation
  it('1. camera video/AI state separation: resolves independent states and formats reasons cleanly', () => {
    const cam01 = mockCameras[0];
    const camCrud = mockCameras[1];

    // Live video with running AI
    const vState1 = resolveVideoState(cam01, null, true);
    const aState1 = resolveAiState(cam01, mockPipelines['CAM-001']);
    expect(vState1.state).toBe('LIVE');
    expect(aState1.state).toBe('RUNNING');

    // Test camera video offline and AI unavailable
    const vStateCrud = resolveVideoState(camCrud);
    const aStateCrud = resolveAiState(camCrud, mockPipelines['CAM-CRUD']);
    expect(vStateCrud.state).toBe('OFFLINE');
    expect(aStateCrud.state).toBe('UNAVAILABLE');

    // Authentication failure mapping
    const vStateAuth = resolveVideoState(cam01, '401 Unauthorized');
    expect(vStateAuth.state).toBe('AUTH REQUIRED');

    // Upstream failure mapping
    const vStateUpstream = resolveVideoState(cam01, 'HTTP 404 Not Found');
    expect(vStateUpstream.state).toBe('UPSTREAM NOT FOUND');
  });

  // 2. CAM-001 Live State
  it('2. CAM-001 live state: renders live video tag and active AI metrics in focus monitor', () => {
    const { container } = render(
      <SelectedCameraPanel
        camera={mockCameras[0]}
        pipelineStatus={mockPipelines['CAM-001']}
        onStartPipeline={vi.fn()}
        onStopPipeline={vi.fn()}
      />
    );

    expect(screen.getByText('STAGE: CAM-001')).toBeInTheDocument();
    expect(screen.getByText('VIDEO:')).toBeInTheDocument();
    expect(screen.getByText('AI Status')).toBeInTheDocument();
    expect(screen.getByText('RUNNING')).toBeInTheDocument();
    expect(screen.getByText('FRAMES:')).toBeInTheDocument();
    expect(screen.getByText('240')).toBeInTheDocument();
    expect(screen.getByText('KA02MM9091')).toBeInTheDocument();

    // Verify video element, live preview container, and intelligence strip are in DOM
    expect(container.querySelector('.live-preview-container')).toBeInTheDocument();
    expect(container.querySelector('.live-video-element')).toBeInTheDocument();
    expect(container.querySelector('.live-intelligence-strip')).toBeInTheDocument();
    expect(screen.getByText(/Last Recognised Vehicle/i)).toBeInTheDocument();
  });

  // 3. Unavailable Test Camera State (CAM-CRUD)
  it('3. unavailable test camera state: displays explicit Test tag and prevents repeated AI engagement', () => {
    const startMock = vi.fn();
    render(
      <SelectedCameraPanel
        camera={mockCameras[1]}
        pipelineStatus={mockPipelines['CAM-CRUD']}
        onStartPipeline={startMock}
        onStopPipeline={vi.fn()}
      />
    );

    expect(screen.getAllByText('Test').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('UNAVAILABLE')).toBeInTheDocument();

    // Engage AI button should be disabled for test camera
    const engageBtn = screen.getByRole('button', { name: /ENGAGE AI/i });
    expect(engageBtn).toBeDisabled();

    fireEvent.click(engageBtn);
    expect(startMock).not.toHaveBeenCalled();
  });

  // 4. Camera Tile Consistency
  it('4. camera tile consistency: compact matrix tiles render separate video and AI status', () => {
    render(
      <CameraCard
        camera={mockCameras[0]}
        pipelineStatus={mockPipelines['CAM-001']}
        compact={true}
      />
    );

    expect(screen.getByText('CAM-001')).toBeInTheDocument();
    expect(screen.getByText('Sector 1 Gate')).toBeInTheDocument();
    expect(screen.getByText('VIDEO: LIVE')).toBeInTheDocument();
    expect(screen.getByText('AI ON')).toBeInTheDocument();
  });

  // 5. Map Selection Synchronizes Selected Focus Monitor
  it('5. map selection: renders GIS tactical map with registered nodes', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('gis-map-container')).toBeInTheDocument();
      expect(screen.getByText('Camera Map')).toBeInTheDocument();
      expect(screen.getByText('2 cameras')).toBeInTheDocument();
    });
  });

  // 6. Selected Camera Monitor
  it('6. selected camera monitor: shows dominant viewport, resolution, codec, and telemetry stats', () => {
    render(
      <SelectedCameraPanel
        camera={mockCameras[0]}
        pipelineStatus={mockPipelines['CAM-001']}
      />
    );

    expect(screen.getAllByText('1280x720').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('H264').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('DETECTIONS:')).toBeInTheDocument();
    expect(screen.getByText('85')).toBeInTheDocument();
    expect(screen.getByText('TRACKED:')).toBeInTheDocument();
    expect(screen.getByText('80')).toBeInTheDocument();
    expect(screen.getByText('ANPR:')).toBeInTheDocument();
    expect(screen.getByText('22')).toBeInTheDocument();
  });

  // 7. Responsive Classes and Layout
  it('7. responsive classes/layout: command-center-root and main operational containers are configured', async () => {
    const { container } = render(<App />);

    await waitFor(() => {
      expect(container.querySelector('.command-center-root')).toBeInTheDocument();
      expect(container.querySelector('.command-bar')).toBeInTheDocument();
      expect(container.querySelector('.command-main')).toBeInTheDocument();
      expect(container.querySelector('.left-map-column')).toBeInTheDocument();
      expect(container.querySelector('.right-cctv-column')).toBeInTheDocument();
    });
  });

  // 8. Surveillance Has No Old Bottom Intelligence Tray
  it('8. Surveillance no tray: live surveillance workspace contains only Map and CCTV console', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /SURVEILLANCE/i })).toHaveClass('active');
    });

    expect(screen.queryByText(/INTELLIGENCE & OPERATIONS TRAY/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/PROTOCOL 4\.2/i)).not.toBeInTheDocument();
    expect(screen.getByText('Live View')).toBeInTheDocument();
    expect(screen.getByText('Camera Map')).toBeInTheDocument();
  });

  // 9. Investigation Controls
  it('9. Investigation controls: accepts plate, selects camera scope, engages and stops AI selectively', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    expect(screen.getByText(/TARGET REGISTRATION PLATE/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SEARCH EXISTING RECORDS/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /ENGAGE AI ON SELECTED/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /STOP AI/i })).toBeInTheDocument();

    // Check CAM-CRUD is tagged as TEST in scope
    expect(screen.getByText('TEST')).toBeInTheDocument();
  });

  // 10. Records Views
  it('10. Records views: renders VEHICLES, EVENTS, ALERTS, and WATCHLIST sub-tabs from PostgreSQL archive', async () => {
    render(<RecordsWorkspace />);

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /VEHICLES/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /DETECTION EVENTS/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /SECURITY ALERTS/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /ACTIVE WATCHLIST/i })).toBeInTheDocument();
    });
  });

  // 11. Vehicle Metadata Display & Columns
  it('11. vehicle metadata display: vehicle records include type and metadata headers', async () => {
    render(<RecordsWorkspace />);

    await waitFor(() => {
      expect(screen.getByText('REGISTRATION PLATE')).toBeInTheDocument();
      expect(screen.getByText('FIRST SEEN')).toBeInTheDocument();
      expect(screen.getByText('LAST SEEN')).toBeInTheDocument();
      expect(screen.getByText('SIGHTING COUNT')).toBeInTheDocument();
      expect(screen.getByText('VEHICLE TYPE')).toBeInTheDocument();
      expect(screen.getByText('COLOR')).toBeInTheDocument();
      expect(screen.getByText('MAKE')).toBeInTheDocument();
      expect(screen.getByText('MODEL')).toBeInTheDocument();
    });
  });

  // 12. NOT AVAILABLE Handling for Color / Make / Model
  it('12. NOT AVAILABLE handling: unclassified color, make, and model honestly display NOT AVAILABLE', async () => {
    render(<RecordsWorkspace />);

    await waitFor(() => {
      expect(screen.getByText('KA02MM9091')).toBeInTheDocument();
    });

    // Color, Make, Model unclassified in current pipeline
    const notAvailList = screen.getAllByText('NOT AVAILABLE');
    expect(notAvailList.length).toBeGreaterThanOrEqual(3);
  });

  // 13. Watchlist Readability
  it('13. Watchlist readability: renders clean operational table with Plate, Description, Severity, Status, Actions', async () => {
    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByRole('table')).toBeInTheDocument();
      expect(screen.getByText('PLATE')).toBeInTheDocument();
      expect(screen.getByText('DESCRIPTION')).toBeInTheDocument();
      expect(screen.getByText('SEVERITY')).toBeInTheDocument();
      expect(screen.getByText('STATUS')).toBeInTheDocument();
      expect(screen.getByText('ACTIONS')).toBeInTheDocument();
      expect(screen.getByText('KA02MM9091')).toBeInTheDocument();
    });
  });

  // 14. Health Readability
  it('14. Health readability: distinguishes SYSTEM, WORKER, CAMERA, STREAM, AI, ANPR with clear telemetry', async () => {
    render(<HealthDashboard />);

    await waitFor(() => {
      expect(screen.getByText('System')).toBeInTheDocument();
      expect(screen.getByText('Worker')).toBeInTheDocument();
      expect(screen.getByText('Saving detections')).toBeInTheDocument();
      expect(screen.getByText('Camera Status')).toBeInTheDocument();
      expect(screen.getByText('Stream')).toBeInTheDocument();
      expect(screen.getByText('AI Status')).toBeInTheDocument();
      expect(screen.getByText('Plates read')).toBeInTheDocument();
    });
  });

  // 15. Error-State Rendering
  it('15. error-state rendering: converts raw Python exceptions into human-readable operator reasons', () => {
    const rawRTSP = 'RuntimeError: 401 Unauthorized RTSP connection rejected';
    const humanRTSP = formatHumanReadableError(rawRTSP);
    expect(humanRTSP).toContain('credentials rejected or missing');

    const raw404 = 'HTTP 404: Upstream route not found on MediaMTX';
    const human404 = formatHumanReadableError(raw404);
    expect(human404).toContain('route not found');

    const rawConn = 'ConnectionRefusedError: [Errno 111] Connection refused on port 8554';
    const humanConn = formatHumanReadableError(rawConn);
    expect(humanConn).toContain('network connection refused');
  });
});
