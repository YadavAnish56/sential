import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { analyticsService } from '../api/analyticsService';
import { alertService } from '../api/alertService';
import { watchlistService } from '../api/watchlistService';
import { cameraService } from '../api/cameraService';

export default function RecordsWorkspace({ onSelectPlate = null, onSelectCamera = null }) {
  const [activeTab, setActiveTab] = useState('vehicles'); // 'vehicles' | 'events' | 'alerts' | 'archive' | 'watchlist'

  // Data states
  const [vehicles, setVehicles] = useState([]);
  const [events, setEvents] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [cameras, setCameras] = useState({});

  // Summary counts
  const [totalVehiclesCount, setTotalVehiclesCount] = useState(0);
  const [totalEventsCount, setTotalEventsCount] = useState(0);
  const [activeAlertsCount, setActiveAlertsCount] = useState(0);
  const [activeWatchlistCount, setActiveWatchlistCount] = useState(0);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchFilter, setSearchFilter] = useState('');

  // Historical Audit Archive specific filters
  const [archiveDateStart, setArchiveDateStart] = useState('');
  const [archiveDateEnd, setArchiveDateEnd] = useState('');
  const [archiveCameraFilter, setArchiveCameraFilter] = useState('ALL');
  const [archivePlateQuery, setArchivePlateQuery] = useState('');

  // Initial load of telemetry metrics & camera metadata
  const loadGlobalMetadata = useCallback(async () => {
    try {
      const [camsData, watchlistData] = await Promise.all([
        cameraService.getCameras().catch(() => ({ cameras: [] })),
        watchlistService.getWatchlist().catch(() => []),
      ]);

      const camList = Array.isArray(camsData) ? camsData : (camsData?.cameras || []);
      const camMap = {};
      camList.forEach((c) => {
        const id = c.id ?? c.camera_id;
        camMap[id] = {
          code: c.camera_code ?? `cam${id}`,
          name: c.name ?? `Camera ${id}`,
          location: c.location || null,
        };
      });
      setCameras(camMap);

      const activeWl = (watchlistData || []).filter((w) => w.is_active !== false);
      setWatchlist(watchlistData || []);
      setActiveWatchlistCount(activeWl.length);
    } catch (_) {
      // Non-blocking
    }
  }, []);

  // Fetch data depending on active tab & update metrics
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (activeTab === 'vehicles') {
        const data = await analyticsService.getVehicles(100);
        const list = Array.isArray(data) ? data : [];
        setVehicles(list);
        setTotalVehiclesCount(list.length);
      } else if (activeTab === 'events' || activeTab === 'archive') {
        const data = await analyticsService.getRecentEvents(100);
        const list = Array.isArray(data?.events) ? data.events : (Array.isArray(data) ? data : []);
        setEvents(list);
        setTotalEventsCount(data?.total ?? list.length);
      } else if (activeTab === 'alerts') {
        const data = await alertService.getAlerts(100, 0);
        const list = Array.isArray(data?.alerts) ? data.alerts : (Array.isArray(data) ? data : []);
        setAlerts(list);
        const unack = list.filter((a) => (a.status || '').toLowerCase() !== 'acknowledged');
        setActiveAlertsCount(unack.length);
      } else if (activeTab === 'watchlist') {
        const data = await watchlistService.getWatchlist();
        const list = Array.isArray(data) ? data : [];
        setWatchlist(list);
        setActiveWatchlistCount(list.filter((w) => w.is_active !== false).length);
      }
    } catch (err) {
      setError(err.message || `Failed to fetch ${activeTab} records.`);
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    loadGlobalMetadata();
  }, [loadGlobalMetadata]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Acknowledge alert handler
  const handleAcknowledgeAlert = async (alertId) => {
    try {
      await alertService.acknowledgeAlert(alertId);
      fetchData();
    } catch (err) {
      console.error('Failed to acknowledge alert:', err);
    }
  };

  // Set of watchlist plates for real match detection
  const watchlistSet = useMemo(() => {
    return new Set(
      watchlist
        .filter((w) => w.is_active !== false)
        .map((w) => (w.plate_number || '').trim().toUpperCase())
    );
  }, [watchlist]);

  // Filtered lists for primary tabs
  const filteredVehicles = vehicles.filter((v) =>
    (v.plate_number || '').toLowerCase().includes(searchFilter.toLowerCase())
  );

  const filteredEvents = events.filter((e) =>
    (e.plate_number || '').toLowerCase().includes(searchFilter.toLowerCase()) ||
    (e.camera_id ? String(e.camera_id) : '').includes(searchFilter) ||
    (e.event_type || '').toLowerCase().includes(searchFilter.toLowerCase())
  );

  const filteredAlerts = alerts.filter((a) =>
    (a.plate_number || '').toLowerCase().includes(searchFilter.toLowerCase()) ||
    (a.alert_type || '').toLowerCase().includes(searchFilter.toLowerCase()) ||
    (a.severity || '').toLowerCase().includes(searchFilter.toLowerCase())
  );

  const filteredWatchlist = watchlist.filter((w) =>
    (w.plate_number || '').toLowerCase().includes(searchFilter.toLowerCase()) ||
    (w.description || '').toLowerCase().includes(searchFilter.toLowerCase())
  );

  // Filtered list for Historical Audit Archive
  const filteredAuditEvents = useMemo(() => {
    return events.filter((evt) => {
      // Camera filter
      if (archiveCameraFilter !== 'ALL') {
        if (String(evt.camera_id) !== String(archiveCameraFilter)) return false;
      }

      // Plate filter
      if (archivePlateQuery.trim()) {
        const q = archivePlateQuery.trim().toLowerCase();
        const plate = (evt.plate_number || '').toLowerCase();
        if (!plate.includes(q)) return false;
      }

      // Date Range filter
      if (archiveDateStart) {
        const startMs = new Date(archiveDateStart).getTime();
        const itemMs = new Date(evt.timestamp).getTime();
        if (itemMs < startMs) return false;
      }
      if (archiveDateEnd) {
        const endMs = new Date(archiveDateEnd).getTime();
        const itemMs = new Date(evt.timestamp).getTime();
        if (itemMs > endMs) return false;
      }

      return true;
    });
  }, [events, archiveCameraFilter, archivePlateQuery, archiveDateStart, archiveDateEnd]);

  // CSV Export for Historical Audit Archive
  const handleExportCsv = () => {
    const headers = [
      'Camera ID',
      'Camera Code',
      'Plate',
      'Timestamp (ISO)',
      'PTS (ms)',
      'Confidence (%)',
      'Vehicle Type',
      'Make',
      'Model',
      'Color',
      'Alert State',
    ];

    const rows = filteredAuditEvents.map((e) => {
      const camMeta = cameras[e.camera_id] || {};
      const plate = (e.plate_number || 'UNKNOWN').trim().toUpperCase();
      const isAlert = watchlistSet.has(plate);
      const conf = e.confidence != null ? (e.confidence * 100).toFixed(1) : 'N/A';
      const pts = e.pts ?? e.pts_ms ?? 'N/A';
      const vType = (e.vehicle_type || e.object_type || 'VEHICLE').toUpperCase();
      const make = e.make ? String(e.make).toUpperCase() : 'NOT AVAILABLE';
      const model = e.model ? String(e.model).toUpperCase() : 'NOT AVAILABLE';
      const color = e.color ? String(e.color).toUpperCase() : 'NOT AVAILABLE';
      const time = e.timestamp ? new Date(e.timestamp).toISOString() : '';

      return [
        `"CAM-${e.camera_id}"`,
        `"${camMeta.code || `cam${e.camera_id}`}"`,
        `"${plate}"`,
        `"${time}"`,
        `"${pts}"`,
        `"${conf}"`,
        `"${vType}"`,
        `"${make}"`,
        `"${model}"`,
        `"${color}"`,
        `"${isAlert ? 'WATCHLIST MATCH' : 'STANDARD'}"`,
      ].join(',');
    });

    const csvContent = [headers.join(','), ...rows].join('\r\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `sentinel_audit_archive_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="records-workspace-container" data-testid="records-workspace">
      {/* 1. TOP OPERATIONAL INTELLIGENCE METRICS DECK */}
      <div className="records-metrics-deck" data-testid="records-metrics-deck">
        <div className="metric-chip">
          <span className="metric-key">TOTAL VEHICLES:</span>
          <strong className="metric-val" data-testid="metric-total-vehicles">
            {totalVehiclesCount || vehicles.length}
          </strong>
        </div>
        <div className="metric-chip">
          <span className="metric-key">TOTAL DETECTION EVENTS:</span>
          <strong className="metric-val" data-testid="metric-total-events">
            {totalEventsCount || events.length}
          </strong>
        </div>
        <div className="metric-chip">
          <span className="metric-key">ACTIVE ALERTS:</span>
          <strong className="metric-val text-warning" data-testid="metric-active-alerts">
            {activeAlertsCount || alerts.length}
          </strong>
        </div>
        <div className="metric-chip">
          <span className="metric-key">ACTIVE WATCHLIST TARGETS:</span>
          <strong className="metric-val" data-testid="metric-watchlist-targets">
            {activeWatchlistCount || watchlist.length}
          </strong>
        </div>
      </div>

      {/* 2. HEADER & SUB-TABS */}
      <div className="records-header-deck">
        <div className="records-title-group">
          <span className="c2-label">PERSISTENT SURVEILLANCE RECORDS & AUDIT ARCHIVE</span>
          <span className="c2-sublabel">
            POSTGRESQL AUDIT ARCHIVE (LIVE DETECTION LOGS ONLY — NO RAW VIDEO STORED)
          </span>
        </div>

        <div className="records-tab-bar" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'vehicles'}
            className={`records-tab-btn ${activeTab === 'vehicles' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('vehicles');
              setSearchFilter('');
            }}
          >
            VEHICLES ({vehicles.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'events'}
            className={`records-tab-btn ${activeTab === 'events' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('events');
              setSearchFilter('');
            }}
          >
            DETECTION EVENTS ({events.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'alerts'}
            className={`records-tab-btn ${activeTab === 'alerts' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('alerts');
              setSearchFilter('');
            }}
          >
            SECURITY ALERTS ({alerts.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'archive'}
            className={`records-tab-btn ${activeTab === 'archive' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('archive');
              setSearchFilter('');
            }}
          >
            HISTORICAL AUDIT ARCHIVE
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'watchlist'}
            className={`records-tab-btn ${activeTab === 'watchlist' ? 'active' : ''}`}
            onClick={() => {
              setActiveTab('watchlist');
              setSearchFilter('');
            }}
          >
            ACTIVE WATCHLIST ({watchlist.length})
          </button>
        </div>
      </div>

      {/* 3. FILTER / SEARCH ROW (For primary tabs) */}
      {activeTab !== 'archive' && (
        <div className="records-filter-bar">
          <input
            type="text"
            placeholder={`Filter ${activeTab} records by plate, camera, or type...`}
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            className="c2-input records-search-input"
            aria-label="Filter records"
          />
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={fetchData}
            disabled={loading}
          >
            {loading ? 'Refreshing...' : 'Refresh Records'}
          </button>
        </div>
      )}

      {/* 4. HISTORICAL AUDIT ARCHIVE FILTER DECK */}
      {activeTab === 'archive' && (
        <div className="archive-filter-deck" data-testid="archive-filter-deck">
          <div className="archive-filter-row">
            <div className="c2-field-group">
              <label htmlFor="archive-plate-filter">TARGET PLATE</label>
              <input
                id="archive-plate-filter"
                type="text"
                placeholder="e.g. KA02MM9091"
                value={archivePlateQuery}
                onChange={(e) => setArchivePlateQuery(e.target.value.toUpperCase())}
                className="c2-input"
              />
            </div>

            <div className="c2-field-group">
              <label htmlFor="archive-camera-select">CAMERA NODE</label>
              <select
                id="archive-camera-select"
                className="c2-select"
                value={archiveCameraFilter}
                onChange={(e) => setArchiveCameraFilter(e.target.value)}
              >
                <option value="ALL">All Cameras</option>
                {Object.entries(cameras).map(([id, cam]) => (
                  <option key={id} value={id}>
                    {cam.code} ({cam.name})
                  </option>
                ))}
              </select>
            </div>

            <div className="c2-field-group">
              <label htmlFor="archive-start-time">START DATE/TIME</label>
              <input
                id="archive-start-time"
                type="datetime-local"
                value={archiveDateStart}
                onChange={(e) => setArchiveDateStart(e.target.value)}
                className="c2-input c2-date"
              />
            </div>

            <div className="c2-field-group">
              <label htmlFor="archive-end-time">END DATE/TIME</label>
              <input
                id="archive-end-time"
                type="datetime-local"
                value={archiveDateEnd}
                onChange={(e) => setArchiveDateEnd(e.target.value)}
                className="c2-input c2-date"
              />
            </div>

            <div className="archive-actions-group">
              <button
                type="button"
                className="btn btn-primary btn-sm btn-export-csv"
                onClick={handleExportCsv}
                disabled={filteredAuditEvents.length === 0}
                title="Download CSV export of filtered audit events"
              >
                EXPORT AUDIT LOG (CSV)
              </button>
            </div>
          </div>

          <div className="archive-audit-notice">
            AI DETECTION AUDIT ARCHIVE (METADATA EVENT LOG ONLY — NO RAW VIDEO STORED) •{' '}
            <strong>{filteredAuditEvents.length} MATCHING EVENTS</strong>
          </div>
        </div>
      )}

      {/* 5. BODY TABLE VIEW */}
      <div className="records-body-table-wrapper">
        {loading && <div className="loading">Loading {activeTab} records from PostgreSQL...</div>}

        {error && (
          <div className="error-banner" role="alert">
            <strong>Database Error:</strong> {error}
          </div>
        )}

        {/* TAB 1: VEHICLES */}
        {!loading && !error && activeTab === 'vehicles' && (
          filteredVehicles.length === 0 ? (
            <p className="empty-state">No vehicle records found in database.</p>
          ) : (
            <table className="records-table" data-testid="vehicles-table">
              <thead>
                <tr>
                  <th>REGISTRATION PLATE</th>
                  <th>FIRST SEEN</th>
                  <th>LAST SEEN</th>
                  <th>SIGHTING COUNT</th>
                  <th>VEHICLE TYPE</th>
                  <th>MAKE</th>
                  <th>MODEL</th>
                  <th>COLOR</th>
                  <th>ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {filteredVehicles.map((v) => {
                  const makeStr = v.make ? String(v.make).toUpperCase() : 'NOT AVAILABLE';
                  const modelStr = v.model ? String(v.model).toUpperCase() : 'NOT AVAILABLE';
                  const colorStr = v.color ? String(v.color).toUpperCase() : 'NOT AVAILABLE';

                  return (
                    <tr key={v.id}>
                      <td>
                        <span className="plate-badge">{v.plate_number}</span>
                      </td>
                      <td>{v.first_seen ? new Date(v.first_seen).toLocaleString() : 'N/A'}</td>
                      <td>{v.last_seen ? new Date(v.last_seen).toLocaleString() : 'N/A'}</td>
                      <td>{v.sighting_count != null ? `${v.sighting_count} sightings` : 'N/A'}</td>
                      <td>{(v.vehicle_type || 'VEHICLE').toUpperCase()}</td>
                      <td className={makeStr === 'NOT AVAILABLE' ? 'text-muted' : ''}>{makeStr}</td>
                      <td className={modelStr === 'NOT AVAILABLE' ? 'text-muted' : ''}>{modelStr}</td>
                      <td className={colorStr === 'NOT AVAILABLE' ? 'text-muted' : ''}>{colorStr}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-xs btn-primary"
                          onClick={() => onSelectPlate && onSelectPlate(v.plate_number)}
                        >
                          Investigate Plate
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        )}

        {/* TAB 2: EVENTS */}
        {!loading && !error && activeTab === 'events' && (
          filteredEvents.length === 0 ? (
            <p className="empty-state">No detection events recorded in database.</p>
          ) : (
            <table className="records-table" data-testid="events-table">
              <thead>
                <tr>
                  <th>CAMERA</th>
                  <th>TIMESTAMP</th>
                  <th>PTS</th>
                  <th>CONFIDENCE</th>
                  <th>VEHICLE TYPE</th>
                  <th>PLATE</th>
                  <th>MAKE</th>
                  <th>MODEL</th>
                  <th>COLOR</th>
                  <th>ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {filteredEvents.map((e) => {
                  const makeStr = e.make ? String(e.make).toUpperCase() : 'NOT AVAILABLE';
                  const modelStr = e.model ? String(e.model).toUpperCase() : 'NOT AVAILABLE';
                  const colorStr = e.color ? String(e.color).toUpperCase() : 'NOT AVAILABLE';

                  return (
                    <tr key={e.id}>
                      <td>
                        <strong>CAM-{e.camera_id}</strong>
                      </td>
                      <td>{new Date(e.timestamp).toLocaleString()}</td>
                      <td>
                        {e.pts != null ? `${e.pts} ms` : (e.pts_ms != null ? `${e.pts_ms} ms` : 'N/A')}
                      </td>
                      <td>{e.confidence != null ? (e.confidence * 100).toFixed(1) + '%' : 'N/A'}</td>
                      <td>{(e.vehicle_type || e.event_type || 'NOT AVAILABLE').toUpperCase()}</td>
                      <td>
                        {e.plate_number ? (
                          <span className="plate-badge">{e.plate_number}</span>
                        ) : (
                          <span className="text-muted">NONE</span>
                        )}
                      </td>
                      <td className={makeStr === 'NOT AVAILABLE' ? 'text-muted' : ''}>{makeStr}</td>
                      <td className={modelStr === 'NOT AVAILABLE' ? 'text-muted' : ''}>{modelStr}</td>
                      <td className={colorStr === 'NOT AVAILABLE' ? 'text-muted' : ''}>{colorStr}</td>
                      <td>
                        <div className="btn-group-row">
                          {e.plate_number && (
                            <button
                              type="button"
                              className="btn btn-xs btn-primary"
                              onClick={() => onSelectPlate && onSelectPlate(e.plate_number)}
                            >
                              Investigate
                            </button>
                          )}
                          <button
                            type="button"
                            className="btn btn-xs btn-secondary"
                            onClick={() => onSelectCamera && onSelectCamera(e.camera_id)}
                          >
                            Focus Cam
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        )}

        {/* TAB 3: ALERTS */}
        {!loading && !error && activeTab === 'alerts' && (
          filteredAlerts.length === 0 ? (
            <p className="empty-state">No security alerts logged.</p>
          ) : (
            <table className="records-table" data-testid="alerts-table">
              <thead>
                <tr>
                  <th>ALERT ID</th>
                  <th>SEVERITY</th>
                  <th>TYPE</th>
                  <th>CAMERA</th>
                  <th>TIMESTAMP</th>
                  <th>TARGET VEHICLE</th>
                  <th>STATUS</th>
                  <th>ACTION</th>
                </tr>
              </thead>
              <tbody>
                {filteredAlerts.map((a) => {
                  const isAck = (a.status || '').toLowerCase() === 'acknowledged';
                  return (
                    <tr key={a.id}>
                      <td>#{a.id}</td>
                      <td>
                        <span className={`severity-badge severity-${(a.severity || 'high').toLowerCase()}`}>
                          {(a.severity || 'HIGH').toUpperCase()}
                        </span>
                      </td>
                      <td>{a.alert_type || 'WATCHLIST_MATCH'}</td>
                      <td>{a.camera_id ? `CAM-${a.camera_id}` : (a.camera_code || 'N/A')}</td>
                      <td>{new Date(a.created_at || a.timestamp).toLocaleString()}</td>
                      <td>
                        {a.plate_number ? (
                          <span className="plate-badge">{a.plate_number}</span>
                        ) : (
                          'N/A'
                        )}
                      </td>
                      <td>
                        <span className={`status-badge ${isAck ? 'status-stopped' : 'status-running'}`}>
                          {isAck ? 'ACKNOWLEDGED' : 'ACTIVE / UNACK'}
                        </span>
                      </td>
                      <td>
                        <div className="btn-group-row">
                          {!isAck && (
                            <button
                              type="button"
                              className="btn btn-xs btn-warning"
                              onClick={() => handleAcknowledgeAlert(a.id)}
                            >
                              Acknowledge
                            </button>
                          )}
                          {a.plate_number && (
                            <button
                              type="button"
                              className="btn btn-xs btn-primary"
                              onClick={() => onSelectPlate && onSelectPlate(a.plate_number)}
                            >
                              Investigate
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        )}

        {/* TAB 4: HISTORICAL AUDIT ARCHIVE */}
        {!loading && !error && activeTab === 'archive' && (
          filteredAuditEvents.length === 0 ? (
            <p className="empty-state">No historical detection events match the archive filter criteria.</p>
          ) : (
            <table className="records-table" data-testid="archive-table">
              <thead>
                <tr>
                  <th>CAMERA</th>
                  <th>TIMESTAMP</th>
                  <th>PTS</th>
                  <th>PLATE</th>
                  <th>CONFIDENCE</th>
                  <th>VEHICLE TYPE</th>
                  <th>ALERT STATE</th>
                  <th>ACTIONS</th>
                </tr>
              </thead>
              <tbody>
                {filteredAuditEvents.map((evt) => {
                  const camMeta = cameras[evt.camera_id] || {};
                  const isHit = watchlistSet.has((evt.plate_number || '').trim().toUpperCase());

                  return (
                    <tr key={evt.id || `${evt.camera_id}-${evt.timestamp}`}>
                      <td>
                        <strong>{camMeta.code || `CAM-${evt.camera_id}`}</strong>
                        {camMeta.name && <span className="cam-subname"> ({camMeta.name})</span>}
                      </td>
                      <td>{new Date(evt.timestamp).toLocaleString()}</td>
                      <td>
                        {evt.pts != null ? `${evt.pts} ms` : (evt.pts_ms != null ? `${evt.pts_ms} ms` : 'N/A')}
                      </td>
                      <td>
                        {evt.plate_number ? (
                          <span className="plate-badge">{evt.plate_number}</span>
                        ) : (
                          <span className="text-muted">NONE</span>
                        )}
                      </td>
                      <td>{evt.confidence != null ? `${(evt.confidence * 100).toFixed(1)}%` : 'N/A'}</td>
                      <td>{(evt.vehicle_type || evt.object_type || 'VEHICLE').toUpperCase()}</td>
                      <td>
                        {isHit ? (
                          <span className="watchlist-hit-pill">WATCHLIST MATCH</span>
                        ) : (
                          <span className="status-badge status-stopped">STANDARD</span>
                        )}
                      </td>
                      <td>
                        <div className="btn-group-row">
                          {evt.plate_number && (
                            <button
                              type="button"
                              className="btn btn-xs btn-primary"
                              onClick={() => onSelectPlate && onSelectPlate(evt.plate_number)}
                            >
                              Investigate
                            </button>
                          )}
                          <button
                            type="button"
                            className="btn btn-xs btn-secondary"
                            onClick={() => onSelectCamera && onSelectCamera(evt.camera_id)}
                          >
                            Focus Cam
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )
        )}

        {/* TAB 5: WATCHLIST */}
        {!loading && !error && activeTab === 'watchlist' && (
          filteredWatchlist.length === 0 ? (
            <p className="empty-state">No vehicles listed in active surveillance watchlist.</p>
          ) : (
            <table className="records-table">
              <thead>
                <tr>
                  <th>TARGET PLATE</th>
                  <th>SEVERITY</th>
                  <th>REASON / NOTES</th>
                  <th>STATUS</th>
                  <th>ACTION</th>
                </tr>
              </thead>
              <tbody>
                {filteredWatchlist.map((w) => (
                  <tr key={w.id}>
                    <td>
                      <span className="plate-badge">{w.plate_number}</span>
                    </td>
                    <td>
                      <span className={`severity-badge severity-${(w.severity || 'high').toLowerCase()}`}>
                        {(w.severity || 'HIGH').toUpperCase()}
                      </span>
                    </td>
                    <td>{w.description || 'No reason specified'}</td>
                    <td>
                      <span className={`status-badge ${w.is_active ? 'status-running' : 'status-stopped'}`}>
                        {w.is_active ? 'ACTIVE' : 'INACTIVE'}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn-xs btn-primary"
                        onClick={() => onSelectPlate && onSelectPlate(w.plate_number)}
                      >
                        Investigate
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  );
}
