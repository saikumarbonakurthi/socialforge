import { createContext, useContext, useState, useCallback } from 'react';
import { api } from '../services/api';

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [queue, setQueue] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchQueue = useCallback(async (status = null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getQueue(status);
      setQueue(res.data || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const res = await api.getStats();
      setStats(res.data || null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  const addPost = useCallback(async (job) => {
    const res = await api.addToQueue(job);
    await fetchQueue();
    return res;
  }, [fetchQueue]);

  const removePost = useCallback(async (id) => {
    await api.deletePost(id);
    await fetchQueue();
  }, [fetchQueue]);

  const postNow = useCallback(async (id) => {
    const res = await api.postNow(id);
    await fetchQueue();
    return res;
  }, [fetchQueue]);

  return (
    <AppContext.Provider
      value={{
        queue, stats, loading, error,
        fetchQueue, fetchStats, addPost, removePost, postNow,
        setError,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export const useApp = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be inside AppProvider');
  return ctx;
};
