import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import App from '../App';
import { cameraService } from '../api/cameraService';
import { analyticsService } from '../api/analyticsService';
import { alertService } from '../api/alertService';
import { healthService } from '../api/healthService';

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
    getPreviewUrl: vi.fn(),
    getWhepProxyUrl: vi.fn((id) => `/api/cameras/${id}/whep`),
  }
}));

vi.mock('../api/analyticsService', () => ({
  analyticsService: {
    getRecentEvents: vi.fn(),
    getVehicleTimeline: vi.fn(),
    getVehicles: vi.fn(),
    searchVehicles: vi.fn(),
  }
}));

vi.mock('../api/alertService', () => ({
  alertService: {
    getAlerts: vi.fn(),
    acknowledgeAlert: vi.fn(),
  }
}));

vi.mock('../api/healthService', () => ({
  healthService: {
    getSystemHealth: vi.fn(),
  }
}));

// Generate 30 real-world format camera records (cam01 through cam30)
const generate30Cameras = () => {
  const list = [];
  for (let i = 1; i <= 30; i++) {
    const code = `cam${String(i).padStart(2, '0')}`;
    list.push({
      id: i,
      camera_code: code,
      name: `Junction Gate ${i}`,
      location: i <= 15 ? 'Ahmedabad Ring Road' : 'Surat Industrial Corridor',
      latitude: 23.0 + i * 0.01,
      longitude: 72.5 + i * 0.01,
      status: i % 4 === 0 ? 'offline' : 'online',
    });
  }
  return list;
};

const mock30Cameras = generate30Cameras();

describe('Phase 17B Command Center UI & 30-Camera Integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    cameraService.getCameras.mockResolvedValue({
      cameras: mock30Cameras,
      total: 30
    });
    cameraService.getCamerasMap.mockResolvedValue(mock30Cameras);
    cameraService.getPipelinesStatus.mockResolvedValue({
      'cam01': { status: 'RUNNING', frames_processed: 450 },
      'cam02': { status: 'STOPPED', frames_processed: 10 }
    });
    cameraService.getPreviewUrl.mockResolvedValue({
      camera_id: 1,
      camera_code: 'cam01',
      webrtc_url: '/api/cameras/1/whep'
    });
    analyticsService.getRecentEvents.mockResolvedValue({ events: [], total: 0 });
    analyticsService.getVehicles.mockResolvedValue([]);
    alertService.getAlerts.mockResolvedValue({ alerts: [], total: 0 });
    healthService.getSystemHealth.mockResolvedValue({
      anpr_persistence_worker: { is_alive: true, queue_size: 0 }
    });
  });

  it('1. command-center shell renders with branding and telemetry bar', async () => {
    await act(async () => {
      render(<App />);
    });

    expect(screen.getByText(/SENTINEL COMMAND CENTER/i)).toBeInTheDocument();
    expect(screen.getByText('GUJARAT POLICE')).toBeInTheDocument();
    expect(screen.getByText('30 REG')).toBeInTheDocument();
  });

  it('2. persistent GIS map and camera wall coexist simultaneously', async () => {
    await act(async () => {
      render(<App />);
    });

    expect(screen.getByTestId('gis-map-container')).toBeInTheDocument();
    expect(screen.getByText(/GRID MATRIX • CAMERA WALL/i)).toBeInTheDocument();
  });

  it('3. renders all 30 real camera records in the camera wall matrix', async () => {
    await act(async () => {
      render(<App />);
    });

    await waitFor(() => {
      expect(screen.getByText('cam01')).toBeInTheDocument();
      expect(screen.getByText('cam15')).toBeInTheDocument();
      expect(screen.getByText('cam30')).toBeInTheDocument();
    });

    // Check all 30 camera tiles exist in DOM
    for (let i = 1; i <= 30; i++) {
      const code = `cam${String(i).padStart(2, '0')}`;
      expect(screen.getByText(code)).toBeInTheDocument();
    }
  });

  it('4 & 5. camera tile selection updates selectedCameraId and focus viewer', async () => {
    await act(async () => {
      render(<App />);
    });

    await waitFor(() => {
      expect(screen.getByText('cam02')).toBeInTheDocument();
    });

    const cam02Tile = screen.getByTestId('camera-tile-2');
    await act(async () => {
      fireEvent.click(cam02Tile);
    });

    // Focus monitor should now be centered on cam02
    expect(screen.getByText('STAGE: cam02')).toBeInTheDocument();
    expect(screen.getAllByText('Junction Gate 2').length).toBeGreaterThanOrEqual(1);
  });

  it('6 & 7. selected camera viewer renders telemetry, status, and offline state', async () => {
    await act(async () => {
      render(<App />);
    });

    // cam04 is offline based on mock formula (i % 4 === 0)
    const cam04Tile = screen.getByTestId('camera-tile-4');
    await act(async () => {
      fireEvent.click(cam04Tile);
    });

    expect(screen.getByText('STAGE: cam04')).toBeInTheDocument();
    expect(screen.getAllByText('OFFLINE').length).toBeGreaterThan(0);
  });

  it('8. toggles between Surveillance and Investigation modes cleanly', async () => {
    await act(async () => {
      render(<App />);
    });

    const investBtn = screen.getByRole('tab', { name: /INVESTIGATION/i });
    await act(async () => {
      fireEvent.click(investBtn);
    });

    expect(screen.getByText(/TARGET REGISTRATION PLATE/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /SEARCH EXISTING RECORDS/i })).toBeInTheDocument();
    expect(screen.getByText(/ENGAGE AI ON SELECTED/i)).toBeInTheDocument();

    const survBtn = screen.getByRole('tab', { name: /SURVEILLANCE/i });
    await act(async () => {
      fireEvent.click(survBtn);
    });

    expect(screen.getByText(/GRID MATRIX • CAMERA WALL/i)).toBeInTheDocument();
  });

  it('9. strictly avoids credential leakage in the DOM or attributes', async () => {
    const { container } = render(<App />);

    await waitFor(() => {
      expect(screen.getByText('cam01')).toBeInTheDocument();
    });

    const domHtml = container.innerHTML;
    expect(domHtml).not.toContain('rtsp://');
    expect(domHtml).not.toContain('sentinel_password');
    expect(domHtml).not.toContain('Basic ');
    expect(domHtml).not.toContain('bearer ');
  });

  it('10 & 11. switches between Watchlist and Health workspaces cleanly', async () => {
    await act(async () => {
      render(<App />);
    });

    const watchlistBtn = screen.getByRole('button', { name: /WATCHLIST/i });
    await act(async () => {
      fireEvent.click(watchlistBtn);
    });

    expect(screen.getByRole('heading', { name: /Surveillance Watchlist/i })).toBeInTheDocument();

    const healthBtn = screen.getByRole('button', { name: /HEALTH/i });
    await act(async () => {
      fireEvent.click(healthBtn);
    });

    expect(screen.getByRole('heading', { name: /System Telemetry & Health/i })).toBeInTheDocument();

    const survBtn = screen.getByRole('tab', { name: /SURVEILLANCE/i });
    await act(async () => {
      fireEvent.click(survBtn);
    });

    expect(screen.getByText(/GRID MATRIX • CAMERA WALL/i)).toBeInTheDocument();
  });
});
