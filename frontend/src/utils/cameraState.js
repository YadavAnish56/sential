/**
 * cameraState.js
 * Centralized state resolution and classification utility for Sentinel CCTV Command Center.
 * Strictly separates VIDEO STREAM state from AI PIPELINE state.
 * Formats human-readable operational errors without raw Python exception dumps.
 */

/**
 * Check if a camera is a test, mock, or development node without an active production stream.
 * @param {Object} camera 
 * @returns {boolean}
 */
export function isTestCamera(camera) {
  if (!camera) return false;
  const code = (camera.camera_code || '').toUpperCase();
  const name = (camera.name || '').toUpperCase();
  const streamUrl = (camera.stream_url || '').toLowerCase();

  return (
    code.includes('CRUD') ||
    code.includes('TEST') ||
    code.includes('MOCK') ||
    code.includes('DEV') ||
    name.includes('CRUD') ||
    name.includes('TEST') ||
    name.includes('MOCK') ||
    streamUrl === 'rtsp://crud' ||
    streamUrl.startsWith('rtsp://test') ||
    streamUrl.startsWith('test')
  );
}

/**
 * Determine the exact VIDEO STREAM STATE.
 * Standard states: LIVE | STANDBY | OFFLINE | AUTH REQUIRED | UPSTREAM NOT FOUND
 * 
 * @param {Object} camera 
 * @param {string|null} previewError 
 * @param {boolean} isStreaming 
 * @returns {{ state: string, label: string, colorClass: string, reason: string|null }}
 */
export function resolveVideoState(camera, previewError = null, isStreaming = false) {
  if (!camera) {
    return {
      state: 'OFFLINE',
      label: 'VIDEO: OFFLINE',
      colorClass: 'state-offline',
      reason: 'No camera selected'
    };
  }

  // Explicit test camera handling
  if (isTestCamera(camera)) {
    return {
      state: 'OFFLINE',
      label: 'VIDEO: OFFLINE (TEST NODE)',
      colorClass: 'state-offline',
      reason: 'Test camera has no active live video stream configured'
    };
  }

  // WHEP connection errors take precedence
  if (previewError) {
    const errUpper = String(previewError).toUpperCase();
    if (errUpper.includes('AUTHENTICATION') || errUpper.includes('AUTH') || errUpper.includes('401')) {
      return {
        state: 'AUTH REQUIRED',
        label: 'VIDEO: AUTH REQUIRED',
        colorClass: 'state-auth-required',
        reason: 'Stream gateway authentication required. Server-side credentials missing or rejected.'
      };
    }
    if (errUpper.includes('NOT FOUND') || errUpper.includes('UPSTREAM_NOT_FOUND') || errUpper.includes('404')) {
      return {
        state: 'UPSTREAM NOT FOUND',
        label: 'VIDEO: UPSTREAM NOT FOUND',
        colorClass: 'state-upstream-not-found',
        reason: 'Stream route does not exist on gateway or camera is unreachable.'
      };
    }
    return {
      state: 'OFFLINE',
      label: 'VIDEO: OFFLINE',
      colorClass: 'state-offline',
      reason: previewError
    };
  }

  // Active streaming video
  if (isStreaming) {
    return {
      state: 'LIVE',
      label: 'VIDEO: LIVE',
      colorClass: 'state-live',
      reason: null
    };
  }

  // Registered camera connectivity status
  const rawStatus = (camera.status || camera.connectivity_status || '').toLowerCase();
  if (rawStatus === 'online') {
    return {
      state: 'LIVE',
      label: 'VIDEO: LIVE',
      colorClass: 'state-live',
      reason: null
    };
  }

  if (rawStatus === 'standby' || rawStatus === 'active') {
    return {
      state: 'STANDBY',
      label: 'VIDEO: STANDBY',
      colorClass: 'state-standby',
      reason: 'Camera registered and waiting for operator stream engagement'
    };
  }

  return {
    state: 'OFFLINE',
    label: 'VIDEO: OFFLINE',
    colorClass: 'state-offline',
    reason: 'Camera stream disconnected or node marked offline'
  };
}

/**
 * Determine the exact AI PIPELINE STATE.
 * Standard states: ON | RUNNING | STARTING | STOPPED | ERROR | UNAVAILABLE
 * 
 * @param {Object} camera 
 * @param {Object|null} pipelineStatus 
 * @returns {{ state: string, label: string, colorClass: string, reason: string|null }}
 */
export function resolveAiState(camera, pipelineStatus = null) {
  if (!camera) {
    return {
      state: 'UNAVAILABLE',
      label: 'AI: UNAVAILABLE',
      colorClass: 'ai-state-unavailable',
      reason: 'No camera selected'
    };
  }

  if (isTestCamera(camera)) {
    return {
      state: 'UNAVAILABLE',
      label: 'AI: UNAVAILABLE',
      colorClass: 'ai-state-unavailable',
      reason: 'Test camera has no active stream configured for AI inference'
    };
  }

  const rawStatus = (pipelineStatus?.status || 'stopped').toLowerCase();

  if (rawStatus === 'running') {
    return {
      state: 'RUNNING',
      label: 'AI: RUNNING',
      colorClass: 'ai-state-running',
      reason: null
    };
  }

  if (rawStatus === 'starting') {
    return {
      state: 'STARTING',
      label: 'AI: STARTING',
      colorClass: 'ai-state-starting',
      reason: 'Initializing inference pipeline'
    };
  }

  if (rawStatus === 'error') {
    const rawMsg = pipelineStatus?.error_message || 'Pipeline startup or ingestion error';
    return {
      state: 'ERROR',
      label: 'AI: ERROR',
      colorClass: 'ai-state-error',
      reason: formatHumanReadableError(rawMsg)
    };
  }

  return {
    state: 'STOPPED',
    label: 'AI: STOPPED',
    colorClass: 'ai-state-stopped',
    reason: null
  };
}

/**
 * Convert raw Python or network error strings to operational, human-readable reasons.
 * @param {string} rawError 
 * @returns {string}
 */
export function formatHumanReadableError(rawError) {
  if (!rawError) return 'Unknown pipeline error';
  const str = String(rawError);

  if (str.includes('401') || str.includes('Unauthorized') || str.includes('AUTHENTICATION_REQUIRED')) {
    return 'RTSP authentication failure — credentials rejected or missing on gateway.';
  }
  if (str.includes('404') || str.includes('UPSTREAM_NOT_FOUND') || str.includes('Not Found')) {
    return 'Upstream RTSP stream route not found on MediaMTX gateway.';
  }
  if (str.includes('Connection refused') || str.includes('NETWORK_ERROR') || str.includes('unreachable')) {
    return 'Stream gateway unreachable or network connection refused.';
  }
  if (str.includes('Failed to start stream session')) {
    return 'Test camera has no active stream (unmapped test node).';
  }
  if (str.includes('StreamReader') || str.includes('EOF')) {
    return 'Stream connection terminated unexpectedly by upstream source.';
  }

  // Strip Python exception class prefixes like "RuntimeError: "
  return str.replace(/^[A-Za-z_]+Error:\s*/, '').replace(/:\/\/[^@]+@/, '://***:***@');
}
