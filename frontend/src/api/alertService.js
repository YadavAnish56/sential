import { fetchClient } from './client';

export const alertService = {
  getAlerts: async (limit = 50, skip = 0) => {
    return fetchClient(`/alerts?limit=${limit}&skip=${skip}`);
  },

  acknowledgeAlert: async (alertId) => {
    return fetchClient(`/alerts/${alertId}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'acknowledged' }),
    });
  }
};
