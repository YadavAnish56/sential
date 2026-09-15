import React from 'react';
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import GISMap from './GISMap';
import InvestigationWorkspace from './InvestigationWorkspace';
import { cameraService } from '../api/cameraService';
import { analyticsService } from '../api/analyticsService';

/**
 * Keeping the sightings list and the route map in step.
 *
 * Picking a stop in the list highlights that stop on the map and the other way
 * round. A plate can pass the same camera more than once, so a stop is
 * identified by its event, not by its camera.
 */

vi.mock('../api/cameraService', () => ({
  cameraService: {
    getCamerasMap: vi.fn(),
    getCameras: vi.fn(),
    getPipelinesStatus: vi.fn(),
    startPipeline: vi.fn(),
    stopPipeline: vi.fn(),
  },
}));

vi.mock('../api/analyticsService', () => ({
  analyticsService: {
    getVehicleTimeline: vi.fn(),
  },
}));

// jsdom cannot run leaflet, so the map primitives are stubbed. Marker exposes
// its click handler and data attributes so selection can be asserted.
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map-container">{children}</div>,
  TileLayer: () => <div data-testid="tile-layer" />,
  Marker: ({ children, position, icon, eventHandlers, ...rest }) => (
    <div
      data-testid={rest['data-testid'] || `marker-${position[0]}-${position[1]}`}
      data-selected={rest['data-selected']}
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
  useMap: () => ({ setView: vi.fn(), fitBounds: vi.fn(), getZoom: () => 12 }),
}));

const cameras = [
  { id: 1, camera_code: 'CAM-001', name: 'Surat Highway', latitude: 21.1702, longitude: 72.8311, status: 'online' },
  { id: 2, camera_code: 'CAM-002', name: 'Bypass Junction', latitude: 21.2100, longitude: 72.8600, status: 'online' },
];

// The same camera appears twice: the vehicle passed it again later.
const route = [
  { event_id: 501, index: 1, camera_id: 1, camera_code: 'CAM-001', camera_name: 'Surat Highway', latitude: 21.1702, longitude: 72.8311, timestamp: '2026-09-10T09:00:00Z' },
  { event_id: 502, index: 2, camera_id: 2, camera_code: 'CAM-002', camera_name: 'Bypass Junction', latitude: 21.2100, longitude: 72.8600, timestamp: '2026-09-10T09:20:00Z' },
  { event_id: 503, index: 3, camera_id: 1, camera_code: 'CAM-001', camera_name: 'Surat Highway', latitude: 21.1702, longitude: 72.8311, timestamp: '2026-09-10T10:05:00Z' },
];

beforeEach(() => {
  vi.clearAllMocks();
  cameraService.getCamerasMap.mockResolvedValue(cameras);
  cameraService.getCameras.mockResolvedValue(cameras);
  cameraService.getPipelinesStatus.mockResolvedValue({});
  analyticsService.getVehicleTimeline.mockResolvedValue({
    plate_number: 'GJ05AB1234',
    total_detections: route.length,
    timeline: route,
  });
});

afterEach(cleanup);

async function renderMap(props = {}) {
  await act(async () => {
    render(<GISMap investigationPath={route} {...props} />);
  });
  await waitFor(() => expect(screen.getByTestId('map-container')).toBeInTheDocument());
}

describe('route map', () => {
  it('plots one checkpoint per stop, including a repeat visit', async () => {
    await renderMap();
    expect(screen.getByTestId('checkpoint-marker-1')).toBeInTheDocument();
    expect(screen.getByTestId('checkpoint-marker-2')).toBeInTheDocument();
    expect(screen.getByTestId('checkpoint-marker-3')).toBeInTheDocument();
  });

  it('draws the path through every stop', async () => {
    await renderMap();
    const positions = JSON.parse(screen.getByTestId('polyline').dataset.positions);
    expect(positions).toHaveLength(3);
  });

  it('marks only the pinned stop as selected', async () => {
    await renderMap({ selectedSightingId: 502 });
    expect(screen.getByTestId('checkpoint-marker-2').dataset.selected).toBe('true');
    expect(screen.getByTestId('checkpoint-marker-1').dataset.selected).toBe('false');
    expect(screen.getByTestId('checkpoint-marker-3').dataset.selected).toBe('false');
  });

  it('distinguishes two visits to one camera', async () => {
    // Stops 1 and 3 share a camera and coordinates; pinning one must not
    // light up the other.
    await renderMap({ selectedSightingId: 503 });
    expect(screen.getByTestId('checkpoint-marker-3').dataset.selected).toBe('true');
    expect(screen.getByTestId('checkpoint-marker-1').dataset.selected).toBe('false');
  });

  it('reports the stop that was clicked', async () => {
    const onSelectSighting = vi.fn();
    await renderMap({ onSelectSighting });

    fireEvent.click(screen.getByTestId('checkpoint-marker-2'));
    expect(onSelectSighting).toHaveBeenCalledWith(502);
  });

  it('clicking the pinned stop again clears it', async () => {
    const onSelectSighting = vi.fn();
    await renderMap({ selectedSightingId: 502, onSelectSighting });

    fireEvent.click(screen.getByTestId('checkpoint-marker-2'));
    expect(onSelectSighting).toHaveBeenCalledWith(null);
  });

  it('stays usable when the route has no coordinates', async () => {
    const noGps = route.map((p) => ({ ...p, latitude: null, longitude: null }));
    await act(async () => {
      render(<GISMap investigationPath={noGps} />);
    });
    await waitFor(() => expect(screen.getByTestId('map-container')).toBeInTheDocument());
    expect(screen.queryByTestId('checkpoint-marker-1')).not.toBeInTheDocument();
  });
});

describe('sightings list', () => {
  async function search() {
    await act(async () => {
      render(
        <InvestigationWorkspace
          cameras={cameras}
          initialPlate="GJ05AB1234"
          selectedSightingId={null}
          onSelectSighting={vi.fn()}
        />,
      );
    });
    const button = await screen.findByRole('button', { name: /SEARCH EXISTING RECORDS/i });
    await act(async () => {
      fireEvent.click(button);
    });
    await waitFor(() => expect(analyticsService.getVehicleTimeline).toHaveBeenCalled());
  }

  it('lists every recorded sighting', async () => {
    await search();
    await waitFor(() => {
      expect(screen.getByTestId('sighting-row-501')).toBeInTheDocument();
      expect(screen.getByTestId('sighting-row-502')).toBeInTheDocument();
      expect(screen.getByTestId('sighting-row-503')).toBeInTheDocument();
    });
  });

  it('reports the sighting the investigator picked', async () => {
    const onSelectSighting = vi.fn();
    await act(async () => {
      render(
        <InvestigationWorkspace
          cameras={cameras}
          initialPlate="GJ05AB1234"
          onSelectSighting={onSelectSighting}
        />,
      );
    });
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /SEARCH EXISTING RECORDS/i }));
    });
    const row = await screen.findByTestId('sighting-row-502');

    await act(async () => {
      fireEvent.click(row);
    });
    expect(onSelectSighting).toHaveBeenCalledWith(502);
  });

  it('shows which row is pinned', async () => {
    await act(async () => {
      render(
        <InvestigationWorkspace
          cameras={cameras}
          initialPlate="GJ05AB1234"
          selectedSightingId={502}
          onSelectSighting={vi.fn()}
        />,
      );
    });
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /SEARCH EXISTING RECORDS/i }));
    });

    await waitFor(() => {
      expect(screen.getByTestId('sighting-row-502').dataset.pinned).toBe('true');
      expect(screen.getByTestId('sighting-row-501').dataset.pinned).toBe('false');
    });
  });
});
