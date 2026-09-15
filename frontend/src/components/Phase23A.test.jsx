import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import App from '../App';
import AddCameraModal from './AddCameraModal';
import InvestigationWorkspace from './InvestigationWorkspace';
import RecordsWorkspace from './RecordsWorkspace';
import WatchlistManager from './WatchlistManager';
import HealthDashboard from './HealthDashboard';
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
    camera_code: 'cam01',
    name: 'Sector 1 Gate',
    location: 'Gandhinagar',
    latitude: 23.2156,
    longitude: 72.6369,
    status: 'online',
  },
  {
    id: 2,
    camera_code: 'cam02',
    name: 'Ring Road Junction',
    location: 'Ahmedabad',
    latitude: 23.0225,
    longitude: 72.5714,
    status: 'online',
  },
  {
    id: 3,
    camera_code: 'cam03',
    name: 'Highway Toll North',
    location: 'Vadodara',
    latitude: 22.3072,
    longitude: 73.1812,
    status: 'offline',
  },
];

describe('Phase 23A — C2 Command Center & Workspace Specifications', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    cameraService.getCameras.mockResolvedValue({
      cameras: mockCameras,
      total: 3,
    });
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValue({
      cam01: { status: 'RUNNING', frames_processed: 120, frames_detected: 45, frames_anpr: 12 },
      cam02: { status: 'STOPPED', frames_processed: 0 },
      cam03: { status: 'ERROR', error_message: 'RTSP 401 Unauthorized' },
    });
    cameraService.getPreviewUrl.mockResolvedValue({
      camera_id: 1,
      camera_code: 'cam01',
      webrtc_url: '/api/cameras/1/whep',
    });
    cameraService.startPipeline.mockResolvedValue({ status: 'RUNNING' });
    cameraService.stopPipeline.mockResolvedValue({ status: 'STOPPED' });
    cameraService.createCamera.mockResolvedValue({ id: 99, camera_code: 'cam99', name: 'New Cam' });

    analyticsService.getRecentEvents.mockResolvedValue({
      events: [
        {
          id: 501,
          camera_id: 1,
          camera_code: 'cam01',
          plate_number: 'KA02MM9091',
          timestamp: '2026-09-12T10:30:00Z',
          confidence: 0.94,
          pts: 14200,
          vehicle_type: 'car',
        },
      ],
      total: 1,
    });
    analyticsService.getVehicles.mockResolvedValue([
      {
        id: 10,
        plate_number: 'KA02MM9091',
        first_seen: '2026-09-10T08:00:00Z',
        last_seen: '2026-09-12T10:30:00Z',
        sighting_count: 7,
      },
    ]);
    analyticsService.getVehicleTimeline.mockResolvedValue({
      plate_number: 'KA02MM9091',
      sightings: [
        {
          id: 101,
          camera_id: 1,
          camera_code: 'cam01',
          camera_name: 'Sector 1 Gate',
          timestamp: '2026-09-12T10:30:00Z',
          latitude: 23.2156,
          longitude: 72.6369,
          confidence: 0.94,
          vehicle_type: 'car',
        },
      ],
    });

    alertService.getAlerts.mockResolvedValue({
      alerts: [
        {
          id: 301,
          alert_type: 'WATCHLIST_MATCH',
          severity: 'CRITICAL',
          timestamp: '2026-09-12T10:30:05Z',
          plate_number: 'KA02MM9091',
          is_acknowledged: false,
        },
      ],
      total: 1,
    });

    healthService.getSystemHealth.mockResolvedValue({
      anpr_persistence_worker: { is_alive: true, queue_size: 2 },
    });

    watchlistService.getWatchlist.mockResolvedValue([
      {
        id: 1,
        plate_number: 'KA02MM9091',
        description: 'Flagged vehicle of interest',
        severity: 'critical',
        is_active: true,
        updated_at: '2026-09-12T09:00:00Z',
      },
    ]);
  });  // 1. Surveillance Layout & Removal of Intelligence Tray
  it('1. Surveillance layout is clean and removes old bottom intelligence tray', async () => {
    const { container } = render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText('Sector 1 Gate').length).toBeGreaterThanOrEqual(1);
    });

    // Surveillance mode active by default
    expect(screen.getByRole('tab', { name: /SURVEILLANCE/i })).toHaveClass('active');

    // Confirm old bloated tray is absent
    expect(screen.queryByText(/INTELLIGENCE & OPERATIONS TRAY/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/PROTOCOL 4\.2/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/HIDE TRAY/i)).not.toBeInTheDocument();
  });

  // 2. Map Width Ratio (~35% left column, ~65% right CCTV column)
  it('2. Map width maintains ~35% left column and ~65% right CCTV workstation', async () => {
    const { container } = render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('gis-map-container')).toBeInTheDocument();
    });

    const leftCol = container.querySelector('.left-map-column');
    const rightCol = container.querySelector('.right-cctv-column');

    expect(leftCol).toBeInTheDocument();
    expect(rightCol).toBeInTheDocument();
  });

  // 3. Selected Camera: Focus monitor separation of video vs AI status
  it('3. Selected camera separates Video feed status from AI pipeline status', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('STAGE: cam01')).toBeInTheDocument();
    });

    // Video status vs AI pipeline status are clearly distinct
    expect(screen.getByText(/● VIDEO: LIVE FEED/i)).toBeInTheDocument();
    expect(screen.getByText('AI Status')).toBeInTheDocument();
    expect(screen.getAllByText(/RUNNING/i).length).toBeGreaterThanOrEqual(1);
  });

  // 4. Camera Wall: Compact tiles with selection indicator and AI status
  it('4. Camera wall renders compact tiles with camera code, name, and AI badge', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('camera-tile-1')).toBeInTheDocument();
      expect(screen.getByTestId('camera-tile-2')).toBeInTheDocument();
    });

    expect(screen.getByText('cam01')).toBeInTheDocument();
    expect(screen.getByText('cam02')).toBeInTheDocument();
    expect(screen.getByText('AI ON')).toBeInTheDocument();
    expect(screen.getByText('AI OFF')).toBeInTheDocument();
  });

  // 5. Map <-> Camera Synchronization
  it('5. Clicking camera tile synchronizes selected focus monitor and active stage', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText('cam02')).toBeInTheDocument();
    });

    const cam02Tile = screen.getByTestId('camera-tile-2');
    await act(async () => {
      fireEvent.click(cam02Tile);
    });

    expect(screen.getByText('STAGE: cam02')).toBeInTheDocument();
    expect(screen.getAllByText('Ring Road Junction').length).toBeGreaterThanOrEqual(1);
  });

  // 6. Investigation Target Input & Formatting
  it('6. Investigation workspace accepts target plate and forces uppercase', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    const plateInput = screen.getByPlaceholderText(/e\.g\. GJ05AB1234/i);
    expect(plateInput).toBeInTheDocument();

    await act(async () => {
      fireEvent.change(plateInput, { target: { value: 'ka02mm9091' } });
    });

    expect(plateInput.value).toBe('KA02MM9091');
  });

  // 7. Time Filter Behavior & PostgreSQL Search (with client-side filter label)
  it('7. Search queries PostgreSQL timeline and labels client-side time filtering honestly', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    const plateInput = screen.getByPlaceholderText(/e\.g\. GJ05AB1234/i);
    await act(async () => {
      fireEvent.change(plateInput, { target: { value: 'KA02MM9091' } });
    });

    const searchBtn = screen.getByRole('button', { name: /SEARCH EXISTING RECORDS/i });
    await act(async () => {
      fireEvent.click(searchBtn);
    });

    expect(analyticsService.getVehicleTimeline).toHaveBeenCalledWith('KA02MM9091');

    await waitFor(() => {
      expect(screen.getAllByText('KA02MM9091').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText(/Sightings/i).length).toBeGreaterThanOrEqual(1);
    });
  });

  // 8. Camera Checklist in Investigation (Select All / Clear All / Checkbox)
  it('8. Camera checklist allows scoping, Select All, and Clear', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    expect(screen.getByText('Cameras in scope')).toBeInTheDocument();

    const clearBtn = screen.getByRole('button', { name: /Clear/i });
    await act(async () => {
      fireEvent.click(clearBtn);
    });

    expect(screen.getByText(/0 of 3 cameras/i)).toBeInTheDocument();

    const allBtn = screen.getByRole('button', { name: /Select All/i });
    await act(async () => {
      fireEvent.click(allBtn);
    });

    expect(screen.getByText(/3 of 3 cameras/i)).toBeInTheDocument();
  });

  // 9. Engage AI on Selected Cameras
  it('9. Engage AI calls startPipeline for selected cameras only', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    const engageBtn = screen.getByRole('button', { name: /ENGAGE AI ON SELECTED/i });
    await act(async () => {
      fireEvent.click(engageBtn);
    });

    expect(cameraService.startPipeline).toHaveBeenCalledWith(1);
    expect(cameraService.startPipeline).toHaveBeenCalledWith(2);
    expect(cameraService.startPipeline).toHaveBeenCalledWith(3);
  });

  // 10. Stop AI on Selected Cameras
  it('10. Stop AI calls stopPipeline for selected cameras only', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    const stopBtn = screen.getByRole('button', { name: /STOP AI/i });
    await act(async () => {
      fireEvent.click(stopBtn);
    });

    expect(cameraService.stopPipeline).toHaveBeenCalledWith(1);
    expect(cameraService.stopPipeline).toHaveBeenCalledWith(2);
    expect(cameraService.stopPipeline).toHaveBeenCalledWith(3);
  });

  // 11. Records Workspace Tabs (Vehicles, Events, Alerts, Watchlist)
  it('11. Records workspace renders VEHICLES, EVENTS, ALERTS, and WATCHLIST tabs', async () => {
    render(<RecordsWorkspace onSelectPlate={vi.fn()} onSelectCamera={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /VEHICLES/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /EVENTS/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /ALERTS/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /WATCHLIST/i })).toBeInTheDocument();
      expect(screen.getByText('KA02MM9091')).toBeInTheDocument();
      expect(screen.getByText('7 sightings')).toBeInTheDocument();
    });

    // Switch to EVENTS tab
    const eventsTab = screen.getByRole('tab', { name: /EVENTS/i });
    await act(async () => {
      fireEvent.click(eventsTab);
    });

    await waitFor(() => {
      expect(screen.getByText('14200 ms')).toBeInTheDocument(); // PTS
      expect(screen.getByText('94.0%')).toBeInTheDocument(); // Confidence
    });
  });

  // 12. Watchlist Readability & Add Vehicle Button
  it('12. Watchlist renders clean, readable table layout with clear Add Vehicle button', async () => {
    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Watchlist/i })).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: /\+ ADD VEHICLE OF INTEREST/i })).toBeInTheDocument();
    expect(screen.getByText('KA02MM9091')).toBeInTheDocument();
    expect(screen.getByText('CRITICAL')).toBeInTheDocument();
  });

  // 13. Health Telemetry Readability
  it('13. Health dashboard renders structured telemetry and pipeline stats', async () => {
    render(<HealthDashboard />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /System Telemetry & Health/i })).toBeInTheDocument();
    });

    expect(screen.getByText('Saving detections')).toBeInTheDocument();
    expect(screen.getByText('Alive / Running')).toBeInTheDocument();
    expect(screen.getByText('Cameras')).toBeInTheDocument();
  });

  // 14. Add Camera Modal Validation
  it('14. Add Camera modal validates required fields', async () => {
    const { container } = render(<AddCameraModal isOpen={true} onClose={vi.fn()} onCameraAdded={vi.fn()} />);

    expect(screen.getByRole('heading', { name: /Add Camera/i })).toBeInTheDocument();

    const form = container.querySelector('form');
    await act(async () => {
      fireEvent.submit(form);
    });

    expect(screen.getByText(/Camera code is required/i)).toBeInTheDocument();
    expect(cameraService.createCamera).not.toHaveBeenCalled();
  });

  // 15. Credential Rejection Security
  it('15. Add Camera modal strictly rejects URLs containing user credentials', async () => {
    const { container } = render(<AddCameraModal isOpen={true} onClose={vi.fn()} onCameraAdded={vi.fn()} />);

    // Fill valid fields
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. CAM-031 or cam31/i), { target: { value: 'cam31' } });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. SG Highway - Pakwan Cross Road/i), { target: { value: 'North Gate' } });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. Ahmedabad, Surat, Vadodara/i), { target: { value: 'Sector 22' } });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. 23\.0305/i), { target: { value: '23.2156' } });
    fireEvent.change(screen.getByPlaceholderText(/e\.g\. 72\.5274/i), { target: { value: '72.6369' } });

    // Inject credentials into stream URL
    const streamInput = screen.getByPlaceholderText(/stream\/cam31/i);
    fireEvent.change(streamInput, { target: { value: 'rtsp://admin:secret123@103.250.160.189:8554/stream/cam31' } });

    const form = container.querySelector('form');
    await act(async () => {
      fireEvent.submit(form);
    });

    expect(screen.getByText(/SECURITY VIOLATION/i)).toBeInTheDocument();
    expect(cameraService.createCamera).not.toHaveBeenCalled();
  });
});
