import React from 'react';
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import CameraMap from './CameraMap';
import { cameraService } from '../api/cameraService';

// jsdom has no layout engine, so Leaflet itself is stubbed. The assertions below
// verify what Sentinel hands to Leaflet, which is the part we own.
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children, center, zoom }) => (
    <div
      data-testid="map-container"
      data-center={JSON.stringify(center)}
      data-zoom={zoom}
    >
      {children}
    </div>
  ),
  TileLayer: ({ url, attribution }) => (
    <div data-testid="tile-layer" data-url={url} data-attribution={attribution} />
  ),
  Marker: ({ children, position, icon, eventHandlers }) => (
    <div
      data-testid="marker"
      data-position={JSON.stringify(position)}
      data-icon-html={icon?.options?.html || ''}
      onClick={() => eventHandlers?.click && eventHandlers.click()}
    >
      {children}
    </div>
  ),
  Popup: ({ children }) => <div data-testid="popup">{children}</div>,
  Polyline: ({ positions }) => (
    <div data-testid="polyline" data-positions={JSON.stringify(positions)} />
  ),
  useMap: () => ({
    setView: vi.fn(),
    fitBounds: vi.fn(),
    getZoom: () => 7,
  }),
}));

vi.mock('../api/cameraService', () => ({
  cameraService: {
    getCameras: vi.fn(),
    getPipelinesStatus: vi.fn(),
    startPipeline: vi.fn(),
    stopPipeline: vi.fn(),
    getPreviewUrl: vi.fn(),
  },
}));

const geoCameras = {
  cameras: [
    {
      id: 1,
      camera_code: 'CAM-A',
      name: 'Adajan Gate',
      location: 'Adajan',
      latitude: 21.1959,
      longitude: 72.7933,
      status: 'active',
    },
    {
      id: 2,
      camera_code: 'CAM-B',
      name: 'Athwa Gate',
      location: 'Athwa',
      latitude: 21.1702,
      longitude: 72.8311,
      status: 'offline',
    },
  ],
  total: 2,
};

const timeline = [
  {
    event_id: 11,
    camera_id: 1,
    camera_code: 'CAM-A',
    camera_name: 'Adajan Gate',
    location: 'Adajan',
    latitude: 21.1959,
    longitude: 72.7933,
    event_type: 'anpr_detection',
    confidence: 0.91,
    timestamp: '2026-09-10T09:00:00Z',
  },
  {
    event_id: 12,
    camera_id: 2,
    camera_code: 'CAM-B',
    camera_name: 'Athwa Gate',
    location: 'Athwa',
    latitude: 21.1702,
    longitude: 72.8311,
    event_type: 'anpr_detection',
    confidence: 0.88,
    timestamp: '2026-09-10T09:10:00Z',
  },
];

const cameraMarkers = () =>
  screen
    .queryAllByTestId('marker')
    .filter((m) => m.getAttribute('data-icon-html').includes('sentinel-camera-pin'));

const hopMarkers = () =>
  screen
    .queryAllByTestId('marker')
    .filter((m) => m.getAttribute('data-icon-html').includes('sentinel-hop'));

const renderMap = async (props = {}) => {
  await act(async () => {
    render(<CameraMap {...props} />);
  });
};

describe('CameraMap', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test('M1: renders the map with a tile layer and the Gujarat default view', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap();

    const map = screen.getByTestId('map-container');
    expect(JSON.parse(map.getAttribute('data-center'))).toEqual([22.2587, 71.1924]);
    expect(map.getAttribute('data-zoom')).toBe('7');

    const tiles = screen.getByTestId('tile-layer');
    expect(tiles.getAttribute('data-url')).toContain('tile.openstreetmap.org');
    expect(tiles.getAttribute('data-attribution')).toContain('OpenStreetMap');
  });

  test('M2: renders one marker per geolocated camera, at its coordinates', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap();

    await waitFor(() => expect(cameraMarkers()).toHaveLength(2));
    expect(JSON.parse(cameraMarkers()[0].getAttribute('data-position'))).toEqual([
      21.1959, 72.7933,
    ]);
    expect(screen.getByText('2 of 2 cameras mapped')).toBeInTheDocument();
  });

  test('M3: cameras without valid coordinates produce no marker', async () => {
    cameraService.getCameras.mockResolvedValue({
      cameras: [
        geoCameras.cameras[0],
        { id: 3, camera_code: 'CAM-C', name: 'No Geo', latitude: null, longitude: null },
        { id: 4, camera_code: 'CAM-D', name: 'Bad Geo', latitude: 999, longitude: 72.8 },
        { id: 5, camera_code: 'CAM-E', name: 'Missing Geo' },
      ],
      total: 4,
    });
    await renderMap();

    await waitFor(() => expect(cameraMarkers()).toHaveLength(1));
    expect(screen.getByText('1 of 4 cameras mapped')).toBeInTheDocument();
  });

  test('M4: shows an empty state when nothing can be plotted', async () => {
    cameraService.getCameras.mockResolvedValue({ cameras: [], total: 0 });
    await renderMap();

    await waitFor(() =>
      expect(screen.getByText('No camera coordinates available to plot.')).toBeInTheDocument()
    );
    expect(cameraMarkers()).toHaveLength(0);
  });

  test('M5: surfaces an API failure without crashing', async () => {
    cameraService.getCameras.mockRejectedValue(new Error('Cameras down'));
    await renderMap();

    await waitFor(() =>
      expect(screen.getByText('Map error: Cameras down')).toBeInTheDocument()
    );
    expect(screen.getByTestId('map-container')).toBeInTheDocument();
  });

  test('M6: popup exposes camera name, code, location and status', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap();

    await waitFor(() => expect(screen.getByText('Adajan Gate')).toBeInTheDocument());
    expect(screen.getByText('Code: CAM-A')).toBeInTheDocument();
    expect(screen.getByText('Location: Adajan')).toBeInTheDocument();
    expect(screen.getByText('Status: active')).toBeInTheDocument();
    expect(screen.getByText('Status: offline')).toBeInTheDocument();
  });

  test('M7: draws the movement path in exact timeline order', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap({ plateNumber: 'GJ05AB1234', timeline });

    await waitFor(() => expect(screen.getByTestId('polyline')).toBeInTheDocument());
    expect(JSON.parse(screen.getByTestId('polyline').getAttribute('data-positions'))).toEqual([
      [21.1959, 72.7933],
      [21.1702, 72.8311],
    ]);
    expect(hopMarkers()).toHaveLength(2);
    expect(screen.getByText(/GJ05AB1234: 2 stops/)).toBeInTheDocument();
  });

  test('M8: numbers each stop in sequence', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap({ plateNumber: 'GJ05AB1234', timeline });

    await waitFor(() => expect(hopMarkers()).toHaveLength(2));
    expect(hopMarkers()[0].getAttribute('data-icon-html')).toContain('>1<');
    expect(hopMarkers()[1].getAttribute('data-icon-html')).toContain('>2<');
    expect(screen.getByText('Stop 1 of 2')).toBeInTheDocument();
  });

  test('M9: timeline entries without coordinates are skipped, path stays connected', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    const gappedTimeline = [
      timeline[0],
      {
        ...timeline[0],
        event_id: 99,
        camera_code: 'CAM-NOGEO',
        latitude: null,
        longitude: null,
      },
      timeline[1],
    ];
    await renderMap({ plateNumber: 'GJ05AB1234', timeline: gappedTimeline });

    await waitFor(() => expect(hopMarkers()).toHaveLength(2));
    expect(JSON.parse(screen.getByTestId('polyline').getAttribute('data-positions'))).toEqual([
      [21.1959, 72.7933],
      [21.1702, 72.8311],
    ]);
  });

  test('M10: a single detection renders a marker but no degenerate polyline', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap({ plateNumber: 'GJ05AB1234', timeline: [timeline[0]] });

    await waitFor(() => expect(hopMarkers()).toHaveLength(1));
    expect(screen.queryByTestId('polyline')).not.toBeInTheDocument();
    expect(screen.getByText(/GJ05AB1234: 1 stop/)).toBeInTheDocument();
  });

  test('M11: a vehicle with no mappable detections reports that plainly', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap({ plateNumber: 'GJ05AB1234', timeline: [] });

    await waitFor(() =>
      expect(screen.getByText('No mapped detections for this vehicle.')).toBeInTheDocument()
    );
    expect(hopMarkers()).toHaveLength(0);
  });

  test('M12: clicking a stop reports the event back to the operator view', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    const onSelectEvent = vi.fn();
    await renderMap({ plateNumber: 'GJ05AB1234', timeline, onSelectEvent });

    await waitFor(() => expect(hopMarkers()).toHaveLength(2));
    fireEvent.click(hopMarkers()[1]);
    expect(onSelectEvent).toHaveBeenCalledWith(12);
  });

  test('M13: the selected stop is highlighted', async () => {
    cameraService.getCameras.mockResolvedValue(geoCameras);
    await renderMap({ plateNumber: 'GJ05AB1234', timeline, selectedEventId: 12 });

    await waitFor(() => expect(hopMarkers()).toHaveLength(2));
    expect(hopMarkers()[0].getAttribute('data-icon-html')).not.toContain('sentinel-hop-selected');
    expect(hopMarkers()[1].getAttribute('data-icon-html')).toContain('sentinel-hop-selected');
  });

  test('M14: never leaks stream URLs onto the map', async () => {
    cameraService.getCameras.mockResolvedValue({
      cameras: [{ ...geoCameras.cameras[0], stream_url: 'rtsp://user:pass@10.0.0.1/stream' }],
      total: 1,
    });
    const { container } = render(<CameraMap />);
    await waitFor(() => expect(cameraMarkers()).toHaveLength(1));
    expect(container.innerHTML).not.toContain('rtsp');
  });
});
