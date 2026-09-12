const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

export async function fetchClient(endpoint, options = {}) {
  const url = `${BASE_URL}${endpoint}`;
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  try {
    const response = await fetch(url, { ...options, headers });
    
    // Some endpoints might return 204 No Content
    if (response.status === 204) {
      return null;
    }

    const data = await response.json();

    if (!response.ok) {
      const errorMessage = data?.detail || data?.message || `HTTP error ${response.status}`;
      throw new Error(errorMessage);
    }

    return data;
  } catch (error) {
    if (error instanceof TypeError && error.message === 'Failed to fetch') {
      throw new Error('Network failure: Unable to connect to the backend API.');
    }
    throw error;
  }
}
