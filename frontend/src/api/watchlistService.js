import { fetchClient } from './client';

export const watchlistService = {
  /**
   * Fetch watchlist entries with optional filtering and search.
   * @param {Object} params - Query parameters (skip, limit, is_active, severity, search)
   */
  async getWatchlist(params = {}) {
    const query = new URLSearchParams();
    if (params.skip !== undefined) query.append('skip', params.skip);
    if (params.limit !== undefined) query.append('limit', params.limit);
    if (params.is_active !== undefined && params.is_active !== null && params.is_active !== 'all') {
      query.append('is_active', params.is_active);
    }
    if (params.severity && params.severity !== 'all') {
      query.append('severity', params.severity);
    }
    if (params.search && params.search.trim()) {
      query.append('search', params.search.trim());
    }

    const qs = query.toString();
    return fetchClient(`/watchlist${qs ? `?${qs}` : ''}`);
  },

  /**
   * Get a single watchlist entry by ID.
   * @param {number} id
   */
  async getWatchlistEntry(id) {
    return fetchClient(`/watchlist/${id}`);
  },

  /**
   * Create a new watchlist entry.
   * @param {Object} payload - { plate_number, description, severity, is_active }
   */
  async createWatchlistEntry(payload) {
    return fetchClient('/watchlist', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  /**
   * Update an existing watchlist entry.
   * @param {number} id
   * @param {Object} payload - { plate_number, description, severity, is_active }
   */
  async updateWatchlistEntry(id, payload) {
    return fetchClient(`/watchlist/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  },

  /**
   * Delete or deactivate a watchlist entry.
   * @param {number} id
   * @param {boolean} deactivateOnly
   */
  async deleteWatchlistEntry(id, deactivateOnly = false) {
    const endpoint = `/watchlist/${id}${deactivateOnly ? '?deactivate_only=true' : ''}`;
    return fetchClient(endpoint, {
      method: 'DELETE',
    });
  },
};
