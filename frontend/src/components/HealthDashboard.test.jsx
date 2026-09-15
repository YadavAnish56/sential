import React from 'react';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import HealthDashboard from './HealthDashboard';
import { cameraService } from '../api/cameraService';
import { healthService } from '../api/healthService';

vi.mock('../api/cameraService', () => ({
  cameraService: {
    getPipelinesStatus: vi.fn(),
  }
}));

vi.mock('../api/healthService', () => ({
  healthService: {
    getSystemHealth: vi.fn(),
  }
}));

describe('HealthDashboard Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test('T1: renders loading state initially', () => {
    cameraService.getPipelinesStatus.mockReturnValue(new Promise(() => {}));
    healthService.getSystemHealth.mockReturnValue(new Promise(() => {}));
    
    render(<HealthDashboard />);
    expect(screen.getByText('Loading telemetry...')).toBeInTheDocument();
  });

  test('T2 & T3 & T4 & T5: renders pipeline telemetry, stream health, pipeline stats, and worker info safely', async () => {
    const mockPipelineData = {
      "CAM-A": {
        camera_id: "CAM-A",
        status: "running",
        pipeline_stats: {
          frames_total: 100,
          frames_skipped: 10,
          frames_detected: 90,
          frames_tracked: 85,
          frames_anpr: 80,
          total_errors: 0
        },
        stream_health: {
          status: "online",
          last_pts_ms: 123456.78,
          consecutive_failures: 0,
          reconnect_attempts: 1,
          properties: { codec: "h264", width: 1920, height: 1080 }
        }
      }
    };

    const mockSystemHealth = {
      anpr_persistence_worker: {
        is_alive: true,
        queue_size: 5
      }
    };

    cameraService.getPipelinesStatus.mockResolvedValue(mockPipelineData);
    healthService.getSystemHealth.mockResolvedValue(mockSystemHealth);

    render(<HealthDashboard />);

    await waitFor(() => {
      expect(screen.getByText('System Telemetry & Health')).toBeInTheDocument();
    });

    // Check worker status (T5)
    expect(screen.getByText('Alive / Running')).toBeInTheDocument();
    expect(screen.getByText('5 items')).toBeInTheDocument();

    // Check Pipeline Telemetry & Stats (T2, T4)
    expect(screen.getByText('CAM-A')).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getByText('R: 100 / S: 10 / D: 90 / T: 85')).toBeInTheDocument();
    expect(screen.getByText('80 reads')).toBeInTheDocument();

    // Check Stream Health safely (T3)
    expect(screen.getByText('h264 / 1920x1080')).toBeInTheDocument();
    expect(screen.getByText('123457')).toBeInTheDocument(); // Last PTS rounded
  });

  test('T7: renders error state on API failure', async () => {
    cameraService.getPipelinesStatus.mockRejectedValue(new Error('Network Error'));
    healthService.getSystemHealth.mockRejectedValue(new Error('Network Error'));

    render(<HealthDashboard />);

    await waitFor(() => {
      expect(screen.getByText('API Error:')).toBeInTheDocument();
    });
    expect(screen.getByText('Network Error')).toBeInTheDocument();
  });

  test('T8: missing optional telemetry fields do not crash UI', async () => {
    const mockPipelineData = {
      "CAM-B": {
        camera_id: "CAM-B",
        status: "error",
        pipeline_stats: null, // missing stats
        stream_health: null // missing stream health
      }
    };

    const mockSystemHealth = {
      anpr_persistence_worker: null // missing worker data
    };

    cameraService.getPipelinesStatus.mockResolvedValue(mockPipelineData);
    healthService.getSystemHealth.mockResolvedValue(mockSystemHealth);

    render(<HealthDashboard />);

    await waitFor(() => {
      expect(screen.getByText('Persistence worker data not available.')).toBeInTheDocument();
    });
    expect(screen.getByText('CAM-B')).toBeInTheDocument();
    expect(screen.getByText('UNKNOWN')).toBeInTheDocument();
  });
});
