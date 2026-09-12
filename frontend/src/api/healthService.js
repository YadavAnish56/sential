import { fetchClient } from './client';

export const healthService = {
  async getSystemHealth() {
    return fetchClient('/health/system');
  }
};
