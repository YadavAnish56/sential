import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { fetchClient } from './client';
import { cameraService } from './cameraService';
import App from '../App';
import GISMap from '../components/GISMap';

// Mock cameraService & alertService for App and GISMap tests
vi.mock('./cameraService', () => ({
  cameraService: {
    getCameras: vi.fn(),
    getCamerasMap: vi.fn(),
    getPipelinesStatus: vi.fn().mockResolvedValue({}),
    startPipeline: vi.fn().mockResolvedValue({}),
    stopPipeline: vi.fn().mockResolvedValue({}),
    getPreviewUrl: vi.fn().mockResolvedValue({}),
    getWhepProxyUrl: vi.fn().mockReturnValue('/api/cameras/1/whep'),
  }
}));

vi.mock('./alertService', () => ({
  alertService: {
    getAlerts: vi.fn().mockResolvedValue({ alerts: [], total: 0 }),
  }
}));

describe('Phase 32B — API Resilience & Error Handling', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.useRealTimers();
  });

  // 1. Successful JSON response
  it('1. handles successful JSON response and returns parsed data', async () => {
    const mockData = { cameras: [{ id: 1, camera_code: 'CAM-001' }], total: 1 };
    global.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve(mockData),
    });

    const result = await fetchClient('/cameras');
    expect(result).toEqual(mockData);
  });

  // 2. HTTP 502 response with HTML body
  it('2. safely handles HTTP 502 with HTML body without throwing SyntaxError', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      headers: new Headers({ 'content-type': 'text/html' }),
      text: () => Promise.resolve('<html><head><title>502 Bad Gateway</title></head><body>502 Bad Gateway</body></html>'),
      json: () => {
        throw new SyntaxError("Unexpected token '<', \"<html> <h\"... is not valid JSON");
      },
    });

    let caughtError = null;
    try {
      await fetchClient('/cameras');
    } catch (err) {
      caughtError = err;
    }

    expect(caughtError).toBeInstanceOf(Error);
    expect(caughtError).not.toBeInstanceOf(SyntaxError);
    expect(caughtError.message).toContain('502');
    expect(caughtError.message).toContain('Bad Gateway');
    expect(caughtError.status).toBe(502);
  });

  // 3. Invalid/non-JSON response when 200 OK
  it('3. rejects non-JSON response gracefully when status is 200 OK', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ 'content-type': 'text/html' }),
      text: () => Promise.resolve('<!doctype html><html><body>SPA HTML Fallback</body></html>'),
    });

    await expect(fetchClient('/cameras')).rejects.toThrow(/Invalid API response/);
  });

  // 9. Existing API behavior remains unchanged (204 No Content and JSON error detail)
  it('9. preserves 204 No Content and structured FastAPI error messages', async () => {
    // 204 No Content
    global.fetch.mockResolvedValueOnce({
      ok: true,
      status: 204,
      headers: new Headers(),
    });
    const result204 = await fetchClient('/events/acknowledge', { method: 'POST' });
    expect(result204).toBeNull();

    // 400 Bad Request with JSON detail
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve({ detail: 'Camera code already registered' }),
    });
    await expect(fetchClient('/cameras')).rejects.toThrow('Camera code already registered');
  });
});

describe('Phase 32B — Camera Load Bounded Retry & Component Recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // 4 & 5. Temporary failure followed by successful response & camera list recovers automatically
  it('4 & 5. retries on temporary failure and automatically recovers camera catalog in App', async () => {
    vi.useFakeTimers();

    const mockCameras = {
      cameras: [
        { id: 1, camera_code: 'CAM-001', name: 'Camera 01', status: 'online' },
        { id: 2, camera_code: 'CAM-002', name: 'Camera 02', status: 'online' },
      ],
      total: 2,
    };

    cameraService.getCameras
      .mockRejectedValueOnce(new Error('HTTP error 502: Bad Gateway'))
      .mockResolvedValueOnce(mockCameras);

    render(<App />);

    // Flush initial microtasks
    await act(async () => {
      await Promise.resolve();
    });

    expect(cameraService.getCameras).toHaveBeenCalledTimes(1);

    // Fast-forward backoff delay (1000ms)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    expect(cameraService.getCameras).toHaveBeenCalledTimes(2);

    // Camera catalog now loaded
    expect(screen.getByTestId('camera-tile-1')).toBeInTheDocument();
    expect(screen.getByTestId('camera-tile-2')).toBeInTheDocument();
    expect(screen.getAllByText(/2 REG/i).length).toBeGreaterThan(0);
  });

  // 6. No duplicate retry timers and bounded to max 10 retries
  it('6. bounds retries to maximum 10 attempts and displays error on exhaustion', async () => {
    vi.useFakeTimers();

    cameraService.getCameras.mockRejectedValue(new Error('HTTP error 502: Bad Gateway'));

    render(<App />);

    // Flush initial attempt
    await act(async () => {
      await Promise.resolve();
    });

    expect(cameraService.getCameras).toHaveBeenCalledTimes(1);

    // Advance timers for all 10 retry steps
    for (let i = 1; i <= 10; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5100);
      });
    }

    // Maximum 1 initial + 10 retries = 11 calls total
    expect(cameraService.getCameras).toHaveBeenCalledTimes(11);

    // Further timer advancement should NOT make additional calls (bounded)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(cameraService.getCameras).toHaveBeenCalledTimes(11);

    // Shows clear unavailable / error state
    expect(screen.getByText(/Camera service temporarily unavailable/i)).toBeInTheDocument();
  });

  // 7. Cleanup on unmount
  it('7. cleans up pending retry timers on unmount without state leaks', async () => {
    vi.useFakeTimers();

    cameraService.getCameras.mockRejectedValue(new Error('HTTP error 502: Bad Gateway'));

    const { unmount } = render(<App />);

    await act(async () => {
      await Promise.resolve();
    });

    expect(cameraService.getCameras).toHaveBeenCalledTimes(1);

    // Unmount before retry fires
    unmount();

    // Advance time
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });

    // No subsequent calls made after unmount
    expect(cameraService.getCameras).toHaveBeenCalledTimes(1);
  });

  // 8. GIS map recovery
  it('8. retries map data loading and automatically clears error upon recovery', async () => {
    vi.useFakeTimers();

    const mockMapNodes = [
      { id: 1, camera_code: 'CAM-001', name: 'Camera 01', latitude: 21.1702, longitude: 72.8311, status: 'online' },
    ];

    // Attempt 1 fails, Attempt 2 succeeds
    cameraService.getCamerasMap
      .mockRejectedValueOnce(new Error('HTTP error 502: Bad Gateway'))
      .mockResolvedValueOnce(mockMapNodes);

    render(<GISMap />);

    // Flush initial attempt
    await act(async () => {
      await Promise.resolve();
    });

    expect(cameraService.getCamerasMap).toHaveBeenCalledTimes(1);

    // Shows error state on initial failure
    expect(screen.getByText(/HTTP error 502: Bad Gateway/i)).toBeInTheDocument();

    // Advance timers for backoff delay
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200);
    });

    expect(cameraService.getCamerasMap).toHaveBeenCalledTimes(2);

    // Error banner is automatically cleared once map loads successfully
    expect(screen.queryByText(/HTTP error 502/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Error:/i)).not.toBeInTheDocument();
  });

  // 9. GISMap retry exhaustion
  it('9. bounds GISMap retries to maximum 10 attempts and shows unavailable state upon exhaustion', async () => {
    vi.useFakeTimers();

    cameraService.getCamerasMap.mockRejectedValue(new Error('HTTP error 502: Bad Gateway'));

    render(<GISMap />);

    // Flush initial attempt
    await act(async () => {
      await Promise.resolve();
    });

    expect(cameraService.getCamerasMap).toHaveBeenCalledTimes(1);

    // Advance timers through all 10 retries
    for (let i = 1; i <= 10; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5100);
      });
    }

    // Maximum 1 initial + 10 retries = 11 calls total
    expect(cameraService.getCamerasMap).toHaveBeenCalledTimes(11);

    // Further timer advancement makes no calls
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(cameraService.getCamerasMap).toHaveBeenCalledTimes(11);

    // Shows unavailable state
    expect(screen.getByText(/Map service temporarily unavailable/i)).toBeInTheDocument();
  });
});

