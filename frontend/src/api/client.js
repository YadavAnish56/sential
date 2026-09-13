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

    const contentType = response.headers?.get ? (response.headers.get('content-type') || '') : '';

    if (!response.ok) {
      let errorMessage = `HTTP error ${response.status}${response.statusText ? ` (${response.statusText})` : ''}`;
      let errorDetail = null;

      if (contentType.includes('application/json')) {
        try {
          const data = await response.json();
          const detail = data?.detail ?? data?.message;
          // A detail may be a plain string or a structured object (e.g. the
          // camera-delete refusal, which carries its counts). Keep the object
          // on the error so callers can act on it, and show its message.
          if (detail && typeof detail === 'object') {
            errorDetail = detail;
            errorMessage = detail.message || detail.detail || errorMessage;
          } else if (typeof detail === 'string' && detail) {
            errorMessage = detail;
          }
        } catch (_) {
          // Fallback to generic status message if error JSON body parsing fails
        }
      } else if (response.status === 502) {
        errorMessage = 'HTTP error 502: Bad Gateway (Backend service is starting or unavailable)';
      } else if (response.status === 503) {
        errorMessage = 'HTTP error 503: Service Unavailable';
      } else if (response.status === 504) {
        errorMessage = 'HTTP error 504: Gateway Timeout';
      }

      const error = new Error(errorMessage);
      error.status = response.status;
      error.detail = errorDetail;
      throw error;
    }

    // Response is OK — ensure it is valid JSON
    if (!contentType || contentType.includes('application/json')) {
      try {
        return await response.json();
      } catch (_) {
        throw new Error('Invalid API response: Server returned malformed JSON');
      }
    }

    // Response is OK but non-JSON content (e.g. accidental HTML fallback)
    throw new Error(`Invalid API response: Expected JSON but received ${contentType || 'non-JSON content'}`);
  } catch (error) {
    if (error instanceof TypeError && error.message === 'Failed to fetch') {
      throw new Error('Network failure: Unable to connect to the backend API.');
    }
    throw error;
  }
}

