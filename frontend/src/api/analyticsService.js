import { fetchClient } from './client';

export const analyticsService = {
  getRecentEvents: async (limit = 20) => {
    return fetchClient(`/events?limit=${limit}`);
  },

  getVehicleTimeline: async (plateNumber) => {
    return fetchClient(`/vehicles/${encodeURIComponent(plateNumber)}/timeline`);
  },

  getVehicles: async (limit = 200) => {
    return fetchClient(`/vehicles?limit=${limit}`);
  },

  searchVehicles: async (plateQuery) => {
    return fetchClient(`/vehicles/search?plate=${encodeURIComponent(plateQuery)}`);
  }
};
