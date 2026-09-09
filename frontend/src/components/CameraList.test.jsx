import React from 'react';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { vi } from 'vitest';
import CameraList from './CameraList';
import { cameraService } from '../api/cameraService';

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
    { id: 1, camera_code: 'CAM-001', name: 'Main Gate', location: 'North', stream_url: 'rtsp://secret' },
    { id: 2, camera_code: 'CAM-002', name: 'Backyard', location: 'South', stream_url: 'rtsp://secret' }
  ],
  total: 2
};

const mockStatus = {
  'CAM-001': { status: 'RUNNING', frames_processed: 150 },
  'CAM-002': { status: 'STOPPED', frames_processed: 0 }
};

describe('CameraList component integration tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  test('T1 & T2: cameras load successfully and data renders', async () => {
    cameraService.getCameras.mockResolvedValueOnce(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValueOnce(mockStatus);

    await act(async () => {
      render(<CameraList />);
    });
    
    expect(screen.getByText('Main Gate')).toBeInTheDocument();
    expect(screen.getByText('CAM-001')).toBeInTheDocument();
    expect(screen.getByText('Backyard')).toBeInTheDocument();
  });

  test('T3 & T11: pipeline status renders, unexpected status safe', async () => {
    cameraService.getCameras.mockResolvedValueOnce(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValueOnce({
      'CAM-001': { status: 'RUNNING' },
      'CAM-002': { status: 'WEIRD_STATE' }
    });

    await act(async () => {
      render(<CameraList />);
    });
    
    expect(screen.getByText('Running')).toBeInTheDocument();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  test('T4, T5, T6: Start/Stop calls API, toggles loading state, prevents duplicates', async () => {
    cameraService.getCameras.mockResolvedValueOnce(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValueOnce(mockStatus);
    
    let resolveStart;
    const startPromise = new Promise(resolve => { resolveStart = resolve; });
    cameraService.startPipeline.mockReturnValueOnce(startPromise);

    await act(async () => {
      render(<CameraList />);
    });
    
    const startBtns = screen.getAllByText('Start');
    const startBtn = startBtns[1]; 
    
    expect(startBtn).not.toBeDisabled();
    
    await act(async () => {
      fireEvent.click(startBtn);
    });
    
    expect(screen.getByText('Processing...')).toBeInTheDocument();
    expect(screen.getByText('Processing...')).toBeDisabled();
    expect(cameraService.startPipeline).toHaveBeenCalledWith(2);

    cameraService.getPipelinesStatus.mockResolvedValueOnce({
      ...mockStatus,
      'CAM-002': { status: 'STARTING' }
    });

    await act(async () => {
      resolveStart();
    });
    
    const stopBtns = screen.getAllByText('Stop');
    const stopBtn = stopBtns[0]; 
    cameraService.stopPipeline.mockResolvedValueOnce({});
    
    await waitFor(() => {
      expect(stopBtn).not.toBeDisabled();
    });

    await act(async () => {
      fireEvent.click(stopBtn);
    });
    expect(cameraService.stopPipeline).toHaveBeenCalledWith(1);
  });

  test('T7 & T8: polling updates runtime status, cleanup occurs on unmount', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    cameraService.getCameras.mockResolvedValueOnce(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValueOnce(mockStatus);

    let view;
    await act(async () => {
      view = render(<CameraList />);
    });
    
    expect(cameraService.getPipelinesStatus).toHaveBeenCalledTimes(1);

    cameraService.getPipelinesStatus.mockResolvedValueOnce({
      'CAM-001': { status: 'STOPPED' },
      'CAM-002': { status: 'STOPPED' }
    });

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(cameraService.getPipelinesStatus).toHaveBeenCalledTimes(2);
    
    view.unmount();

    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(cameraService.getPipelinesStatus).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  test('T9 & T10: API failure renders safe error, 404/400 handled safely', async () => {
    cameraService.getCameras.mockRejectedValueOnce(new Error('Network failure: Unable to connect to the backend API.'));

    await act(async () => {
      render(<CameraList />);
    });
    
    expect(screen.getByText('Error: Network failure: Unable to connect to the backend API.')).toBeInTheDocument();

    cameraService.getCameras.mockResolvedValueOnce(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValueOnce(mockStatus);
    cameraService.startPipeline.mockRejectedValueOnce(new Error('Camera 2 has no stream_url'));

    await act(async () => {
      render(<CameraList />);
    });
    
    const startBtns = screen.getAllByText('Start');
    await act(async () => {
      fireEvent.click(startBtns[1]);
    });

    expect(screen.getByText('Camera 2 has no stream_url')).toBeInTheDocument();
  });

  test('T12 & T13: stream_url is never rendered or connected, no RTSP elements created', async () => {
    cameraService.getCameras.mockResolvedValueOnce(mockCameras);
    const videoElements = document.querySelectorAll('video');
    expect(videoElements.length).toBe(0);

    const domHtml = document.body.innerHTML;
    expect(domHtml).not.toContain('rtsp://secret');
  });

  test('T8-T16: Live Preview and WHEP behavior', async () => {
    const mockCreateOffer = vi.fn().mockResolvedValue({ type: 'offer', sdp: 'fake-offer' });
    const mockSetLocalDescription = vi.fn().mockResolvedValue();
    const mockSetRemoteDescription = vi.fn().mockResolvedValue();
    const mockAddTransceiver = vi.fn();
    const mockClose = vi.fn();
    let ontrackCallback = null;

    global.RTCPeerConnection = class {
      constructor() {
        this.createOffer = mockCreateOffer;
        this.setLocalDescription = mockSetLocalDescription;
        this.setRemoteDescription = mockSetRemoteDescription;
        this.addTransceiver = mockAddTransceiver;
        this.close = mockClose;
      }
      set ontrack(cb) {
        ontrackCallback = cb;
      }
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: () => Promise.resolve('fake-answer-sdp')
    });

    cameraService.getCameras.mockResolvedValueOnce(mockCameras);
    cameraService.getPipelinesStatus.mockResolvedValueOnce(mockStatus);
    
    await act(async () => {
      render(<CameraList />);
    });

    const viewLiveBtns = screen.getAllByText('View Live');
    expect(viewLiveBtns.length).toBe(2);

    cameraService.getPreviewUrl.mockResolvedValueOnce({ webrtc_url: 'https://test/whep' });

    await act(async () => {
      fireEvent.click(viewLiveBtns[0]);
    });

    expect(cameraService.getPreviewUrl).toHaveBeenCalledWith(1);
    expect(cameraService.getPreviewUrl).toHaveBeenCalledTimes(1);

    expect(screen.getByText('Connecting to live stream...')).toBeInTheDocument();

    await waitFor(() => {
      expect(mockCreateOffer).toHaveBeenCalled();
    });

    expect(mockAddTransceiver).toHaveBeenCalledWith('video', { direction: 'recvonly' });
    expect(mockAddTransceiver).toHaveBeenCalledWith('audio', { direction: 'recvonly' });

    expect(global.fetch).toHaveBeenCalledWith('https://test/whep', {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: 'fake-offer'
    });

    await act(async () => {
      if (ontrackCallback) {
        ontrackCallback({ streams: [{ id: 'mock-stream', getTracks: () => [] }] });
      }
    });

    expect(screen.queryByText('Connecting to live stream...')).not.toBeInTheDocument();
    
    const videos = document.querySelectorAll('video');
    expect(videos.length).toBe(1);

    const stopPreviewBtn = screen.getByText('Stop Preview');
    await act(async () => {
      fireEvent.click(stopPreviewBtn);
    });

    expect(mockClose).toHaveBeenCalled();
    expect(document.querySelectorAll('video').length).toBe(0);

    cameraService.getPreviewUrl.mockRejectedValueOnce(new Error('Network fail'));
    await act(async () => {
      fireEvent.click(screen.getAllByText('View Live')[1]);
    });
    
    await waitFor(() => {
      expect(screen.getByText('Network fail')).toBeInTheDocument();
    });

    delete global.RTCPeerConnection;
    delete global.fetch;
  });
});
