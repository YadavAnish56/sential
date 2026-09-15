import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import SelectedCameraPanel from './SelectedCameraPanel';
import LivePreview from './LivePreview';
import CameraCard from './CameraCard';
import { cameraService } from '../api/cameraService';

// Mock camera service
vi.mock('../api/cameraService', () => ({
  cameraService: {
    getCameras: vi.fn(),
    getPipelinesStatus: vi.fn(),
    startPipeline: vi.fn(),
    stopPipeline: vi.fn(),
    getWhepProxyUrl: vi.fn((id) => `/api/cameras/${id}/whep`),
    getPreviewUrl: vi.fn((id) => `/api/cameras/${id}/preview`),
  },
}));

describe('Phase 2A: Safe Display-Only Plate Viewing Enhancements', () => {
  const originalFetch = global.fetch;
  const originalRTCPeerConnection = window.RTCPeerConnection;

  const mockCamera = {
    id: 101,
    camera_code: 'CAM-GJ-01',
    name: 'Ahmedabad Junction Highgate',
    location: 'SG Highway Block A',
    latitude: 23.0225,
    longitude: 72.5714,
    status: 'online',
    stream_url: 'rtsp://192.168.1.100:8554/live/highgate',
  };

  const mockCamera2 = {
    id: 102,
    camera_code: 'CAM-GJ-02',
    name: 'Surat Ring Road Checkpoint',
    location: 'Surat Expressway',
    latitude: 21.1702,
    longitude: 72.8311,
    status: 'online',
    stream_url: 'rtsp://192.168.1.101:8554/live/surat',
  };

  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();

    // Mock RTCPeerConnection for LivePreview
    class MockPeerConnection {
      constructor() {
        this.connectionState = 'new';
        setTimeout(() => {
          this.connectionState = 'connected';
          if (this.onconnectionstatechange) this.onconnectionstatechange();
        }, 10);
      }
      addTransceiver() {}
      createOffer() {
        return Promise.resolve({ type: 'offer', sdp: 'mock-sdp' });
      }
      setLocalDescription() {
        return Promise.resolve();
      }
      close() {}
    }
    window.RTCPeerConnection = MockPeerConnection;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    window.RTCPeerConnection = originalRTCPeerConnection;
  });

  // 1. Default zoom is 1×
  it('1. Default zoom level is 1× on initial load', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    const zoom1Btn = screen.getByRole('button', { name: '1×' });
    const zoom2Btn = screen.getByRole('button', { name: '2×' });
    const zoom4Btn = screen.getByRole('button', { name: '4×' });

    expect(zoom1Btn).toHaveClass('active');
    expect(zoom2Btn).not.toHaveClass('active');
    expect(zoom4Btn).not.toHaveClass('active');

    const videoEl = document.querySelector('video.live-video-element');
    expect(videoEl).toBeInTheDocument();
    expect(videoEl.style.transform).toBe('none');
  });

  // 2. Default pan is centered
  it('2. Default pan is centered and nudge controls are inactive at 1×', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    const panUp = screen.getByRole('button', { name: 'Pan Up' });
    const panDown = screen.getByRole('button', { name: 'Pan Down' });
    const panLeft = screen.getByRole('button', { name: 'Pan Left' });
    const panRight = screen.getByRole('button', { name: 'Pan Right' });
    const panCenter = screen.getByRole('button', { name: 'Center Pan' });

    expect(panUp).toBeDisabled();
    expect(panDown).toBeDisabled();
    expect(panLeft).toBeDisabled();
    expect(panRight).toBeDisabled();
    expect(panCenter).toBeDisabled();
  });

  // 3. Default filter is Normal
  it('3. Default filter preset is Normal with no color/contrast distortion applied', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    const normalBtn = screen.getByRole('button', { name: 'Normal' });
    const contrastBtn = screen.getByRole('button', { name: 'Contrast Boost' });
    const nightBtn = screen.getByRole('button', { name: 'Night Boost' });

    expect(normalBtn).toHaveClass('active');
    expect(contrastBtn).not.toHaveClass('active');
    expect(nightBtn).not.toHaveClass('active');

    const videoEl = document.querySelector('video.live-video-element');
    expect(videoEl.style.filter).toBe('none');
    expect(videoEl).not.toHaveClass('filter-contrast');
    expect(videoEl).not.toHaveClass('filter-night');
  });

  // 4. Zoom controls update the video scale
  it('4. Zoom controls scale the video smoothly to 2× and 4×', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    const zoom1Btn = screen.getByRole('button', { name: '1×' });
    const zoom2Btn = screen.getByRole('button', { name: '2×' });
    const zoom4Btn = screen.getByRole('button', { name: '4×' });
    const videoEl = document.querySelector('video.live-video-element');

    // Click 2×
    act(() => {
      fireEvent.click(zoom2Btn);
    });
    expect(zoom2Btn).toHaveClass('active');
    expect(zoom1Btn).not.toHaveClass('active');
    expect(videoEl.style.transform).toContain('scale(2)');

    // Click 4×
    act(() => {
      fireEvent.click(zoom4Btn);
    });
    expect(zoom4Btn).toHaveClass('active');
    expect(zoom2Btn).not.toHaveClass('active');
    expect(videoEl.style.transform).toContain('scale(4)');

    // Click 1×
    act(() => {
      fireEvent.click(zoom1Btn);
    });
    expect(zoom1Btn).toHaveClass('active');
    expect(videoEl.style.transform).toBe('none');
  });

  // 5. Pan is inactive at 1×
  it('5. Pan dragging and directional nudges are disabled when zoom is 1×', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    const container = document.querySelector('.live-preview-container');
    expect(container).not.toHaveClass('zoom-active');

    // Pan nudge buttons must be disabled
    expect(screen.getByRole('button', { name: 'Pan Up' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Center Pan' })).toBeDisabled();

    // Pointer events should not initiate drag at 1×
    act(() => {
      fireEvent.pointerDown(container, { clientX: 100, clientY: 100, button: 0 });
      fireEvent.pointerMove(container, { clientX: 150, clientY: 150 });
      fireEvent.pointerUp(container);
    });

    const videoEl = document.querySelector('video.live-video-element');
    expect(videoEl.style.transform).toBe('none');
  });

  // 6. Pan is clamped at higher zoom levels
  it('6. Pan movements are strictly clamped within viewport boundaries at 2× and 4× zoom', async () => {
    const handlePanChange = vi.fn();
    await act(async () => {
      render(
        <LivePreview
          webrtcUrl="/api/cameras/101/whep"
          enableEnhancements={true}
          zoom={2}
          pan={{ x: 0, y: 0 }}
          filterPreset="normal"
          onPanChange={handlePanChange}
        />
      );
    });

    const container = document.querySelector('.live-preview-container');
    expect(container).toHaveClass('zoom-active');

    // Mock bounding dimensions (800w x 450h)
    vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({
      width: 800,
      height: 450,
      top: 0,
      left: 0,
      right: 800,
      bottom: 450,
    });

    // Max pan at zoom 2: maxPanX = (800 * (2-1))/2 = 400; maxPanY = (450 * 1)/2 = 225
    act(() => {
      fireEvent.pointerDown(container, { clientX: 0, clientY: 0, button: 0 });
      fireEvent.pointerMove(container, { clientX: 9999, clientY: 9999 });
    });

    expect(handlePanChange).toHaveBeenCalled();
    const lastCall = handlePanChange.mock.calls[handlePanChange.mock.calls.length - 1][0];
    expect(lastCall.x).toBeLessThanOrEqual(400);
    expect(lastCall.y).toBeLessThanOrEqual(225);

    // Negative boundary test
    act(() => {
      fireEvent.pointerDown(container, { clientX: 0, clientY: 0, button: 0 });
      fireEvent.pointerMove(container, { clientX: -9999, clientY: -9999 });
    });
    const negativeCall = handlePanChange.mock.calls[handlePanChange.mock.calls.length - 1][0];
    expect(negativeCall.x).toBeGreaterThanOrEqual(-400);
    expect(negativeCall.y).toBeGreaterThanOrEqual(-225);
  });

  // 6b. Letterbox-aware clamping test
  it('6b. Pan bounds accurately account for letterboxed regions and actual rendered video aspect ratio', async () => {
    const handlePanChange = vi.fn();
    await act(async () => {
      render(
        <LivePreview
          webrtcUrl="/api/cameras/101/whep"
          enableEnhancements={true}
          zoom={2}
          pan={{ x: 0, y: 0 }}
          filterPreset="normal"
          onPanChange={handlePanChange}
        />
      );
    });

    const container = document.querySelector('.live-preview-container');
    // Container is taller than 16:9 video: 800w x 600h (4:3 aspect)
    vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({
      width: 800,
      height: 600,
      top: 0,
      left: 0,
      right: 800,
      bottom: 600,
    });

    // At 16:9 video (800x450 rendered content), scaled by 2x = 900h.
    // maxPanY = (900 - 600)/2 = 150 (not 300 from old container-only formula).
    act(() => {
      fireEvent.pointerDown(container, { clientX: 0, clientY: 0, button: 0 });
      fireEvent.pointerMove(container, { clientX: 0, clientY: 9999 });
    });

    const lastCall = handlePanChange.mock.calls[handlePanChange.mock.calls.length - 1][0];
    expect(lastCall.y).toBeLessThanOrEqual(150);
  });

  // 7. Center resets pan
  it('7. Center nudge button resets pan translation back to (0, 0)', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    // Zoom to 2× to enable pan
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '2×' }));
    });

    const panUp = screen.getByRole('button', { name: 'Pan Up' });
    const panCenter = screen.getByRole('button', { name: 'Center Pan' });

    // Nudge up
    act(() => {
      fireEvent.click(panUp);
    });
    expect(panCenter).not.toBeDisabled();

    // Reset via Center
    act(() => {
      fireEvent.click(panCenter);
    });
    expect(panCenter).toBeDisabled();

    const videoEl = document.querySelector('video.live-video-element');
    expect(videoEl.style.transform).toBe('translate(0px, 0px) scale(2)');
  });

  // 8. Reset View restores zoom, pan, and filter defaults
  it('8. Reset View restores zoom to 1×, pan to center, and filter to Normal', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    // Apply 4× zoom, pan nudge, and Night Boost filter
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '4×' }));
      fireEvent.click(screen.getByRole('button', { name: 'Pan Left' }));
      fireEvent.click(screen.getByRole('button', { name: 'Night Boost' }));
    });

    const videoEl = document.querySelector('video.live-video-element');
    expect(videoEl).toHaveClass('filter-night');

    // Click Reset View
    const resetBtn = screen.getByRole('button', { name: 'Reset View' });
    act(() => {
      fireEvent.click(resetBtn);
    });

    // Zoom should be 1×
    expect(screen.getByRole('button', { name: '1×' })).toHaveClass('active');
    // Filter should be Normal
    expect(screen.getByRole('button', { name: 'Normal' })).toHaveClass('active');
    // Pan should be disabled
    expect(screen.getByRole('button', { name: 'Center Pan' })).toBeDisabled();
    // Video styles reset
    expect(videoEl.style.transform).toBe('none');
    expect(videoEl.style.filter).toBe('none');
  });

  // 9. Camera changes reset enhancement state
  it('9. Camera changes automatically reset enhancement state to safe defaults', async () => {
    let rerenderFn;
    await act(async () => {
      const res = render(
        <SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />
      );
      rerenderFn = res.rerender;
    });

    // Configure 2× zoom and Contrast Boost
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '2×' }));
      fireEvent.click(screen.getByRole('button', { name: 'Contrast Boost' }));
    });

    expect(screen.getByRole('button', { name: '2×' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: 'Contrast Boost' })).toHaveClass('active');

    // Switch to different camera
    await act(async () => {
      rerenderFn(<SelectedCameraPanel camera={mockCamera2} pipelineStatus={{ status: 'running' }} />);
    });

    // Verify auto-reset on new camera selection
    expect(screen.getByRole('button', { name: '1×' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: 'Normal' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: 'Center Pan' })).toBeDisabled();
  });

  // 10. Filter changes affect only display styling
  it('10. Display filter presets modify only CSS presentation without altering video source or data', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    const videoEl = document.querySelector('video.live-video-element');

    // Contrast Boost
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Contrast Boost' }));
    });
    expect(videoEl).toHaveClass('filter-contrast');
    expect(videoEl.style.filter).toContain('contrast(1.35)');

    // Night Boost
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Night Boost' }));
    });
    expect(videoEl).toHaveClass('filter-night');
    expect(videoEl.style.filter).toContain('contrast(1.45)');
    expect(videoEl.style.filter).toContain('grayscale(0.25)');

    // Back to Normal
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Normal' }));
    });
    expect(videoEl).not.toHaveClass('filter-contrast');
    expect(videoEl).not.toHaveClass('filter-night');
    expect(videoEl.style.filter).toBe('none');
  });

  // 11. Compact camera thumbnails remain unchanged
  it('11. CameraCard compact thumbnails remain unaffected without enhancement controls', async () => {
    await act(async () => {
      render(
        <CameraCard
          camera={mockCamera}
          pipelineStatus={{ status: 'running' }}
          compact={true}
        />
      );
    });

    // Camera card must NOT render the enhancement toolbar
    expect(screen.queryByRole('toolbar', { name: /Plate Display Enhancement Controls/i })).not.toBeInTheDocument();
    expect(screen.queryByText('DISPLAY ENHANCEMENT — NOT EVIDENCE')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reset View' })).not.toBeInTheDocument();
  });

  // 12. No API or network request is triggered by enhancement controls
  it('12. No network requests or backend API calls are triggered by any enhancement controls', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    const initialFetchCount = global.fetch.mock.calls.length;
    const initialStartCalls = cameraService.startPipeline.mock.calls.length;
    const initialStopCalls = cameraService.stopPipeline.mock.calls.length;

    // Perform zoom changes
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: '2×' }));
      fireEvent.click(screen.getByRole('button', { name: '4×' }));
    });

    // Perform pan nudges
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Pan Up' }));
      fireEvent.click(screen.getByRole('button', { name: 'Pan Left' }));
      fireEvent.click(screen.getByRole('button', { name: 'Center Pan' }));
    });

    // Perform filter toggles
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Contrast Boost' }));
      fireEvent.click(screen.getByRole('button', { name: 'Night Boost' }));
      fireEvent.click(screen.getByRole('button', { name: 'Normal' }));
    });

    // Perform Reset View
    act(() => {
      fireEvent.click(screen.getByRole('button', { name: 'Reset View' }));
    });

    // Verify zero API requests triggered
    expect(global.fetch.mock.calls.length).toBe(initialFetchCount);
    expect(cameraService.startPipeline.mock.calls.length).toBe(initialStartCalls);
    expect(cameraService.stopPipeline.mock.calls.length).toBe(initialStopCalls);
  });

  // 13. Disclaimer badge and clipped viewport structure
  it('13. Renders prominent non-evidentiary disclaimer badge and clipped viewport structure', async () => {
    await act(async () => {
      render(<SelectedCameraPanel camera={mockCamera} pipelineStatus={{ status: 'running' }} />);
    });

    // Mandatory disclaimer badge
    const badge = screen.getByText('DISPLAY ENHANCEMENT — NOT EVIDENCE');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveClass('enhancement-disclaimer-badge');

    // Clipped viewport structure
    const container = document.querySelector('.live-preview-container');
    expect(container).toBeInTheDocument();
    expect(container.style.overflow).toBe('hidden');

    const monitorScreen = document.querySelector('.monitor-screen');
    expect(monitorScreen).toBeInTheDocument();
  });
});
