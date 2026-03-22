import { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { api } from '../services/api';
import { Link } from 'react-router-dom';
import {
  Clock, CheckCircle, AlertTriangle, Send, TrendingUp,
  Twitter, Linkedin, Facebook, Instagram, MessageCircle,
  PlusCircle, ArrowRight,
} from 'lucide-react';
import { format } from 'date-fns';
import clsx from 'clsx';

const PLATFORM_ICONS = {
  twitter: Twitter,
  linkedin: Linkedin,
  facebook: Facebook,
  instagram: Instagram,
  whatsapp: MessageCircle,
};

const PLATFORM_COLORS = {
  twitter: 'text-sky-500',
  linkedin: 'text-blue-700',
  facebook: 'text-blue-600',
  instagram: 'text-pink-500',
  whatsapp: 'text-green-500',
};

const StatCard = ({ icon: Icon, label, value, color, bg }) => (
  <div className={clsx('rounded-xl border p-5', bg, 'border-gray-200 bg-white')}>
    <div className="flex items-center justify-between">
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="mt-1 text-3xl font-bold text-gray-900">{value}</p>
      </div>
      <div className={clsx('flex h-12 w-12 items-center justify-center rounded-xl', color)}>
        <Icon size={24} />
      </div>
    </div>
  </div>
);

export default function Dashboard() {
  const { stats, queue, fetchStats, fetchQueue, loading } = useApp();
  const [connected, setConnected] = useState(null);

  useEffect(() => {
    fetchStats();
    fetchQueue();
    api.health().then(() => setConnected(true)).catch(() => setConnected(false));
  }, [fetchStats, fetchQueue]);

  const recentPosts = (queue || []).slice(0, 5);
  const pendingCount = stats?.pending || 0;
  const completedCount = stats?.completed || 0;
  const partialCount = stats?.partial || 0;
  const totalCount = stats?.total || 0;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-gray-500 mt-1">Overview of your social media activity</p>
        </div>
        <Link
          to="/create"
          className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-primary-600 to-accent-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm hover:opacity-90 transition"
        >
          <PlusCircle size={16} />
          Create Post
        </Link>
      </div>

      {/* Connection status */}
      {connected === false && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700 flex items-center gap-2">
          <AlertTriangle size={16} />
          Backend not reachable. Check that the server is running and API secret is configured in Settings.
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Send} label="Total Posts" value={totalCount} color="bg-primary-50 text-primary-600" />
        <StatCard icon={Clock} label="Pending" value={pendingCount} color="bg-amber-50 text-amber-600" />
        <StatCard icon={CheckCircle} label="Completed" value={completedCount} color="bg-green-50 text-green-600" />
        <StatCard icon={AlertTriangle} label="Partial / Failed" value={partialCount + (stats?.failed || 0)} color="bg-red-50 text-red-500" />
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent queue */}
        <div className="lg:col-span-2 rounded-xl border border-gray-200 bg-white">
          <div className="flex items-center justify-between p-5 border-b border-gray-100">
            <h2 className="font-semibold text-gray-900">Recent Posts</h2>
            <Link to="/queue" className="text-sm text-primary-600 hover:text-primary-700 flex items-center gap-1">
              View all <ArrowRight size={14} />
            </Link>
          </div>
          <div className="divide-y divide-gray-50">
            {loading && recentPosts.length === 0 ? (
              <div className="p-8 text-center text-gray-400">Loading...</div>
            ) : recentPosts.length === 0 ? (
              <div className="p-8 text-center text-gray-400">
                No posts yet. Create your first post!
              </div>
            ) : (
              recentPosts.map((post) => (
                <div key={post.id} className="flex items-center gap-4 p-4 hover:bg-gray-50 transition">
                  <div className="flex -space-x-1">
                    {(post.platforms || []).map((pp) => {
                      const Icon = PLATFORM_ICONS[pp.platform] || Send;
                      return (
                        <div
                          key={pp.platform}
                          className={clsx(
                            'flex h-7 w-7 items-center justify-center rounded-full bg-white border border-gray-200',
                            PLATFORM_COLORS[pp.platform],
                          )}
                        >
                          <Icon size={13} />
                        </div>
                      );
                    })}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">{post.topic}</p>
                    <p className="text-xs text-gray-400">
                      {post.scheduled_for ? format(new Date(post.scheduled_for), 'MMM d, yyyy h:mm a') : '—'}
                    </p>
                  </div>
                  <span
                    className={clsx(
                      'text-xs font-medium px-2.5 py-1 rounded-full',
                      post.status === 'completed' && 'bg-green-50 text-green-700',
                      post.status === 'pending' && 'bg-amber-50 text-amber-700',
                      post.status === 'partial' && 'bg-orange-50 text-orange-700',
                      post.status === 'failed' && 'bg-red-50 text-red-700',
                    )}
                  >
                    {post.status}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Platforms overview */}
        <div className="rounded-xl border border-gray-200 bg-white">
          <div className="p-5 border-b border-gray-100">
            <h2 className="font-semibold text-gray-900">Platforms</h2>
          </div>
          <div className="p-4 space-y-3">
            {Object.entries(PLATFORM_ICONS).map(([key, Icon]) => {
              const platformPosts = (queue || []).flatMap(
                (q) => (q.platforms || []).filter((pp) => pp.platform === key)
              );
              const posted = platformPosts.filter((pp) => pp.status === 'posted').length;
              return (
                <div key={key} className="flex items-center gap-3 p-2 rounded-lg hover:bg-gray-50">
                  <div className={clsx('flex h-9 w-9 items-center justify-center rounded-lg bg-gray-50', PLATFORM_COLORS[key])}>
                    <Icon size={18} />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-700 capitalize">{key}</p>
                    <p className="text-xs text-gray-400">{posted} posted</p>
                  </div>
                  <TrendingUp size={14} className="text-gray-300" />
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
