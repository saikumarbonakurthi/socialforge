import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../context/AppContext';
import {
  Twitter, Linkedin, Facebook, Instagram, MessageCircle,
  Sparkles, Send, Clock, Image, Hash, Loader2,
} from 'lucide-react';
import clsx from 'clsx';
import { format } from 'date-fns';

const PLATFORMS = [
  { key: 'twitter', label: 'Twitter / X', icon: Twitter, color: 'text-sky-500 border-sky-200 bg-sky-50', maxLen: 280 },
  { key: 'linkedin', label: 'LinkedIn', icon: Linkedin, color: 'text-blue-700 border-blue-200 bg-blue-50', maxLen: 3000 },
  { key: 'facebook', label: 'Facebook', icon: Facebook, color: 'text-blue-600 border-blue-200 bg-blue-50', maxLen: 5000 },
  { key: 'instagram', label: 'Instagram', icon: Instagram, color: 'text-pink-500 border-pink-200 bg-pink-50', maxLen: 2200 },
  { key: 'whatsapp', label: 'WhatsApp', icon: MessageCircle, color: 'text-green-600 border-green-200 bg-green-50', maxLen: 4096 },
];

const TONES = ['Professional', 'Casual', 'Enthusiastic', 'Informative', 'Humorous', 'Inspirational'];

export default function CreatePost() {
  const navigate = useNavigate();
  const { addPost } = useApp();

  const [topic, setTopic] = useState('');
  const [tone, setTone] = useState('Professional');
  const [selectedPlatforms, setSelectedPlatforms] = useState(['linkedin', 'twitter']);
  const [posts, setPosts] = useState({});
  const [hashtags, setHashtags] = useState({});
  const [hashtagInput, setHashtagInput] = useState({});
  const [imagePrompt, setImagePrompt] = useState('');
  const [scheduleDate, setScheduleDate] = useState(format(new Date(), 'yyyy-MM-dd'));
  const [scheduleTime, setScheduleTime] = useState(format(new Date(), 'HH:mm'));
  const [submitting, setSubmitting] = useState(false);
  const [aiGenerating, setAiGenerating] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState(1); // 1: topic/platforms, 2: content, 3: schedule

  const togglePlatform = (key) => {
    setSelectedPlatforms((prev) =>
      prev.includes(key) ? prev.filter((p) => p !== key) : [...prev, key]
    );
  };

  const generateAIContent = () => {
    if (!topic.trim()) return;
    setAiGenerating(true);
    // Simulated AI content generation
    setTimeout(() => {
      const generated = {};
      const generatedTags = {};
      for (const key of selectedPlatforms) {
        const plat = PLATFORMS.find((p) => p.key === key);
        if (key === 'twitter') {
          generated[key] = `${topic} - Stay ahead of the curve! Check out how this is transforming the industry. ${tone === 'Casual' ? 'Pretty wild, right?' : ''}`;
        } else if (key === 'linkedin') {
          generated[key] = `I'm excited to share insights about ${topic}.\n\nIn today's rapidly evolving landscape, staying informed is key. Here's what you need to know:\n\n1. Innovation drives growth\n2. Adaptability is essential\n3. Community matters\n\nWhat are your thoughts? I'd love to hear your perspective in the comments below.`;
        } else if (key === 'instagram') {
          generated[key] = `${topic} is changing the game! Here's everything you need to know about staying ahead.\n\nSwipe to learn more about the latest trends and insights that are shaping the future.`;
        } else if (key === 'facebook') {
          generated[key] = `Excited to share our latest thoughts on ${topic}!\n\nThis is a topic that's been getting a lot of attention lately, and for good reason. Here's our take on what it means for the industry and what you can do to stay ahead.\n\nRead on and let us know what you think!`;
        } else if (key === 'whatsapp') {
          generated[key] = `Hi! Quick update on ${topic}. Check out our latest post for all the details. Let me know if you have any questions!`;
        }
        generatedTags[key] = ['Innovation', 'Trending', plat?.label?.replace(/\s.*/, '') || key];
      }
      setPosts(generated);
      setHashtags(generatedTags);
      setAiGenerating(false);
      setStep(2);
    }, 1500);
  };

  const addHashtag = (platform) => {
    const tag = (hashtagInput[platform] || '').trim().replace(/^#/, '');
    if (!tag) return;
    setHashtags((prev) => ({
      ...prev,
      [platform]: [...(prev[platform] || []), tag],
    }));
    setHashtagInput((prev) => ({ ...prev, [platform]: '' }));
  };

  const removeHashtag = (platform, idx) => {
    setHashtags((prev) => ({
      ...prev,
      [platform]: (prev[platform] || []).filter((_, i) => i !== idx),
    }));
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const filteredPosts = {};
      for (const key of selectedPlatforms) {
        if (posts[key]?.trim()) filteredPosts[key] = posts[key];
      }
      if (Object.keys(filteredPosts).length === 0) {
        setError('Write content for at least one platform.');
        setSubmitting(false);
        return;
      }

      await addPost({
        topic,
        tone,
        scheduledFor: `${scheduleDate}T${scheduleTime}:00`,
        posts: filteredPosts,
        hashtags,
        imagePrompt: imagePrompt || undefined,
      });
      navigate('/queue');
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Create Post</h1>
        <p className="text-gray-500 mt-1">Craft and schedule content across platforms</p>
      </div>

      {/* Progress steps */}
      <div className="flex items-center gap-2">
        {[
          { n: 1, label: 'Topic & Platforms' },
          { n: 2, label: 'Content' },
          { n: 3, label: 'Schedule' },
        ].map(({ n, label }) => (
          <button
            key={n}
            onClick={() => n < step + 1 && setStep(n)}
            className={clsx(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition',
              step === n
                ? 'bg-primary-600 text-white'
                : step > n
                  ? 'bg-primary-50 text-primary-700 hover:bg-primary-100'
                  : 'bg-gray-100 text-gray-400',
            )}
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/20 text-xs">{n}</span>
            <span className="hidden sm:inline">{label}</span>
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-3 text-sm text-red-700">{error}</div>
      )}

      {/* Step 1: Topic & Platforms */}
      {step === 1 && (
        <div className="space-y-6">
          <div className="rounded-xl border border-gray-200 bg-white p-6 space-y-5">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Topic</label>
              <input
                type="text"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="e.g., How AI is transforming social media marketing"
                className="w-full rounded-lg border border-gray-300 px-4 py-3 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none transition"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Tone</label>
              <div className="flex flex-wrap gap-2">
                {TONES.map((t) => (
                  <button
                    key={t}
                    onClick={() => setTone(t)}
                    className={clsx(
                      'px-4 py-2 rounded-lg text-sm font-medium border transition',
                      tone === t
                        ? 'border-primary-500 bg-primary-50 text-primary-700'
                        : 'border-gray-200 text-gray-600 hover:border-gray-300',
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">Select Platforms</label>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                {PLATFORMS.map(({ key, label, icon: Icon, color }) => (
                  <button
                    key={key}
                    onClick={() => togglePlatform(key)}
                    className={clsx(
                      'flex flex-col items-center gap-2 rounded-xl border-2 p-4 transition',
                      selectedPlatforms.includes(key)
                        ? clsx(color, 'border-current')
                        : 'border-gray-200 text-gray-400 hover:border-gray-300',
                    )}
                  >
                    <Icon size={24} />
                    <span className="text-xs font-medium">{label}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="flex justify-end gap-3">
            <button
              onClick={generateAIContent}
              disabled={!topic.trim() || selectedPlatforms.length === 0 || aiGenerating}
              className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-accent-600 to-primary-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm hover:opacity-90 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {aiGenerating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
              {aiGenerating ? 'Generating...' : 'Generate with AI'}
            </button>
            <button
              onClick={() => setStep(2)}
              disabled={!topic.trim() || selectedPlatforms.length === 0}
              className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-primary-700 transition disabled:opacity-50"
            >
              Write Manually
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Content */}
      {step === 2 && (
        <div className="space-y-5">
          {selectedPlatforms.map((key) => {
            const plat = PLATFORMS.find((p) => p.key === key);
            const Icon = plat?.icon || Send;
            const charCount = (posts[key] || '').length;
            return (
              <div key={key} className="rounded-xl border border-gray-200 bg-white overflow-hidden">
                <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-100 bg-gray-50">
                  <Icon size={18} className={plat?.color?.split(' ')[0]} />
                  <span className="text-sm font-semibold text-gray-700">{plat?.label}</span>
                  <span className={clsx(
                    'ml-auto text-xs',
                    charCount > (plat?.maxLen || 5000) ? 'text-red-500 font-bold' : 'text-gray-400',
                  )}>
                    {charCount}/{plat?.maxLen}
                  </span>
                </div>
                <div className="p-5 space-y-3">
                  <textarea
                    rows={key === 'twitter' ? 3 : 5}
                    value={posts[key] || ''}
                    onChange={(e) => setPosts((p) => ({ ...p, [key]: e.target.value }))}
                    placeholder={`Write your ${plat?.label} post...`}
                    className="w-full rounded-lg border border-gray-200 px-4 py-3 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none transition resize-none"
                  />

                  {/* Hashtags */}
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <Hash size={14} className="text-gray-400" />
                      <span className="text-xs text-gray-500 font-medium">Hashtags</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5 mb-2">
                      {(hashtags[key] || []).map((tag, idx) => (
                        <span
                          key={idx}
                          className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2.5 py-1 text-xs text-primary-700"
                        >
                          #{tag}
                          <button
                            onClick={() => removeHashtag(key, idx)}
                            className="text-primary-400 hover:text-red-500"
                          >
                            &times;
                          </button>
                        </span>
                      ))}
                    </div>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={hashtagInput[key] || ''}
                        onChange={(e) => setHashtagInput((p) => ({ ...p, [key]: e.target.value }))}
                        onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addHashtag(key))}
                        placeholder="Add hashtag"
                        className="flex-1 rounded-lg border border-gray-200 px-3 py-1.5 text-xs focus:border-primary-500 outline-none"
                      />
                      <button
                        onClick={() => addHashtag(key)}
                        className="rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-200"
                      >
                        Add
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}

          {/* Image prompt */}
          <div className="rounded-xl border border-gray-200 bg-white p-5 space-y-2">
            <div className="flex items-center gap-2">
              <Image size={16} className="text-gray-400" />
              <label className="text-sm font-medium text-gray-700">Image Prompt (optional)</label>
            </div>
            <input
              type="text"
              value={imagePrompt}
              onChange={(e) => setImagePrompt(e.target.value)}
              placeholder="Describe the image you want AI to generate..."
              className="w-full rounded-lg border border-gray-200 px-4 py-2.5 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none"
            />
          </div>

          <div className="flex justify-between">
            <button
              onClick={() => setStep(1)}
              className="rounded-lg border border-gray-200 px-5 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"
            >
              Back
            </button>
            <button
              onClick={() => setStep(3)}
              className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 transition"
            >
              <Clock size={16} />
              Schedule
            </button>
          </div>
        </div>
      )}

      {/* Step 3: Schedule */}
      {step === 3 && (
        <div className="space-y-6">
          <div className="rounded-xl border border-gray-200 bg-white p-6 space-y-5">
            <h3 className="text-lg font-semibold text-gray-900">Schedule Your Post</h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Date</label>
                <input
                  type="date"
                  value={scheduleDate}
                  onChange={(e) => setScheduleDate(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-4 py-3 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Time</label>
                <input
                  type="time"
                  value={scheduleTime}
                  onChange={(e) => setScheduleTime(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-4 py-3 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none"
                />
              </div>
            </div>

            {/* Preview */}
            <div className="rounded-lg bg-gray-50 p-4 space-y-3">
              <h4 className="text-sm font-semibold text-gray-700">Post Summary</h4>
              <div className="space-y-2 text-sm text-gray-600">
                <p><span className="font-medium">Topic:</span> {topic}</p>
                <p><span className="font-medium">Tone:</span> {tone}</p>
                <p><span className="font-medium">Platforms:</span> {selectedPlatforms.join(', ')}</p>
                <p>
                  <span className="font-medium">Scheduled:</span>{' '}
                  {scheduleDate && scheduleTime
                    ? format(new Date(`${scheduleDate}T${scheduleTime}`), 'MMMM d, yyyy h:mm a')
                    : '—'}
                </p>
              </div>
            </div>
          </div>

          <div className="flex justify-between">
            <button
              onClick={() => setStep(2)}
              className="rounded-lg border border-gray-200 px-5 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"
            >
              Back
            </button>
            <button
              onClick={handleSubmit}
              disabled={submitting}
              className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-primary-600 to-accent-600 px-6 py-2.5 text-sm font-medium text-white shadow-sm hover:opacity-90 transition disabled:opacity-50"
            >
              {submitting ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
              {submitting ? 'Scheduling...' : 'Schedule Post'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
