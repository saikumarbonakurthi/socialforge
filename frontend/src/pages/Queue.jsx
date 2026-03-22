import { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import {
  Twitter, Linkedin, Facebook, Instagram, MessageCircle,
  Send, Trash2, PlayCircle, Clock, CheckCircle, AlertTriangle,
  Filter, Loader2,
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

const STATUS_CONFIG = {
  pending: { icon: Clock, color: 'bg-amber-50 text-amber-700 border-amber-200', label: 'Pending' },
  completed: { icon: CheckCircle, color: 'bg-green-50 text-green-700 border-green-200', label: 'Completed' },
  partial: { icon: AlertTriangle, color: 'bg-orange-50 text-orange-700 border-orange-200', label: 'Partial' },
  failed: { icon: AlertTriangle, color: 'bg-red-50 text-red-700 border-red-200', label: 'Failed' },
  posted: { icon: CheckCircle, color: 'bg-green-50 text-green-700 border-green-200', label: 'Posted' },
};

export default function Queue() {
  const { queue, fetchQueue, removePost, postNow, loading } = useApp();
  const [filter, setFilter] = useState('all');
  const [expandedId, setExpandedId] = useState(null);
  const [actionLoading, setActionLoading] = useState(null);

  useEffect(() => {
    fetchQueue(filter === 'all' ? null : filter);
  }, [fetchQueue, filter]);

  const handleDelete = async (id) => {
    if (!confirm('Delete this post from the queue?')) return;
    setActionLoading(id);
    try {
      await removePost(id);
    } finally {
      setActionLoading(null);
    }
  };

  const handlePostNow = async (id) => {
    if (!confirm('Post this immediately to all platforms?')) return;
    setActionLoading(id);
    try {
      await postNow(id);
    } finally {
      setActionLoading(null);
    }
  };

  const filters = [
    { key: 'all', label: 'All' },
    { key: 'pending', label: 'Pending' },
    { key: 'completed', label: 'Completed' },
    { key: 'partial', label: 'Partial' },
    { key: 'failed', label: 'Failed' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Post Queue</h1>
        <p className="text-gray-500 mt-1">Manage and monitor your scheduled posts</p>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter size={16} className="text-gray-400" />
        {filters.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={clsx(
              'px-3.5 py-1.5 rounded-full text-xs font-medium border transition',
              filter === key
                ? 'border-primary-500 bg-primary-50 text-primary-700'
                : 'border-gray-200 text-gray-500 hover:border-gray-300',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Queue list */}
      <div className="space-y-3">
        {loading && queue.length === 0 ? (
          <div className="rounded-xl border border-gray-200 bg-white p-12 text-center">
            <Loader2 size={24} className="animate-spin text-gray-400 mx-auto" />
            <p className="mt-3 text-gray-400">Loading queue...</p>
          </div>
        ) : queue.length === 0 ? (
          <div className="rounded-xl border border-gray-200 bg-white p-12 text-center">
            <Send size={32} className="text-gray-300 mx-auto" />
            <p className="mt-3 text-gray-400">No posts in queue</p>
          </div>
        ) : (
          queue.map((post) => {
            const status = STATUS_CONFIG[post.status] || STATUS_CONFIG.pending;
            const StatusIcon = status.icon;
            const isExpanded = expandedId === post.id;

            return (
              <div
                key={post.id}
                className="rounded-xl border border-gray-200 bg-white overflow-hidden transition hover:shadow-sm"
              >
                {/* Main row */}
                <div
                  className="flex items-center gap-4 p-4 cursor-pointer"
                  onClick={() => setExpandedId(isExpanded ? null : post.id)}
                >
                  {/* Platform icons */}
                  <div className="flex -space-x-1 shrink-0">
                    {(post.platforms || []).map((pp) => {
                      const Icon = PLATFORM_ICONS[pp.platform] || Send;
                      return (
                        <div
                          key={pp.platform}
                          className={clsx(
                            'flex h-8 w-8 items-center justify-center rounded-full bg-white border border-gray-200',
                            PLATFORM_COLORS[pp.platform],
                          )}
                        >
                          <Icon size={14} />
                        </div>
                      );
                    })}
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-gray-900 truncate">{post.topic}</p>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs text-gray-400 flex items-center gap-1">
                        <Clock size={12} />
                        {post.scheduled_for
                          ? format(new Date(post.scheduled_for), 'MMM d, yyyy h:mm a')
                          : '—'}
                      </span>
                      <span className="text-xs text-gray-300">|</span>
                      <span className="text-xs text-gray-400 capitalize">{post.tone}</span>
                    </div>
                  </div>

                  {/* Status badge */}
                  <span className={clsx('text-xs font-medium px-2.5 py-1 rounded-full border flex items-center gap-1', status.color)}>
                    <StatusIcon size={12} />
                    {status.label}
                  </span>

                  {/* Actions */}
                  <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                    {post.status === 'pending' && (
                      <button
                        onClick={() => handlePostNow(post.id)}
                        disabled={actionLoading === post.id}
                        className="p-2 rounded-lg text-primary-600 hover:bg-primary-50 transition disabled:opacity-50"
                        title="Post now"
                      >
                        {actionLoading === post.id ? (
                          <Loader2 size={16} className="animate-spin" />
                        ) : (
                          <PlayCircle size={16} />
                        )}
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(post.id)}
                      disabled={actionLoading === post.id}
                      className="p-2 rounded-lg text-red-400 hover:bg-red-50 hover:text-red-600 transition disabled:opacity-50"
                      title="Delete"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                  <div className="border-t border-gray-100 bg-gray-50/50 p-4 space-y-3">
                    {(post.platforms || []).map((pp) => {
                      const Icon = PLATFORM_ICONS[pp.platform] || Send;
                      const ppStatus = STATUS_CONFIG[pp.status] || STATUS_CONFIG.pending;
                      return (
                        <div key={pp.platform} className="rounded-lg bg-white border border-gray-200 p-4">
                          <div className="flex items-center gap-2 mb-2">
                            <Icon size={16} className={PLATFORM_COLORS[pp.platform]} />
                            <span className="text-sm font-semibold text-gray-700 capitalize">{pp.platform}</span>
                            <span className={clsx('ml-auto text-xs font-medium px-2 py-0.5 rounded-full border', ppStatus.color)}>
                              {ppStatus.label}
                            </span>
                          </div>
                          <p className="text-sm text-gray-600 whitespace-pre-wrap">{pp.content}</p>
                          {pp.hashtags && pp.hashtags.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-2">
                              {pp.hashtags.map((tag, i) => (
                                <span key={i} className="text-xs text-primary-600 bg-primary-50 px-2 py-0.5 rounded-full">
                                  #{tag}
                                </span>
                              ))}
                            </div>
                          )}
                          {pp.result && pp.result.error && (
                            <p className="mt-2 text-xs text-red-500">Error: {pp.result.error}</p>
                          )}
                          {pp.result && pp.result.url && (
                            <a
                              href={pp.result.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-2 inline-block text-xs text-primary-600 hover:underline"
                            >
                              View post &rarr;
                            </a>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
