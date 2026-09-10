import React from 'react';
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import App from '../App';
import { analyticsService } from '../api/analyticsService';
import { cameraService } from '../api/cameraService';
import { alertService } from '../api/alertService';

// Leaflet is stubbed so the operator view can be asserted under jsdom.
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map-container">{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
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
  useMap: () => ({ setView: vi.fn(), fitBounds: vi.fn(), getZoom: () => 7 }),
}));

vi.mock('../api/analyticsService', () => ({
  analyticsService: {
    getRecentEvents: vi.fn(),
    getVehicleTimeline: vi.fn(),
    getVehicles: vi.fn(),
    searchVehicles: vi.fn(),
  },
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

vi.mock('../api/alertService', () => ({
  alertService: {
    getAlerts: vi.fn(),
    acknowledgeAlert: vi.fn(),
  },
}));

const cameras = {
  cameras: [
    {
      id: 101,
      camera_code: 'CAM-A',
      name: 'Adajan Gate',
      location: 'Adajan',
      latitude: 21.1959,
      longitude: 72.7933,
      status: 'active',
    },
    {
      id: 102,
      camera_code: 'CAM-B',
      name: 'Athwa Gate',
      location: 'Athwa',
      latitude: 21.1702,
      longitude: 72.8311,
      status: 'active',
    },
  ],
  total: 2,
};

const events = {
  events: [
    {
      id: 1,
      camera_id: 101,
      vehicle_id: 55,
      event_type: 'anpr_detection',
      object_type: 'vehicle',
      confidence: 0.95,
      timestamp: '2026-09-10T09:00:00Z',
    },
  ],
  total: 1,
};

const vehicles = [{ id: 55, plate_number: 'GJ05AB1234' }];

const timelineResponse = {
  plate_number: 'GJ05AB1234',
  vehicle: { id: 55, plate_number: 'GJ05AB1234' },
  timeline: [
    {
      event_id: 11,
      camera_id: 101,
      camera_code: 'CAM-A',
      camera_name: 'Adajan Gate',
      location: 'Adajan',
      latitude: 21.1959,
      longitude: 72.7933,
      event_type: 'anpr_detection',
      confidence: 0.95,
      timestamp: '2026-09-10T09:00:00Z',
    },
    {
      event_id: 12,
      camera_id: 102,
      camera_code: 'CAM-B',
      camera_name: 'Athwa Gate',
      location: 'Athwa',
      latitude: 21.1702,
      longitude: 72.8311,
      event_type: 'anpr_detection',
      confidence: 0.9,
      timestamp: '2026-09-10T09:12:00Z',
    },
  ],
  total_detections: 2,
};

const hopMarkers = () =>
  screen
    .queryAllByTestId('marker')
    .filter((m) => m.getAttribute('data-icon-html').includes('sentinel-hop'));

const selectPlate = async () => {
  const plateBtn = await screen.findByRole('button', { name: 'Plate: GJ05AB1234' });
  await act(async () => {
    fireEvent.click(plateBtn);
  });
};

describe('Phase 13 map integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cameraService.getCameras.mockResolvedValue(cameras);
    cameraService.getPipelinesStatus.mockResolvedValue({});
    analyticsService.getRecentEvents.mockResolvedValue(events);
    analyticsService.getVehicles.mockResolvedValue(vehicles);
    analyticsService.getVehicleTimeline.mockResolvedValue(timelineResponse);
    alertService.getAlerts.mockResolvedValue({ alerts: [], total: 0 });
  });

  afterEach(() => {
    cleanup();
  });

  test('I1: cameras reach the map on load', async () => {
    render(<App />);

    await waitFor(() => {
      const cameraPins = screen
        .queryAllByTestId('marker')
        .filter((m) => m.getAttribute('data-icon-html').includes('sentinel-camera-pin'));
      expect(cameraPins).toHaveLength(2);
    });
    expect(screen.getByText('2 of 2 cameras mapped')).toBeInTheDocument();
  });

  test('I2: selecting a plate plots its cross-camera movement', async () => {
    render(<App />);
    await selectPlate();

    await waitFor(() => expect(screen.getByTestId('polyline')).toBeInTheDocument());
    expect(JSON.parse(screen.getByTestId('polyline').getAttribute('data-positions'))).toEqual([
      [21.1959, 72.7933],
      [21.1702, 72.8311],
    ]);
    expect(hopMarkers()).toHaveLength(2);
  });

  test('I3: the timeline is fetched once and shared with the map', async () => {
    render(<App />);
    await selectPlate();

    await waitFor(() => expect(screen.getByTestId('polyline')).toBeInTheDocument());
    expect(analyticsService.getVehicleTimeline).toHaveBeenCalledTimes(1);
    expect(analyticsService.getVehicleTimeline).toHaveBeenCalledWith('GJ05AB1234');
  });

  test('I4: clicking a timeline entry highlights that stop on the map', async () => {
    const { container } = render(<App />);
    await selectPlate();

    await waitFor(() => expect(hopMarkers()).toHaveLength(2));
    expect(hopMarkers()[1].getAttribute('data-icon-html')).not.toContain('sentinel-hop-selected');

    const entries = container.querySelector('.timeline-entries');
    await act(async () => {
      fireEvent.click(entries.children[1]);
    });

    await waitFor(() =>
      expect(hopMarkers()[1].getAttribute('data-icon-html')).toContain('sentinel-hop-selected')
    );
  });

  test('I5: clicking a map stop selects it, and clicking again clears it', async () => {
    render(<App />);
    await selectPlate();

    await waitFor(() => expect(hopMarkers()).toHaveLength(2));

    await act(async () => {
      fireEvent.click(hopMarkers()[0]);
    });
    await waitFor(() =>
      expect(hopMarkers()[0].getAttribute('data-icon-html')).toContain('sentinel-hop-selected')
    );

    await act(async () => {
      fireEvent.click(hopMarkers()[0]);
    });
    await waitFor(() =>
      expect(hopMarkers()[0].getAttribute('data-icon-html')).not.toContain('sentinel-hop-selected')
    );
  });

  test('I6: closing the timeline clears the path from the map', async () => {
    render(<App />);
    await selectPlate();

    await waitFor(() => expect(hopMarkers()).toHaveLength(2));

    await act(async () => {
      fireEvent.click(screen.getByLabelText('Close timeline'));
    });

    await waitFor(() => expect(hopMarkers()).toHaveLength(0));
    expect(screen.queryByTestId('polyline')).not.toBeInTheDocument();
  });

  test('I7: a timeline with no coordinates leaves the map usable', async () => {
    analyticsService.getVehicleTimeline.mockResolvedValue({
      ...timelineResponse,
      timeline: timelineResponse.timeline.map((entry) => ({
        ...entry,
        latitude: null,
        longitude: null,
      })),
    });

    render(<App />);
    await selectPlate();

    await waitFor(() =>
      expect(screen.getByText('No mapped detections for this vehicle.')).toBeInTheDocument()
    );
    expect(hopMarkers()).toHaveLength(0);
    expect(screen.getByTestId('map-container')).toBeInTheDocument();
  });
});
