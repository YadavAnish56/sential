import React, { useState, useEffect, useCallback } from 'react';
import { watchlistService } from '../api/watchlistService';

const SEVERITIES = ['critical', 'high', 'medium', 'low'];

export default function WatchlistManager() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [actionSuccess, setActionSuccess] = useState(null);

  // Filter & Search state
  const [searchQuery, setSearchQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState('all');
  const [severityFilter, setSeverityFilter] = useState('all');

  // Modal states
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [editingEntry, setEditingEntry] = useState(null); // null = Add mode, object = Edit mode
  const [deleteConfirmEntry, setDeleteConfirmEntry] = useState(null);

  // Form inputs
  const [formData, setFormData] = useState({
    plate_number: '',
    description: '',
    severity: 'high',
    is_active: true,
  });
  const [formError, setFormError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Fetch watchlist from backend
  const fetchWatchlist = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (searchQuery.trim()) params.search = searchQuery.trim();
      if (activeFilter !== 'all') params.is_active = activeFilter === 'true';
      if (severityFilter !== 'all') params.severity = severityFilter;

      const data = await watchlistService.getWatchlist(params);
      setEntries(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message || 'Failed to load surveillance watchlist.');
    } finally {
      setLoading(false);
    }
  }, [searchQuery, activeFilter, severityFilter]);

  useEffect(() => {
    fetchWatchlist();
  }, [fetchWatchlist]);

  // Open Add Modal
  const handleOpenAddModal = () => {
    setEditingEntry(null);
    setFormData({
      plate_number: '',
      description: '',
      severity: 'high',
      is_active: true,
    });
    setFormError(null);
    setIsModalOpen(true);
  };

  // Open Edit Modal
  const handleOpenEditModal = (entry) => {
    setEditingEntry(entry);
    setFormData({
      plate_number: entry.plate_number,
      description: entry.description || '',
      severity: entry.severity || 'high',
      is_active: entry.is_active,
    });
    setFormError(null);
    setIsModalOpen(true);
  };

  const handleCloseModal = () => {
    setIsModalOpen(false);
    setEditingEntry(null);
    setFormError(null);
  };

  // Handle Form Submit (Add or Edit)
  const handleFormSubmit = async (e) => {
    e.preventDefault();
    setFormError(null);
    setActionError(null);
    setActionSuccess(null);

    if (!formData.plate_number.trim()) {
      setFormError('Plate number is required.');
      return;
    }

    setIsSubmitting(true);
    try {
      if (editingEntry) {
        // Edit mode
        const updated = await watchlistService.updateWatchlistEntry(editingEntry.id, {
          plate_number: formData.plate_number.trim(),
          description: formData.description.trim() || null,
          severity: formData.severity,
          is_active: formData.is_active,
        });
        setActionSuccess(`Watchlist entry for ${updated.plate_number} updated successfully.`);
      } else {
        // Add mode
        const created = await watchlistService.createWatchlistEntry({
          plate_number: formData.plate_number.trim(),
          description: formData.description.trim() || null,
          severity: formData.severity,
          is_active: formData.is_active,
        });
        setActionSuccess(`Vehicle ${created.plate_number} added to surveillance watchlist.`);
      }

      handleCloseModal();
      fetchWatchlist();
    } catch (err) {
      setFormError(err.message || 'Operation failed. Please check plate format.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Quick Toggle Active/Inactive
  const handleToggleActive = async (entry) => {
    setActionError(null);
    setActionSuccess(null);
    try {
      const updated = await watchlistService.updateWatchlistEntry(entry.id, {
        is_active: !entry.is_active,
      });
      setActionSuccess(
        `Vehicle ${updated.plate_number} is now ${updated.is_active ? 'ACTIVE' : 'INACTIVE'}.`
      );
      fetchWatchlist();
    } catch (err) {
      setActionError(err.message || 'Failed to update vehicle status.');
    }
  };

  // Delete Action
  const handleDeleteConfirm = async () => {
    if (!deleteConfirmEntry) return;
    setActionError(null);
    setActionSuccess(null);
    try {
      await watchlistService.deleteWatchlistEntry(deleteConfirmEntry.id, false);
      setActionSuccess(`Vehicle ${deleteConfirmEntry.plate_number} removed from watchlist.`);
      setDeleteConfirmEntry(null);
      fetchWatchlist();
    } catch (err) {
      setActionError(err.message || 'Failed to delete watchlist entry.');
    }
  };

  // Deactivate instead of hard delete
  const handleDeactivateConfirm = async () => {
    if (!deleteConfirmEntry) return;
    setActionError(null);
    setActionSuccess(null);
    try {
      await watchlistService.deleteWatchlistEntry(deleteConfirmEntry.id, true);
      setActionSuccess(`Vehicle ${deleteConfirmEntry.plate_number} deactivated.`);
      setDeleteConfirmEntry(null);
      fetchWatchlist();
    } catch (err) {
      setActionError(err.message || 'Failed to deactivate watchlist entry.');
    }
  };

  return (
    <div className="watchlist-container">
      {/* Header */}
      <div className="watchlist-header">
        <div>
          <h2>Surveillance Watchlist</h2>
          <span className="watchlist-subtitle">
            FLAGGED TARGET VEHICLES FOR REAL-TIME ANPR ALERT MATCHING
          </span>
        </div>
        <button
          type="button"
          className="btn btn-primary btn-add-vehicle"
          onClick={handleOpenAddModal}
        >
          + ADD VEHICLE OF INTEREST
        </button>
      </div>

      {/* Action Messages */}
      {actionSuccess && (
        <div className="success-banner" role="status">
          ✓ {actionSuccess}
        </div>
      )}
      {actionError && (
        <div className="error-banner" role="alert">
          {actionError}
        </div>
      )}

      {/* Controls / Filter Bar */}
      <div className="watchlist-controls">
        <input
          type="text"
          placeholder="Search plate (e.g. GJ05, MH12)..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ minWidth: '220px', flex: '1 1 200px' }}
          aria-label="Search plate"
        />

        <select
          value={activeFilter}
          onChange={(e) => setActiveFilter(e.target.value)}
          aria-label="Filter status"
        >
          <option value="all">All Statuses</option>
          <option value="true">Active Only</option>
          <option value="false">Inactive Only</option>
        </select>

        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          aria-label="Filter severity"
        >
          <option value="all">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>

        {(searchQuery || activeFilter !== 'all' || severityFilter !== 'all') && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => {
              setSearchQuery('');
              setActiveFilter('all');
              setSeverityFilter('all');
            }}
          >
            Reset Filters
          </button>
        )}
      </div>

      {/* Main Table / States */}
      {loading && (
        <div className="loading" aria-busy="true">
          Loading surveillance watchlist...
        </div>
      )}

      {!loading && error && (
        <div className="error-banner" role="alert">
          Error: {error}
        </div>
      )}

      {!loading && !error && entries.length === 0 && (
        <div className="empty-state">
          <p>No vehicles found matching current filters.</p>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleOpenAddModal}>
            Add Vehicle of Interest
          </button>
        </div>
      )}

      {!loading && !error && entries.length > 0 && (
        <div className="watchlist-table-wrapper">
          <table className="watchlist-table">
            <thead>
              <tr>
                <th>PLATE</th>
                <th>DESCRIPTION</th>
                <th>SEVERITY</th>
                <th>STATUS</th>
                <th>ACTIONS</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>
                    <span className="plate-badge">{entry.plate_number}</span>
                  </td>
                  <td className="watchlist-notes-cell">
                    {entry.description || 'No description provided'}
                  </td>
                  <td>
                    <span className={`severity-badge severity-${entry.severity || 'high'}`}>
                      {entry.severity ? entry.severity.toUpperCase() : 'HIGH'}
                    </span>
                  </td>
                  <td>
                    {entry.is_active ? (
                      <span className="status-active-badge">ACTIVE</span>
                    ) : (
                      <span className="status-inactive-badge">INACTIVE</span>
                    )}
                  </td>
                  <td>
                    <div className="action-btns">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleOpenEditModal(entry)}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        className={`btn ${entry.is_active ? 'btn-warning' : 'btn-success'} btn-sm`}
                        onClick={() => handleToggleActive(entry)}
                        title={entry.is_active ? 'Deactivate from alerts' : 'Reactivate for alerts'}
                      >
                        {entry.is_active ? 'Deactivate' : 'Activate'}
                      </button>
                      <button
                        type="button"
                        className="btn btn-danger btn-sm"
                        onClick={() => setDeleteConfirmEntry(entry)}
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="watchlist-footer-notice" style={{ marginTop: '0.8rem', fontSize: '0.78rem', color: '#64748b' }}>
        Automated Alert Integration: Active watchlist targets are evaluated in real-time by the ANPR coordinator.
      </div>

      {/* Add/Edit Modal */}
      {isModalOpen && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal-card">
            <div className="modal-header">
              <h3>{editingEntry ? 'Edit Watchlist Entry' : 'Add Vehicle to Watchlist'}</h3>
              <button
                type="button"
                onClick={handleCloseModal}
                style={{ background: 'none', border: 'none', fontSize: '1.2rem', cursor: 'pointer' }}
                aria-label="Close modal"
              >
                ✕
              </button>
            </div>
            <form onSubmit={handleFormSubmit}>
              <div className="modal-body">
                {formError && (
                  <div className="error-banner" role="alert" style={{ marginBottom: 0 }}>
                    {formError}
                  </div>
                )}

                <div className="form-group">
                  <label htmlFor="plate_number">Indian License Plate Number *</label>
                  <input
                    id="plate_number"
                    type="text"
                    placeholder="e.g. GJ05AB1234 or 22BH1234AA"
                    value={formData.plate_number}
                    onChange={(e) => setFormData({ ...formData, plate_number: e.target.value })}
                    required
                    autoFocus
                  />
                  <small style={{ color: '#888' }}>
                    Standard Indian RTO or Bharat Series formats will be validated and normalized.
                  </small>
                </div>

                <div className="form-group">
                  <label htmlFor="severity">Alert Severity *</label>
                  <select
                    id="severity"
                    value={formData.severity}
                    onChange={(e) => setFormData({ ...formData, severity: e.target.value })}
                  >
                    {SEVERITIES.map((sev) => (
                      <option key={sev} value={sev}>
                        {sev.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="description">Reason / Case Notes</label>
                  <textarea
                    id="description"
                    rows="3"
                    placeholder="e.g. Suspect in hit-and-run incident at North Gate"
                    value={formData.description}
                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  />
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.25rem' }}>
                  <input
                    id="is_active"
                    type="checkbox"
                    checked={formData.is_active}
                    onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                    style={{ width: '18px', height: '18px', cursor: 'pointer' }}
                  />
                  <label htmlFor="is_active" style={{ cursor: 'pointer', fontWeight: 'bold' }}>
                    Active (triggers automated security alerts upon ANPR recognition)
                  </label>
                </div>
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleCloseModal}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
                  {isSubmitting
                    ? 'Saving...'
                    : editingEntry
                    ? 'Save Changes'
                    : 'Add to Watchlist'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete / Deactivate Confirmation Modal */}
      {deleteConfirmEntry && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal-card">
            <div className="modal-header">
              <h3>Remove from Watchlist</h3>
              <button
                type="button"
                onClick={() => setDeleteConfirmEntry(null)}
                style={{ background: 'none', border: 'none', fontSize: '1.2rem', cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <p style={{ margin: '0 0 1rem 0' }}>
                Are you sure you want to remove vehicle{' '}
                <strong>{deleteConfirmEntry.plate_number}</strong> from the surveillance watchlist?
              </p>
              <div style={{ fontSize: '0.85rem', color: '#666', background: '#f8f9fa', padding: '0.75rem', borderRadius: '4px' }}>
                <strong>TIP:</strong> If you only wish to temporarily stop alerts without removing
                historical context, choose <em>Deactivate</em> instead.
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setDeleteConfirmEntry(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-warning"
                onClick={handleDeactivateConfirm}
              >
                Deactivate Only
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={handleDeleteConfirm}
              >
                Delete Permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
