import React from 'react';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { vi } from 'vitest';
import WatchlistManager from './WatchlistManager';
import { watchlistService } from '../api/watchlistService';

vi.mock('../api/watchlistService', () => ({
  watchlistService: {
    getWatchlist: vi.fn(),
    getWatchlistEntry: vi.fn(),
    createWatchlistEntry: vi.fn(),
    updateWatchlistEntry: vi.fn(),
    deleteWatchlistEntry: vi.fn(),
  },
}));

const mockEntries = [
  {
    id: 1,
    plate_number: 'GJ05AB1234',
    description: 'Suspect vehicle in commercial burglary',
    severity: 'critical',
    is_active: true,
    created_at: '2026-09-11T09:00:00Z',
    updated_at: '2026-09-11T09:30:00Z',
  },
  {
    id: 2,
    plate_number: 'MH12CD5678',
    description: 'Expired permit / flagged',
    severity: 'low',
    is_active: false,
    created_at: '2026-09-10T12:00:00Z',
    updated_at: '2026-09-10T12:00:00Z',
  },
];

describe('WatchlistManager Component Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  // 1 & 2: Watchlist loads and entries render correctly
  test('1 & 2: Watchlist loads successfully and renders entries with badges', async () => {
    watchlistService.getWatchlist.mockResolvedValue(mockEntries);

    render(<WatchlistManager />);

    // Check loading state first
    expect(screen.getByText(/loading surveillance watchlist/i)).toBeInTheDocument();

    // Await data load
    await waitFor(() => {
      expect(watchlistService.getWatchlist).toHaveBeenCalled();
      expect(screen.getByText('Surveillance Watchlist')).toBeInTheDocument();
    });

    // Check entry 1
    expect(screen.getByText('GJ05AB1234')).toBeInTheDocument();
    expect(screen.getByText('CRITICAL')).toBeInTheDocument();
    expect(screen.getByText('Suspect vehicle in commercial burglary')).toBeInTheDocument();
    expect(screen.getByText('ACTIVE')).toBeInTheDocument();

    // Check entry 2
    expect(screen.getByText('MH12CD5678')).toBeInTheDocument();
    expect(screen.getByText('LOW')).toBeInTheDocument();
    expect(screen.getByText('INACTIVE')).toBeInTheDocument();

    // Check automated alert notice
    expect(screen.getByText(/Automated Alert Integration:/i)).toBeInTheDocument();
  });

  // 3: Search and filter controls work
  test('3: Search input and filters trigger API with expected query parameters', async () => {
    watchlistService.getWatchlist.mockResolvedValue(mockEntries);

    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText('GJ05AB1234')).toBeInTheDocument();
    });

    // Search input
    const searchInput = screen.getByLabelText(/search plate/i);
    fireEvent.change(searchInput, { target: { value: 'GJ05' } });

    await waitFor(() => {
      expect(watchlistService.getWatchlist).toHaveBeenCalledWith(
        expect.objectContaining({ search: 'GJ05' })
      );
    });

    // Active status filter
    const statusSelect = screen.getByLabelText(/filter status/i);
    fireEvent.change(statusSelect, { target: { value: 'true' } });

    await waitFor(() => {
      expect(watchlistService.getWatchlist).toHaveBeenCalledWith(
        expect.objectContaining({ is_active: true })
      );
    });

    // Severity filter
    const severitySelect = screen.getByLabelText(/filter severity/i);
    fireEvent.change(severitySelect, { target: { value: 'critical' } });

    await waitFor(() => {
      expect(watchlistService.getWatchlist).toHaveBeenCalledWith(
        expect.objectContaining({ severity: 'critical' })
      );
    });
  });

  // 4: Add entry submits correctly
  test('4: Add modal opens and submits new entry to POST /api/watchlist', async () => {
    watchlistService.getWatchlist.mockResolvedValue([]);
    watchlistService.createWatchlistEntry.mockResolvedValue({
      id: 3,
      plate_number: 'DL01A9999',
      description: 'Stolen sedan',
      severity: 'high',
      is_active: true,
      created_at: '2026-09-11T10:00:00Z',
      updated_at: '2026-09-11T10:00:00Z',
    });

    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText(/no vehicles found/i)).toBeInTheDocument();
    });

    // Click Add button
    fireEvent.click(screen.getByRole('button', { name: /\+ add vehicle of interest/i }));

    expect(screen.getByText('Add Vehicle to Watchlist')).toBeInTheDocument();

    // Fill form
    fireEvent.change(screen.getByLabelText(/Indian License Plate Number/i), {
      target: { value: 'DL01A9999' },
    });
    fireEvent.change(screen.getByLabelText(/Reason \/ Case Notes/i), {
      target: { value: 'Stolen sedan' },
    });

    // Submit
    fireEvent.click(screen.getByRole('button', { name: /add to watchlist/i }));

    await waitFor(() => {
      expect(watchlistService.createWatchlistEntry).toHaveBeenCalledWith({
        plate_number: 'DL01A9999',
        description: 'Stolen sedan',
        severity: 'high',
        is_active: true,
      });
      expect(screen.getByText(/Vehicle DL01A9999 added to surveillance watchlist/i)).toBeInTheDocument();
    });
  });

  // 5: Edit entry submits correctly
  test('5: Edit modal opens with populated data and submits PATCH /api/watchlist/{id}', async () => {
    watchlistService.getWatchlist.mockResolvedValue(mockEntries);
    watchlistService.updateWatchlistEntry.mockResolvedValue({
      ...mockEntries[0],
      description: 'Updated case notes',
      severity: 'critical',
    });

    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText('GJ05AB1234')).toBeInTheDocument();
    });

    // Click Edit on first row
    const editButtons = screen.getAllByRole('button', { name: /^edit$/i });
    fireEvent.click(editButtons[0]);

    expect(screen.getByText('Edit Watchlist Entry')).toBeInTheDocument();
    const descInput = screen.getByLabelText(/Reason \/ Case Notes/i);
    expect(descInput.value).toBe('Suspect vehicle in commercial burglary');

    // Change description
    fireEvent.change(descInput, { target: { value: 'Updated case notes' } });

    // Submit edit
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(watchlistService.updateWatchlistEntry).toHaveBeenCalledWith(1, {
        plate_number: 'GJ05AB1234',
        description: 'Updated case notes',
        severity: 'critical',
        is_active: true,
      });
      expect(screen.getByText(/Watchlist entry for GJ05AB1234 updated successfully/i)).toBeInTheDocument();
    });
  });

  // 6: Deactivate and Delete actions work
  test('6: Quick toggle and delete confirmation work as expected', async () => {
    watchlistService.getWatchlist.mockResolvedValue(mockEntries);
    watchlistService.updateWatchlistEntry.mockResolvedValue({
      ...mockEntries[0],
      is_active: false,
    });
    watchlistService.deleteWatchlistEntry.mockResolvedValue({ message: 'Deleted' });

    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText('GJ05AB1234')).toBeInTheDocument();
    });

    // Quick toggle Deactivate on active row
    const deactivateBtn = screen.getByRole('button', { name: /^deactivate$/i });
    fireEvent.click(deactivateBtn);

    await waitFor(() => {
      expect(watchlistService.updateWatchlistEntry).toHaveBeenCalledWith(1, {
        is_active: false,
      });
      expect(screen.getByText(/Vehicle GJ05AB1234 is now INACTIVE/i)).toBeInTheDocument();
    });

    // Click Delete on first row
    const deleteButtons = screen.getAllByRole('button', { name: /^delete$/i });
    fireEvent.click(deleteButtons[0]);

    // Confirmation dialog opens
    expect(screen.getByText(/Remove from Watchlist/i)).toBeInTheDocument();
    expect(screen.getByText(/Are you sure you want to remove vehicle/i)).toBeInTheDocument();

    // Confirm permanent deletion
    const confirmDeleteBtn = screen.getByRole('button', { name: /delete permanently/i });
    fireEvent.click(confirmDeleteBtn);

    await waitFor(() => {
      expect(watchlistService.deleteWatchlistEntry).toHaveBeenCalledWith(1, false);
      expect(screen.getByText(/Vehicle GJ05AB1234 removed from watchlist/i)).toBeInTheDocument();
    });
  });

  // 7: Validation error is displayed
  test('7: Displays 422 validation error message from backend without crash', async () => {
    watchlistService.getWatchlist.mockResolvedValue([]);
    watchlistService.createWatchlistEntry.mockRejectedValue(
      new Error("Invalid Indian license plate format: 'INVALID-PLATE'. Must follow standard RTO or BH series syntax.")
    );

    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText(/no vehicles found/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /\+ add vehicle of interest/i }));

    fireEvent.change(screen.getByLabelText(/Indian License Plate Number/i), {
      target: { value: 'INVALID-PLATE' },
    });

    fireEvent.click(screen.getByRole('button', { name: /add to watchlist/i }));

    await waitFor(() => {
      expect(screen.getByText(/Invalid Indian license plate format/i)).toBeInTheDocument();
    });
  });

  // 8: Duplicate plate error is displayed
  test('8: Displays 409 duplicate plate conflict error cleanly', async () => {
    watchlistService.getWatchlist.mockResolvedValue([]);
    watchlistService.createWatchlistEntry.mockRejectedValue(
      new Error("Active watchlist entry for plate 'GJ05AB1234' already exists (ID: 1)")
    );

    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText(/no vehicles found/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: /\+ add vehicle of interest/i }));

    fireEvent.change(screen.getByLabelText(/Indian License Plate Number/i), {
      target: { value: 'GJ05AB1234' },
    });

    fireEvent.click(screen.getByRole('button', { name: /add to watchlist/i }));

    await waitFor(() => {
      expect(screen.getByText(/Active watchlist entry for plate 'GJ05AB1234' already exists/i)).toBeInTheDocument();
    });
  });

  // 9: Loading and API failure states
  test('9 & 10: Displays loading, error, and empty states appropriately', async () => {
    // 9a: Error on load
    watchlistService.getWatchlist.mockRejectedValue(new Error('Network failure: Unable to connect to the backend API.'));

    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText(/Error: Network failure: Unable to connect to the backend API/i)).toBeInTheDocument();
    });
    cleanup();

    // 10: Empty state
    watchlistService.getWatchlist.mockResolvedValue([]);
    render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText(/No vehicles found matching current filters/i)).toBeInTheDocument();
    });
  });

  // 11: Table wrapper and badge classes
  test('11: Watchlist table wrapper and badges have expected styling classes', async () => {
    watchlistService.getWatchlist.mockResolvedValue(mockEntries);

    const { container } = render(<WatchlistManager />);

    await waitFor(() => {
      expect(screen.getByText('GJ05AB1234')).toBeInTheDocument();
    });

    const wrapper = container.querySelector('.watchlist-table-wrapper');
    expect(wrapper).toBeInTheDocument();

    const table = container.querySelector('.watchlist-table');
    expect(table).toBeInTheDocument();

    const plateBadges = container.querySelectorAll('.plate-badge');
    expect(plateBadges.length).toBe(2);

    const activeBadge = container.querySelector('.status-active-badge');
    expect(activeBadge).toBeInTheDocument();
    expect(activeBadge).toHaveTextContent('ACTIVE');

    const inactiveBadge = container.querySelector('.status-inactive-badge');
    expect(inactiveBadge).toBeInTheDocument();
    expect(inactiveBadge).toHaveTextContent('INACTIVE');
  });
});
