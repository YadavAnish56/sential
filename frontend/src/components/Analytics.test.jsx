import React from 'react';
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import App from '../App';
import EventFeed from './EventFeed';
import VehicleTimeline from './VehicleTimeline';
import VehicleSearch from './VehicleSearch';
import { analyticsService } from '../api/analyticsService';
import { cameraService } from '../api/cameraService';

vi.mock('../api/analyticsService', () => ({
  analyticsService: {
    getRecentEvents: vi.fn(),
    getVehicleTimeline: vi.fn(),
    getVehicles: vi.fn(),
    searchVehicles: vi.fn(),
  }
}));

vi.mock('../api/cameraService', () => ({
  cameraService: {
    getCameras: vi.fn(),
    getPipelinesStatus: vi.fn(),
    startPipeline: vi.fn(),
    stopPipeline: vi.fn(),
    getPreviewUrl: vi.fn(),
  }
}));

const mockCameras = {
  cameras: [
    { id: 101, camera_code: 'CAM-A', name: 'Main Gate' }
  ]
};

const mockVehicles = [
  { id: 55, plate_number: 'ABC-123' },
  { id: 56, plate_number: 'XYZ-999' }
];

const mockEvents = {
  events: [
    { id: 1, camera_id: 101, vehicle_id: 55, event_type: 'vehicle_detected', object_type: 'car', confidence: 0.95, timestamp: '2026-09-07T10:00:00Z' },
    { id: 2, camera_id: 101, vehicle_id: null, event_type: 'motion_detected', object_type: null, confidence: null, timestamp: '2026-09-07T10:05:00Z' }
  ],
  total: 2
};

const mockTimeline = {
  plate_number: 'ABC-123',
  vehicle: { id: 55, plate_number: 'ABC-123' },
  timeline: [
    { event_id: 1, camera_id: 101, camera_code: 'CAM-A', camera_name: 'Main Gate', location: 'North', event_type: 'vehicle_detected', confidence: 0.95, timestamp: '2026-09-07T10:00:00Z' }
  ],
  total_detections: 1
};

const mockSearchResults = [
  { id: 55, plate_number: 'ABC-123', color: 'red', make: 'toyota', model: 'camry', vehicle_type: 'sedan' }
];

describe('Analytics components integration tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test('T1 & T2 & T5 & T6: EventFeed renders states and selects plate safely', async () => {
    analyticsService.getRecentEvents.mockResolvedValue(mockEvents);
    analyticsService.getVehicles.mockResolvedValue(mockVehicles);
    cameraService.getCameras.mockResolvedValue(mockCameras);

    const onPlateSelect = vi.fn();

    let container;
    const res = render(<EventFeed onPlateSelect={onPlateSelect} />);
    container = res.container;
    
    expect(screen.getByText('Loading recent events...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText('vehicle_detected - car')).toBeInTheDocument();
    });

    expect(screen.getByText('(95%)')).toBeInTheDocument();
    expect(screen.getByText('motion_detected')).toBeInTheDocument();

    const plateBtn = screen.getByRole('button', { name: 'Plate: ABC-123' });
    expect(plateBtn).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(plateBtn);
    });
    expect(onPlateSelect).toHaveBeenCalledWith('ABC-123');

    const buttons = screen.getAllByRole('button');
    expect(buttons.length).toBe(1);

    expect(container.innerHTML).not.toContain('rtsp');
  });

  test('T3: EventFeed handles empty response', async () => {
    analyticsService.getRecentEvents.mockResolvedValue({ events: [], total: 0 });
    analyticsService.getVehicles.mockResolvedValue([]);
    cameraService.getCameras.mockResolvedValue({ cameras: [] });

    render(<EventFeed onPlateSelect={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('No recent events found.')).toBeInTheDocument();
    });
  });

  test('T4: EventFeed handles API failure', async () => {
    analyticsService.getRecentEvents.mockRejectedValue(new Error('API Down'));
    analyticsService.getVehicles.mockResolvedValue([]);
    cameraService.getCameras.mockResolvedValue({ cameras: [] });

    render(<EventFeed onPlateSelect={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Error: API Down')).toBeInTheDocument();
    });
  });

  test('T7 & T8 & T9 & T10: VehicleTimeline fetches and displays ordered timeline safely', async () => {
    analyticsService.getVehicleTimeline.mockResolvedValue(mockTimeline);
    const onClose = vi.fn();

    let container;
    const res = render(<VehicleTimeline plateNumber="ABC 123/XYZ" onClose={onClose} />);
    container = res.container;
    
    await waitFor(() => {
      expect(screen.getByText('Main Gate')).toBeInTheDocument();
    });

    expect(analyticsService.getVehicleTimeline).toHaveBeenCalledWith('ABC 123/XYZ');
    expect(screen.getByText('vehicle_detected')).toBeInTheDocument();
    expect(container.innerHTML).not.toContain('rtsp');
  });

  test('T11: Timeline handles empty response', async () => {
    analyticsService.getVehicleTimeline.mockResolvedValue({ timeline: [] });
    render(<VehicleTimeline plateNumber="EMPTY" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('No timeline entries found for this vehicle.')).toBeInTheDocument();
    });
  });

  test('T12: Timeline handles API failure', async () => {
    analyticsService.getVehicleTimeline.mockRejectedValue(new Error('Timeline Error'));
    render(<VehicleTimeline plateNumber="ERROR" onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText('Error: Timeline Error')).toBeInTheDocument();
    });
  });

  test('Search T1 & T2 & T3 & T4: VehicleSearch input, loading, results, select', async () => {
    analyticsService.searchVehicles.mockResolvedValue(mockSearchResults);
    const onPlateSelect = vi.fn();
    render(<VehicleSearch onPlateSelect={onPlateSelect} />);

    const input = screen.getByPlaceholderText('Enter plate number...');
    const searchBtn = screen.getByRole('button', { name: 'Search' });
    
    expect(searchBtn).toBeDisabled();

    fireEvent.change(input, { target: { value: 'ABC' } });
    expect(searchBtn).not.toBeDisabled();

    fireEvent.click(searchBtn);

    expect(screen.getByText('Searching...')).toBeInTheDocument();
    expect(analyticsService.searchVehicles).toHaveBeenCalledWith('ABC');

    await waitFor(() => {
      expect(screen.getByText('ABC-123')).toBeInTheDocument();
    });
    expect(screen.getByText('red toyota camry sedan')).toBeInTheDocument();

    const viewBtn = screen.getByRole('button', { name: 'View Timeline' });
    fireEvent.click(viewBtn);
    expect(onPlateSelect).toHaveBeenCalledWith('ABC-123');
  });

  test('Search T5: VehicleSearch empty results', async () => {
    analyticsService.searchVehicles.mockResolvedValue([]);
    render(<VehicleSearch onPlateSelect={vi.fn()} />);
    
    fireEvent.change(screen.getByPlaceholderText('Enter plate number...'), { target: { value: 'NONE' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(screen.getByText('No vehicles found matching "NONE".')).toBeInTheDocument();
    });
  });

  test('Search T6: VehicleSearch API failure', async () => {
    analyticsService.searchVehicles.mockRejectedValue(new Error('Search Error'));
    render(<VehicleSearch onPlateSelect={vi.fn()} />);
    
    fireEvent.change(screen.getByPlaceholderText('Enter plate number...'), { target: { value: 'FAIL' } });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(screen.getByText('Search failed. Please try again.')).toBeInTheDocument();
    });
  });

  test('T13 & T14: App integrates EventFeed, Search, and Timeline safely', async () => {
    analyticsService.getRecentEvents.mockResolvedValue(mockEvents);
    analyticsService.getVehicles.mockResolvedValue(mockVehicles);
    cameraService.getCameras.mockResolvedValue(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValue({});
    analyticsService.getVehicleTimeline.mockResolvedValue(mockTimeline);

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText('Main Gate').length).toBeGreaterThan(0);
    });

    expect(screen.getByText('CAM-A')).toBeInTheDocument();
    expect(screen.getByText('Recent Detections')).toBeInTheDocument();
    expect(screen.getByText('Select a plate from the event feed to view its cross-camera timeline.')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Enter plate number...')).toBeInTheDocument();

    const plateBtn = screen.getByRole('button', { name: 'Plate: ABC-123' });
    await act(async () => {
      fireEvent.click(plateBtn);
    });

    expect(analyticsService.getVehicleTimeline).toHaveBeenCalledWith('ABC-123');
    
    await waitFor(() => {
      expect(screen.getByText('Timeline: ABC-123')).toBeInTheDocument();
    });

    const closeBtn = screen.getByLabelText('Close timeline');
    await act(async () => {
      fireEvent.click(closeBtn);
    });

    await waitFor(() => {
      expect(screen.getByText('Select a plate from the event feed to view its cross-camera timeline.')).toBeInTheDocument();
    });
  });
});
