import React, { useState } from 'react';
import { cameraService } from '../api/cameraService';

export default function AddCameraModal({ isOpen, onClose, onCameraCreated }) {
  const [formData, setFormData] = useState({
    camera_code: '',
    name: '',
    location: '',
    latitude: '',
    longitude: '',
    stream_id_or_url: '',
    vendor: 'Gujarat Traffic Police',
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);
  const [formSuccess, setFormSuccess] = useState(null);

  if (!isOpen) return null;

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    setFormError(null);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormError(null);
    setFormSuccess(null);

    const code = formData.camera_code.trim().toUpperCase();
    const name = formData.name.trim();

    if (!code) {
      setFormError('Camera code is required (e.g. CAM-003).');
      return;
    }
    if (!name) {
      setFormError('Camera name is required.');
      return;
    }

    const rawStream = formData.stream_id_or_url.trim();

    // Security check: NEVER allow credentials in stream URL
    if (rawStream && (rawStream.includes('@') || /:\/\/[^/]+:[^/]+@/.test(rawStream))) {
      setFormError(
        'SECURITY VIOLATION: RTSP credentials must NEVER be entered in camera forms or stored in the database. Credentials are held server-side only.'
      );
      return;
    }

    // Resolve sanitized stream URL
    let sanitizedStreamUrl = null;
    if (rawStream) {
      if (rawStream.startsWith('rtsp://') || rawStream.startsWith('http://') || rawStream.startsWith('https://')) {
        sanitizedStreamUrl = rawStream;
      } else {
        // Stream ID provided, resolve standard gateway format
        const cleanId = rawStream.replace(/^\/+/, '');
        sanitizedStreamUrl = `rtsp://103.250.160.189:8554/stream/${cleanId}`;
      }
    }

    // Parse coordinates if provided
    let lat = null;
    let lon = null;
    if (formData.latitude !== '') {
      const parsedLat = parseFloat(formData.latitude);
      if (isNaN(parsedLat) || parsedLat < -90 || parsedLat > 90) {
        setFormError('Latitude must be a valid number between -90 and 90 degrees.');
        return;
      }
      lat = parsedLat;
    }
    if (formData.longitude !== '') {
      const parsedLon = parseFloat(formData.longitude);
      if (isNaN(parsedLon) || parsedLon < -180 || parsedLon > 180) {
        setFormError('Longitude must be a valid number between -180 and 180 degrees.');
        return;
      }
      lon = parsedLon;
    }

    const payload = {
      camera_code: code,
      name: name,
      location: formData.location.trim() || null,
      latitude: lat,
      longitude: lon,
      stream_url: sanitizedStreamUrl,
      status: 'offline',
      vendor: formData.vendor.trim() || null,
    };

    setIsSubmitting(true);
    try {
      const result = await cameraService.createCamera(payload);
      setFormSuccess(`Camera ${result.camera_code || code} registered successfully.`);
      if (onCameraCreated) {
        onCameraCreated(result);
      }
      setTimeout(() => {
        onClose();
      }, 1000);
    } catch (err) {
      setFormError(err.message || 'Failed to register camera. Code may already exist.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="command-modal-overlay" role="dialog" aria-modal="true" data-testid="add-camera-modal">
      <div className="command-modal-window add-camera-window">
        <div className="command-modal-header">
          <div>
            <h3>CAMERA MANAGEMENT • ONBOARD NODE</h3>
            <span className="modal-header-sub">REGISTER CCTV SENSOR NODE TO GUJARAT POLICE SURVEILLANCE GRID</span>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close modal"
          >
            ✕
          </button>
        </div>

        <div className="command-modal-body">
          <div className="security-notice-banner">
            <span className="notice-tag">[SECURITY PROTOCOL]</span>
            <span>Zero-Credential Storage: Credentials must remain server-side. Do not enter RTSP passwords.</span>
          </div>

          {formError && (
            <div className="error-banner" role="alert">
              <strong>Error: </strong> {formError}
            </div>
          )}

          {formSuccess && (
            <div className="success-banner" role="alert">
              {formSuccess}
            </div>
          )}

          <form onSubmit={handleSubmit} className="add-camera-form">
            <div className="form-row-grid">
              <div className="form-field">
                <label htmlFor="camera_code">CAMERA CODE *</label>
                <input
                  id="camera_code"
                  name="camera_code"
                  type="text"
                  placeholder="e.g. CAM-031 or cam31"
                  value={formData.camera_code}
                  onChange={handleChange}
                  required
                  autoFocus
                />
                <small className="field-hint">Unique hardware/catalogue identifier</small>
              </div>

              <div className="form-field">
                <label htmlFor="name">CAMERA / JUNCTION NAME *</label>
                <input
                  id="name"
                  name="name"
                  type="text"
                  placeholder="e.g. SG Highway - Pakwan Cross Road"
                  value={formData.name}
                  onChange={handleChange}
                  required
                />
                <small className="field-hint">Descriptive checkpoint display name</small>
              </div>
            </div>

            <div className="form-row-grid">
              <div className="form-field">
                <label htmlFor="location">LOCATION / DISTRICT</label>
                <input
                  id="location"
                  name="location"
                  type="text"
                  placeholder="e.g. Ahmedabad, Surat, Vadodara"
                  value={formData.location}
                  onChange={handleChange}
                />
              </div>

              <div className="form-field">
                <label htmlFor="vendor">DEPARTMENT / VENDOR</label>
                <input
                  id="vendor"
                  name="vendor"
                  type="text"
                  placeholder="e.g. Gujarat Traffic Police"
                  value={formData.vendor}
                  onChange={handleChange}
                />
              </div>
            </div>

            <div className="form-row-grid">
              <div className="form-field">
                <label htmlFor="latitude">LATITUDE (GPS -90 to 90)</label>
                <input
                  id="latitude"
                  name="latitude"
                  type="number"
                  step="any"
                  placeholder="e.g. 23.0305"
                  value={formData.latitude}
                  onChange={handleChange}
                />
              </div>

              <div className="form-field">
                <label htmlFor="longitude">LONGITUDE (GPS -180 to 180)</label>
                <input
                  id="longitude"
                  name="longitude"
                  type="number"
                  step="any"
                  placeholder="e.g. 72.5274"
                  value={formData.longitude}
                  onChange={handleChange}
                />
              </div>
            </div>

            <div className="form-field">
              <label htmlFor="stream_id_or_url">STREAM IDENTIFIER OR SANITIZED RTSP URL</label>
              <input
                id="stream_id_or_url"
                name="stream_id_or_url"
                type="text"
                placeholder="e.g. cam31 OR rtsp://103.250.160.189:8554/stream/cam31"
                value={formData.stream_id_or_url}
                onChange={handleChange}
              />
              <small className="field-hint">
                Enter stream path or non-credentialed URL. For government feeds, catalogue sync is authoritative.
              </small>
            </div>

            <div className="modal-actions-deck">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={onClose}
                disabled={isSubmitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Registering Node...' : 'Register Camera Node'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
