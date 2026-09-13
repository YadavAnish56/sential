import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import GISMap from './GISMap';
import SelectedCameraPanel from './SelectedCameraPanel';
import CameraCard from './CameraCard';
import CameraList from './CameraList';
import HealthDashboard from './HealthDashboard';
import { cameraService } from '../api/cameraService';
import { healthService } from '../api/healthService';

vi.mock('../api/cameraService', () => ({
  cameraService: {
    getCameras: vi.fn(),
    getCamerasMap: vi.fn(),
    getPipelinesStatus: vi.fn(),
    getPreviewUrl: vi.fn(),
    getWhepProxyUrl: vi.fn((id) => `/api/cameras/${id}/whep`),
    startPipeline: vi.fn(),
    stopPipeline: vi.fn(),
  }
}));

vi.mock('../api/healthService', () => ({
  healthService: {
    getSystemHealth: vi.fn(),
  }
}));

// Mock react-leaflet to inspect TileLayer attributes and Marker positioning
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map-container">{children}</div>,
  TileLayer: ({ url, attribution }) => (
    <div data-testid="tile-layer" data-url={url} data-attribution={attribution} />
  ),
  Marker: ({ children, position, eventHandlers }) => (
    <div
      data-testid={`marker-${position[0]}-${position[1]}`}
      onClick={eventHandlers?.click}
      role="button"
    >
      {children}
    </div>
  ),
  Popup: ({ children }) => <div data-testid="popup">{children}</div>,
  Polyline: ({ positions }) => <div data-testid="polyline" data-positions={JSON.stringify(positions)} />,
  useMap: () => ({ fitBounds: vi.fn(), setView: vi.fn(), invalidateSize: vi.fn(), getZoom: () => 13 }),
}));

describe('Phase 21B — Command Center UI & Government Integration Verification', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  // 1. Map Tile Verification — Esri Dark Basemap, NO Carto API KEY REQUIRED
  it('1. GISMap uses legitimate Esri World Dark Gray basemap with no API key requirement', async () => {
    const mockCameras = [
      { id: 1, name: 'S.G. Highway 01', camera_code: 'cam01', latitude: 23.0225, longitude: 72.5714, status: 'online' }
    ];
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);

    render(<GISMap selectedCameraId={1} />);

    await waitFor(() => {
      expect(screen.getByTestId('map-container')).toBeInTheDocument();
    });

    const tileLayers = screen.getAllByTestId('tile-layer');
    expect(tileLayers.length).toBeGreaterThanOrEqual(1);

    // Verify all tile layer URLs point to Esri or legitimate public provider, NEVER Carto dark_all with API key error
    tileLayers.forEach((layer) => {
      const url = layer.getAttribute('data-url');
      const attr = layer.getAttribute('data-attribution');
      expect(url).not.toContain('cartocdn.com/dark_all');
      expect(url).toContain('arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray');
      expect(attr).toContain('Esri');
    });

    // Verify camera marker rendered
    expect(screen.getByTestId('marker-23.0225-72.5714')).toBeInTheDocument();
  });

  // 2. Map-to-Camera Selection Synchronization
  it('2. Clicking camera marker triggers onSelectCamera with correct camera ID', async () => {
    const mockCameras = [
      { id: 7, name: 'Ring Road 07', camera_code: 'cam07', latitude: 23.1, longitude: 72.6, status: 'online' }
    ];
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);
    const onSelect = vi.fn();

    render(<GISMap selectedCameraId={null} onSelectCamera={onSelect} />);

    await waitFor(() => {
      expect(screen.getByTestId('marker-23.1-72.6')).toBeInTheDocument();
    });

    const marker = screen.getByTestId('marker-23.1-72.6');
    fireEvent.click(marker);

    expect(onSelect).toHaveBeenCalledWith(7);
  });

  // 3. Camera Wall 30-Camera Dense Rendering
  it('3. Renders high-density camera matrix with registered cameras', async () => {
    const mock30 = Array.from({ length: 30 }, (_, i) => ({
      id: i + 1,
      camera_code: `cam${String(i + 1).padStart(2, '0')}`,
      name: `Junction ${i + 1}`,
      location: `Zone ${i + 1}`,
      status: i === 0 ? 'online' : 'offline',
    }));

    cameraService.getCameras.mockResolvedValue({ cameras: mock30, total: 30 });
    cameraService.getPipelinesStatus.mockResolvedValue({});

    render(<CameraList compactMode={true} selectedCameraId={1} onSelectCamera={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByTestId('camera-tile-1')).toBeInTheDocument();
      expect(screen.getByTestId('camera-tile-30')).toBeInTheDocument();
    });

    expect(screen.getByText('cam01')).toBeInTheDocument();
    expect(screen.getByText('cam30')).toBeInTheDocument();
  });

  // 4. Camera Tile Selection Synchronization
  it('4. Clicking camera tile invokes onSelectCamera callback', async () => {
    const onSelect = vi.fn();
    const cam = { id: 5, camera_code: 'cam05', name: 'Junction 5', status: 'online' };

    render(
      <CameraCard
        camera={cam}
        compact={true}
        isSelected={false}
        onSelect={onSelect}
      />
    );

    const tile = screen.getByTestId('camera-tile-5');
    fireEvent.click(tile);
    expect(onSelect).toHaveBeenCalledWith(5);
  });

  // 5. Selected Camera Panel — Dominant Viewport & HUD Telemetry
  it('5. SelectedCameraPanel displays HUD telemetry and coordinates or honest GPS UNAVAILABLE', () => {
    const camWithGps = {
      id: 1,
      camera_code: 'cam01',
      name: 'Ahmedabad Junction 01',
      location: 'SG Highway',
      latitude: 23.0225,
      longitude: 72.5714,
      status: 'online',
    };

    const { rerender } = render(
      <SelectedCameraPanel camera={camWithGps} pipelineStatus={{ status: 'running' }} />
    );

    expect(screen.getByText('STAGE: cam01')).toBeInTheDocument();
    expect(screen.getByText('FEED ID #cam01')).toBeInTheDocument();
    expect(screen.getByText(/23.0225, 72.5714/)).toBeInTheDocument();

    // Missing GPS shows honest GPS: UNAVAILABLE
    const camNoGps = {
      id: 2,
      camera_code: 'cam02',
      name: 'Surat Gate 02',
      latitude: null,
      longitude: null,
      status: 'offline',
    };

    rerender(<SelectedCameraPanel camera={camNoGps} pipelineStatus={null} />);
    expect(screen.getByText('GPS: UNAVAILABLE')).toBeInTheDocument();
  });

  // 6. Health Dashboard — Dark Command-Center Theme & Contrast
  it('6. HealthDashboard renders in dark command-center theme without white background cards', async () => {
    cameraService.getPipelinesStatus.mockResolvedValue({
      cam01: {
        camera_id: 'cam01',
        status: 'running',
        pipeline_stats: { frames_total: 250, frames_skipped: 5, frames_detected: 245, frames_tracked: 240, frames_anpr: 120, total_errors: 0 },
        stream_health: { status: 'online', last_pts_ms: 50000, consecutive_failures: 0, reconnect_attempts: 0, properties: { codec: 'h264', width: 1920, height: 1080 } }
      }
    });
    healthService.getSystemHealth.mockResolvedValue({
      anpr_persistence_worker: { is_alive: true, queue_size: 0 }
    });

    const { container } = render(<HealthDashboard />);

    await waitFor(() => {
      expect(screen.getByText('System Telemetry & Health')).toBeInTheDocument();
    });

    // Ensure the main container does not use #ffffff white background
    const dashboard = container.querySelector('.health-dashboard');
    expect(dashboard.style.background).not.toBe('rgb(255, 255, 255)');
    expect(dashboard.style.background).toBe('rgb(10, 15, 24)'); // #0a0f18

    // Ensure persistence worker panel is not white SaaS card
    const workerSection = container.querySelector('.system-health-section');
    expect(workerSection.style.background).not.toBe('rgb(248, 249, 250)');
    expect(workerSection.style.background).toBe('rgb(15, 23, 42)'); // #0f172a
  });
});
