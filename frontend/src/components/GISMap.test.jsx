import React from 'react';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import GISMap from './GISMap';
import { cameraService } from '../api/cameraService';

vi.mock('../api/cameraService', () => ({
  cameraService: {
    getCamerasMap: vi.fn(),
  }
}));

// Mock react-leaflet because jsdom doesn't support all leaflet DOM features
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map-container">{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
  Marker: ({ children, position }) => <div data-testid={`marker-${position[0]}-${position[1]}`}>{children}</div>,
  Popup: ({ children }) => <div data-testid="popup">{children}</div>,
  Polyline: ({ positions }) => <div data-testid="polyline" data-positions={JSON.stringify(positions)} />,
  useMap: () => ({ fitBounds: vi.fn() }),
}));

describe('GISMap Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test('renders loading state initially', () => {
    cameraService.getCamerasMap.mockReturnValue(new Promise(() => {})); // pending promise
    render(<GISMap />);
    expect(screen.getByText('Loading GIS Map...')).toBeInTheDocument();
  });

  test('renders empty state if no mapped cameras', async () => {
    cameraService.getCamerasMap.mockResolvedValue([]);
    render(<GISMap />);
    await waitFor(() => {
      expect(screen.getByText('No Mapped Cameras')).toBeInTheDocument();
    });
  });

  test('renders error state on API failure', async () => {
    cameraService.getCamerasMap.mockRejectedValue(new Error('API Down'));
    render(<GISMap />);
    await waitFor(() => {
      expect(screen.getByText('Error: API Down')).toBeInTheDocument();
    });
  });

  test('renders map with markers when cameras have valid coordinates', async () => {
    const mockCameras = [
      { id: 1, name: 'Cam 1', camera_code: 'C1', latitude: 40.7128, longitude: -74.0060, status: 'online' },
      { id: 2, name: 'Cam 2', camera_code: 'C2', latitude: 34.0522, longitude: -118.2437, status: 'offline' }
    ];
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);
    render(<GISMap />);
    
    await waitFor(() => {
      expect(screen.getByTestId('map-container')).toBeInTheDocument();
    });
    
    expect(screen.getByTestId('marker-40.7128--74.006')).toBeInTheDocument();
    expect(screen.getByTestId('marker-34.0522--118.2437')).toBeInTheDocument();
  });

  test('calls onSelectCamera when Focus Camera button is clicked in popup', async () => {
    const mockCameras = [
      { id: 10, name: 'Command Cam 10', camera_code: 'cam10', latitude: 23.02, longitude: 72.57, status: 'online' }
    ];
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);
    const onSelect = vi.fn();
    render(<GISMap selectedCameraId={null} onSelectCamera={onSelect} />);

    await waitFor(() => {
      expect(screen.getByTestId('map-container')).toBeInTheDocument();
    });

    const focusBtn = screen.getByText('Focus Camera');
    focusBtn.click();
    expect(onSelect).toHaveBeenCalledWith(10);
  });

  test('renders polyline when investigationPath is provided', async () => {
    const mockCameras = [
      { id: 1, name: 'Cam 1', camera_code: 'C1', latitude: 23.0, longitude: 72.0, status: 'online' }
    ];
    cameraService.getCamerasMap.mockResolvedValue(mockCameras);
    const mockPath = [
      { latitude: 23.0, longitude: 72.0, camera_id: 1 },
      { latitude: 23.1, longitude: 72.1, camera_id: 2 }
    ];
    render(<GISMap investigationPath={mockPath} />);

    await waitFor(() => {
      expect(screen.getByTestId('polyline')).toBeInTheDocument();
    });
  });
});

