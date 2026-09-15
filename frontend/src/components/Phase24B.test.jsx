import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import App from '../App';
import LivePlateFeed from './LivePlateFeed';
import GISMap from './GISMap';
import InvestigationWorkspace from './InvestigationWorkspace';
import RecordsWorkspace from './RecordsWorkspace';
import { cameraService } from '../api/cameraService';
import { analyticsService } from '../api/analyticsService';
import { alertService } from '../api/alertService';
import { watchlistService } from '../api/watchlistService';

// Mock Leaflet & React-Leaflet
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="gis-map-container">{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
  Marker: ({ children, position, eventHandlers, 'data-testid': testId }) => (
    <div
      data-testid={testId || `marker-${position[0]}-${position[1]}`}
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
    location: 'Gandhinagar Police HQ',
    latitude: 23.2156,
    longitude: 72.6369,
    status: 'online',
  },
  {
    id: 2,
    camera_code: 'cam02',
    name: 'Ring Road Junction',
    location: 'Ahmedabad Bypass',
    latitude: 23.0225,
    longitude: 72.5714,
    status: 'online',
  },
  {
    id: 3,
    camera_code: 'cam03',
    name: 'Test Node Bench',
    location: 'Lab',
    latitude: 22.3072,
    longitude: 73.1812,
    status: 'offline',
  },
];

const mockEvents = [
  {
    id: 101,
    camera_id: 1,
    camera_code: 'cam01',
    plate_number: 'GJ01AB1234',
    timestamp: '2026-09-12T10:15:00Z',
    confidence: 0.965,
    pts: 14200,
    vehicle_type: 'car',
  },
  {
    id: 102,
    camera_id: 2,
    camera_code: 'cam02',
    plate_number: 'KA02MM9091',
    timestamp: '2026-09-12T10:20:00Z',
    confidence: 0.923,
    pts: 17400,
    vehicle_type: 'suv',
  },
];

const mockWatchlist = [
  {
    id: 1,
    plate_number: 'KA02MM9091',
    description: 'Vehicle of interest',
    severity: 'critical',
    is_active: true,
  },
];

describe('Phase 24B — Tripartite C2 Operational Workflows & Specifications', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    cameraService.getCameras.mockResolvedValue({
      cameras: mockCameras,
      total: 3,
    });
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValue({
      cam01: { status: 'RUNNING', frames_processed: 120 },
      cam02: { status: 'STOPPED' },
      cam03: { status: 'OFF' },
    });
    cameraService.startPipeline.mockResolvedValue({ status: 'RUNNING' });
    cameraService.stopPipeline.mockResolvedValue({ status: 'STOPPED' });

    analyticsService.getRecentEvents.mockResolvedValue({
      events: mockEvents,
      total: 2,
    });
    analyticsService.getVehicles.mockResolvedValue([
      {
        id: 1,
        plate_number: 'GJ01AB1234',
        first_seen: '2026-09-10T08:00:00Z',
        last_seen: '2026-09-12T10:15:00Z',
        sighting_count: 5,
        vehicle_type: 'car',
        make: null,
        model: null,
        color: null,
      },
      {
        id: 2,
        plate_number: 'KA02MM9091',
        first_seen: '2026-09-11T09:00:00Z',
        last_seen: '2026-09-12T10:20:00Z',
        sighting_count: 3,
        vehicle_type: 'suv',
      },
    ]);
    analyticsService.getVehicleTimeline.mockResolvedValue({
      plate_number: 'KA02MM9091',
      vehicle: {
        plate_number: 'KA02MM9091',
        vehicle_type: 'suv',
        first_seen: '2026-09-11T09:00:00Z',
        last_seen: '2026-09-12T10:20:00Z',
      },
      timeline: [
        {
          event_id: 101,
          camera_id: 1,
          camera_code: 'cam01',
          camera_name: 'Sector 1 Gate',
          location: 'Gandhinagar Police HQ',
          latitude: 23.2156,
          longitude: 72.6369,
          timestamp: '2026-09-12T10:15:00Z',
          confidence: 0.94,
          pts: 14200,
          vehicle_type: 'suv',
        },
        {
          event_id: 102,
          camera_id: 2,
          camera_code: 'cam02',
          camera_name: 'Ring Road Junction',
          location: 'Ahmedabad Bypass',
          latitude: 23.0225,
          longitude: 72.5714,
          timestamp: '2026-09-12T10:20:00Z',
          confidence: 0.91,
          pts: 17400,
          vehicle_type: 'suv',
        },
      ],
    });

    alertService.getAlerts.mockResolvedValue({
      alerts: [
        {
          id: 501,
          alert_type: 'WATCHLIST_MATCH',
          severity: 'CRITICAL',
          timestamp: '2026-09-12T10:20:05Z',
          plate_number: 'KA02MM9091',
          camera_id: 2,
          status: 'unacknowledged',
        },
      ],
      total: 1,
    });
    alertService.acknowledgeAlert.mockResolvedValue({ id: 501, status: 'acknowledged' });

    watchlistService.getWatchlist.mockResolvedValue(mockWatchlist);
  });

  // 1. Live Detection Feed
  it('1. LivePlateFeed renders real detections with PLATE, CAMERA, LOCATION, PTS, and CONFIDENCE', async () => {
    render(<LivePlateFeed />);

    await waitFor(() => {
      expect(screen.getByText('GJ01AB1234')).toBeInTheDocument();
      expect(screen.getByText('KA02MM9091')).toBeInTheDocument();
    });

    expect(screen.getByText(/REAL-TIME ANPR DETECTION STREAM/i)).toBeInTheDocument();
    expect(screen.getByText(/14200 ms/i)).toBeInTheDocument();
    expect(screen.getByText(/96.5%/i)).toBeInTheDocument();
    expect(screen.getByText(/Gandhinagar Police HQ/i)).toBeInTheDocument();
  });

  // 2. Detection -> Investigation Navigation
  it('2. Clicking Investigate Target on a live detection invokes onSelectPlate', async () => {
    const onSelectPlate = vi.fn();
    render(<LivePlateFeed onSelectPlate={onSelectPlate} />);

    await waitFor(() => {
      expect(screen.getByText('GJ01AB1234')).toBeInTheDocument();
    });

    const investigateBtns = screen.getAllByRole('button', { name: /INVESTIGATE TARGET/i });
    expect(investigateBtns.length).toBeGreaterThanOrEqual(1);

    fireEvent.click(investigateBtns[0]);
    expect(onSelectPlate).toHaveBeenCalledWith('GJ01AB1234');
  });

  // 3. Camera Scope in Investigation
  it('3. InvestigationWorkspace allows selecting, clearing, and scoping cameras', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    expect(screen.getByText(/3 of 3 cameras/i)).toBeInTheDocument();

    const clearBtn = screen.getByRole('button', { name: /Clear/i });
    fireEvent.click(clearBtn);
    expect(screen.getByText(/0 of 3 cameras/i)).toBeInTheDocument();

    const selectAllBtn = screen.getByRole('button', { name: /Select All/i });
    fireEvent.click(selectAllBtn);
    expect(screen.getByText(/3 of 3 cameras/i)).toBeInTheDocument();
  });

  // 4. Engage AI on Selected Cameras Only
  it('4. Engage AI only starts pipelines on selected operational cameras', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    const engageBtn = screen.getByRole('button', { name: /Engage AI on Selected/i });
    await act(async () => {
      fireEvent.click(engageBtn);
    });

    // Skips test cam (cam03) and starts cam01 and cam02
    expect(cameraService.startPipeline).toHaveBeenCalledWith(1);
    expect(cameraService.startPipeline).toHaveBeenCalledWith(2);
    expect(cameraService.startPipeline).not.toHaveBeenCalledWith(3);
  });

  // 5. Stop AI on Selected Cameras Only
  it('5. Stop AI stops pipelines on selected cameras', async () => {
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={vi.fn()}
      />
    );

    const stopBtn = screen.getByRole('button', { name: /Stop AI/i });
    await act(async () => {
      fireEvent.click(stopBtn);
    });

    expect(cameraService.stopPipeline).toHaveBeenCalledWith(1);
    expect(cameraService.stopPipeline).toHaveBeenCalledWith(2);
  });

  // 6. Investigation Route Normalization ([lat, lon] and { latitude, longitude })
  it('6. GISMap normalizes both [lat, lon] arrays and { latitude, longitude } objects', async () => {
    // Array format: [[lat, lon], [lat, lon]]
    const { rerender } = render(
      <GISMap investigationPath={[[23.2156, 72.6369], [23.0225, 72.5714]]} />
    );

    await waitFor(() => {
      expect(screen.getByTestId('polyline')).toBeInTheDocument();
      expect(screen.getByTestId('checkpoint-marker-1')).toBeInTheDocument();
      expect(screen.getByTestId('checkpoint-marker-2')).toBeInTheDocument();
    });

    // Object format: [{ latitude, longitude }]
    rerender(
      <GISMap
        investigationPath={[
          { latitude: 23.2156, longitude: 72.6369, camera_code: 'cam01', index: 1 },
          { latitude: 23.0225, longitude: 72.5714, camera_code: 'cam02', index: 2 },
        ]}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('polyline')).toBeInTheDocument();
      expect(screen.getByTestId('checkpoint-marker-1')).toBeInTheDocument();
    });
  });

  // 7. Route Checkpoint Selection
  it('7. Clicking a checkpoint in investigation results focuses the corresponding camera', async () => {
    const onSelectCam = vi.fn();
    render(
      <InvestigationWorkspace
        cameras={mockCameras}
        selectedCameraId={1}
        onSelectCamera={onSelectCam}
        initialPlate="KA02MM9091"
      />
    );

    const searchBtn = screen.getByRole('button', { name: /SEARCH EXISTING RECORDS/i });
    await act(async () => {
      fireEvent.click(searchBtn);
    });

    await waitFor(() => {
      expect(screen.getByText('#1')).toBeInTheDocument();
      expect(screen.getByText('#2')).toBeInTheDocument();
    });

    const focusCamBtns = screen.getAllByRole('button', { name: /Focus Cam|Focusing/i });
    fireEvent.click(focusCamBtns[1]);
    expect(onSelectCam).toHaveBeenCalledWith(2);
  });

  // 8. Records Vehicles
  it('8. RecordsWorkspace renders stored Vehicles table with counts and metadata', async () => {
    render(<RecordsWorkspace />);

    await waitFor(() => {
      expect(screen.getByTestId('vehicles-table')).toBeInTheDocument();
      expect(screen.getByText('GJ01AB1234')).toBeInTheDocument();
      expect(screen.getByText('5 sightings')).toBeInTheDocument();
    });
  });

  // 9. Records Events
  it('9. RecordsWorkspace renders Detection Events table with actions', async () => {
    render(<RecordsWorkspace />);

    const eventsTab = screen.getByRole('tab', { name: /DETECTION EVENTS/i });
    await act(async () => {
      fireEvent.click(eventsTab);
    });

    await waitFor(() => {
      expect(screen.getByTestId('events-table')).toBeInTheDocument();
      expect(screen.getByText('CAM-1')).toBeInTheDocument();
      expect(screen.getAllByRole('button', { name: /Focus Cam/i }).length).toBeGreaterThanOrEqual(1);
    });
  });

  // 10. Records Alerts
  it('10. RecordsWorkspace renders Security Alerts and handles acknowledgement', async () => {
    render(<RecordsWorkspace />);

    const alertsTab = screen.getByRole('tab', { name: /SECURITY ALERTS/i });
    await act(async () => {
      fireEvent.click(alertsTab);
    });

    await waitFor(() => {
      expect(screen.getByTestId('alerts-table')).toBeInTheDocument();
      expect(screen.getByText('WATCHLIST_MATCH')).toBeInTheDocument();
    });

    const ackBtn = screen.getByRole('button', { name: /Acknowledge/i });
    await act(async () => {
      fireEvent.click(ackBtn);
    });

    expect(alertService.acknowledgeAlert).toHaveBeenCalledWith(501);
  });

  // 11. Historical Audit Archive
  it('11. RecordsWorkspace renders Historical Audit Archive with filters', async () => {
    render(<RecordsWorkspace />);

    const archiveTab = screen.getByRole('tab', { name: /HISTORICAL AUDIT ARCHIVE/i });
    await act(async () => {
      fireEvent.click(archiveTab);
    });

    await waitFor(() => {
      expect(screen.getByTestId('archive-filter-deck')).toBeInTheDocument();
      expect(screen.getByTestId('archive-table')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Export CSV/i })).toBeInTheDocument();
    });
  });

  // 12. CSV Export
  it('12. Clicking Export Audit Log triggers CSV download', async () => {
    render(<RecordsWorkspace />);

    const archiveTab = screen.getByRole('tab', { name: /HISTORICAL AUDIT ARCHIVE/i });
    await act(async () => {
      fireEvent.click(archiveTab);
    });

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Export CSV/i })).toBeInTheDocument();
    });

    // Mock URL and createElement for link download
    const createObjectURLSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-url');
    const revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const exportBtn = screen.getByRole('button', { name: /Export CSV/i });
    act(() => {
      fireEvent.click(exportBtn);
    });

    expect(createObjectURLSpy).toHaveBeenCalled();
    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
  });

  // 13. Watchlist Matching
  it('13. LivePlateFeed highlights detections matching active watchlist with WATCHLIST MATCH badge', async () => {
    render(<LivePlateFeed />);

    await waitFor(() => {
      expect(screen.getByText('KA02MM9091')).toBeInTheDocument();
    });

    expect(screen.getByTestId('watchlist-match-badge')).toBeInTheDocument();
    expect(screen.getByText('WATCHLIST MATCH')).toBeInTheDocument();
  });

  // 14. Missing Vehicle Attributes Shown as NOT AVAILABLE
  it('14. Missing vehicle attributes (make, model, color) are displayed as NOT AVAILABLE and never fabricated', async () => {
    render(<RecordsWorkspace />);

    await waitFor(() => {
      expect(screen.getByTestId('vehicles-table')).toBeInTheDocument();
    });

    // Multiple NOT AVAILABLE cells in table for make, model, and color
    const notAvailableCells = screen.getAllByText('NOT AVAILABLE');
    expect(notAvailableCells.length).toBeGreaterThanOrEqual(3);
  });
});
