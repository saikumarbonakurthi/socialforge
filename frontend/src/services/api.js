const BASE_URL = import.meta.env.VITE_API_URL || '';

const getSecret = () => localStorage.getItem('sf_api_secret') || '';

const request = async (path, options = {}) => {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'x-api-secret': getSecret(),
      ...options.headers,
    },
  });

  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `Request failed: ${res.status}`);
  return data;
};

export const api = {
  // Health
  health: () => request('/api/queue/health'),

  // Queue
  getQueue: (status, limit = 50) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (limit) params.set('limit', String(limit));
    return request(`/api/queue?${params}`);
  },

  addToQueue: (job) =>
    request('/api/queue', { method: 'POST', body: JSON.stringify(job) }),

  deletePost: (id) =>
    request(`/api/queue/${id}`, { method: 'DELETE' }),

  postNow: (id) =>
    request(`/api/queue/${id}/post-now`, { method: 'POST' }),

  testPost: (platform, content, hashtags = []) =>
    request('/api/queue/test-post', {
      method: 'POST',
      body: JSON.stringify({ platform, content, hashtags }),
    }),

  getStats: () => request('/api/queue/stats'),
};

export const setApiSecret = (secret) => localStorage.setItem('sf_api_secret', secret);
export const getApiSecret = () => localStorage.getItem('sf_api_secret') || '';
