import { fetchClient } from './client';

export const cameraService = {
  async getCameras() {
    return fetchClient('/cameras');
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
  /**
   * Fetch safe preview URLs from authoritative catalog
   * @param {number} id
   */
  getPreviewUrl: (id) => {
    return fetchClient(`/cameras/${id}/preview`);
  }
};
