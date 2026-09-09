import React from 'react';
import { render, screen, waitFor, fireEvent, act, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import AlertsInbox from './AlertsInbox';
import { alertService } from '../api/alertService';

vi.mock('../api/alertService', () => ({
  alertService: {
    getAlerts: vi.fn(),
    acknowledgeAlert: vi.fn(),
  }
}));

const mockAlerts = {
  alerts: [
    {
      id: 1,
      camera_id: 101,
      vehicle_id: 55,
      alert_type: 'Watchlist Match',
      severity: 'high',
      message: 'Vehicle ABC-123 matched watchlist.',
      status: 'new',
      timestamp: '2026-09-08T10:00:00Z'
    },
    {
      id: 2,
      camera_id: 102,
      vehicle_id: null,
      alert_type: 'Motion Detected',
      severity: 'low',
      message: 'Motion detected in restricted area.',
      status: 'acknowledged',
      timestamp: '2026-09-08T09:00:00Z'
    }
  ],
  total: 2
};

describe('AlertsInbox integration tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test('T1 & T2 & T3: AlertsInbox renders, calls API, displays alerts and badges', async () => {
    alertService.getAlerts.mockResolvedValue(mockAlerts);

    render(<AlertsInbox />);

    await waitFor(() => {
      expect(alertService.getAlerts).toHaveBeenCalled();
      expect(screen.getByText('Security Alerts')).toBeInTheDocument();
    });

    // Check new alert
    expect(screen.getByText('Watchlist Match')).toBeInTheDocument();
    expect(screen.getByText('HIGH')).toBeInTheDocument();
    expect(screen.getByText('NEW')).toBeInTheDocument();
    expect(screen.getByText('Vehicle ABC-123 matched watchlist.')).toBeInTheDocument();
    
    // Check acknowledged alert
    expect(screen.getByText('Motion Detected')).toBeInTheDocument();
    expect(screen.getByText('LOW')).toBeInTheDocument();
    expect(screen.getByText('ACKNOWLEDGED')).toBeInTheDocument();
    
    // Check Acknowledge button only on 'new'
    const buttons = screen.getAllByRole('button', { name: 'Acknowledge' });
    expect(buttons.length).toBe(1);
  });

  test('T4 & T5: AlertsInbox empty and error states', async () => {
    // Empty
    alertService.getAlerts.mockResolvedValue({ alerts: [], total: 0 });
    
    let container;
    const res = render(<AlertsInbox />);
    container = res.container;
    
    await waitFor(() => {
      expect(screen.getByText('No active alerts. System is secure.')).toBeInTheDocument();
    });
    cleanup();

    // Error
    alertService.getAlerts.mockRejectedValue(new Error('Network Error'));
    render(<AlertsInbox />);
    
    await waitFor(() => {
      expect(screen.getByText('Error: Failed to load alerts.')).toBeInTheDocument();
    });
  });

  test('T6 & T7 & T8: Acknowledge alert, loading state, success updates UI, handles failure', async () => {
    alertService.getAlerts.mockResolvedValue(mockAlerts);
    alertService.acknowledgeAlert.mockResolvedValue({
      ...mockAlerts.alerts[0],
      status: 'acknowledged'
    });

    render(<AlertsInbox />);
    
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeInTheDocument();
    });

    const ackButton = screen.getByRole('button', { name: 'Acknowledge' });
    
    // Click acknowledge
    await act(async () => {
      fireEvent.click(ackButton);
    });

    expect(alertService.acknowledgeAlert).toHaveBeenCalledWith(1);
    
    // After success, it should be updated to ACKNOWLEDGED
    await waitFor(() => {
      expect(screen.getAllByText('ACKNOWLEDGED').length).toBe(2);
    });
    
    // Button should be gone
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument();
  });

  test('T9: Prevent duplicate acknowledgment', async () => {
    alertService.getAlerts.mockResolvedValue(mockAlerts);
    // Simulate slow response
    let resolveAck;
    alertService.acknowledgeAlert.mockImplementation(() => {
      return new Promise(resolve => {
        resolveAck = resolve;
      });
    });

    render(<AlertsInbox />);
    
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeInTheDocument();
    });

    const ackButton = screen.getByRole('button', { name: 'Acknowledge' });
    
    await act(async () => {
      fireEvent.click(ackButton);
    });

    // Button should show Acknowledging and be disabled
    expect(screen.getByText('Acknowledging...')).toBeInTheDocument();
    expect(ackButton).toBeDisabled();

    // Click again
    await act(async () => {
      fireEvent.click(ackButton);
    });

    // Should only be called once
    expect(alertService.acknowledgeAlert).toHaveBeenCalledTimes(1);

    // Resolve the promise
    await act(async () => {
      resolveAck({ ...mockAlerts.alerts[0], status: 'acknowledged' });
    });
  });
});
