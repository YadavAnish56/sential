import { fetchClient } from './client';

export const cameraService = {
  async getCameras() {
    return fetchClient('/cameras');
  },

  async getCamerasMap(onlyMapped = false) {
    const url = onlyMapped ? '/cameras/map?only_mapped=true' : '/cameras/map';
    return fetchClient(url);
  },

  async getPipelinesStatus() {
    return fetchClient('/cameras/pipeline-status');
  },

  async getCameraPipelineStatus(id) {
    return fetchClient(`/cameras/${id}/pipeline-status`);
  },

  async startPipeline(id) {
    return fetchClient(`/cameras/${id}/start`, {
      method: 'POST',
    });
  },

  async stopPipeline(id) {
    return fetchClient(`/cameras/${id}/stop`, {
      method: 'POST',
    });
  },

  async createCamera(payload) {
    return fetchClient('/cameras', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /**
   * Remove a camera. Refused with 409 when sightings reference it, unless
   * `force` is set — in which case the camera is retired and its recorded
   * sightings are kept.
   * @param {number|string} id
   * @param {{ force?: boolean }} [options]
   */
  async deleteCamera(id, { force = false } = {}) {
    const suffix = force ? '?force=true' : '';
    return fetchClient(`/cameras/${id}${suffix}`, {
      method: 'DELETE',
    });
  },
  /**
   * Fetch safe preview URLs from authoritative catalog
   * @param {number} id
   */
  getPreviewUrl: (id) => {
    return fetchClient(`/cameras/${id}/preview`);
  },

  /**
   * Trigger server-side catalogue synchronization
   */
  async syncCatalogue() {
    return fetchClient('/cameras/sync-catalogue', {
      method: 'POST',
    });
  },

  /**
   * Get direct secure Sentinel backend WHEP proxy endpoint.
   * Uses the same base URL as fetchClient (/api).
   * @param {number} id
   */
  getWhepProxyUrl: (id) => {
    const base = import.meta.env.VITE_API_BASE_URL || '/api';
    return `${base}/cameras/${id}/whep`;
  }
};

