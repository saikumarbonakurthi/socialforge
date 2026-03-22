import { useState, useEffect } from 'react';
import {
  Key, Server, Twitter, Linkedin, Facebook, Instagram,
  MessageCircle, CheckCircle, XCircle, Save, Eye, EyeOff,
  ExternalLink,
} from 'lucide-react';
import { setApiSecret, getApiSecret } from '../services/api';
import { api } from '../services/api';
import clsx from 'clsx';

const PLATFORM_SETTINGS = [
  {
    key: 'twitter',
    label: 'Twitter / X',
    icon: Twitter,
    color: 'text-sky-500',
    envVars: ['TWITTER_API_KEY', 'TWITTER_API_SECRET', 'TWITTER_ACCESS_TOKEN', 'TWITTER_ACCESS_TOKEN_SECRET'],
    docsUrl: 'https://developer.twitter.com/en/portal/dashboard',
  },
  {
    key: 'linkedin',
    label: 'LinkedIn',
    icon: Linkedin,
    color: 'text-blue-700',
    envVars: ['LINKEDIN_CLIENT_ID', 'LINKEDIN_CLIENT_SECRET', 'LINKEDIN_ACCESS_TOKEN', 'LINKEDIN_PERSON_URN'],
    docsUrl: 'https://www.linkedin.com/developers/apps',
  },
  {
    key: 'facebook',
    label: 'Facebook',
    icon: Facebook,
    color: 'text-blue-600',
    envVars: ['FACEBOOK_PAGE_ACCESS_TOKEN', 'FACEBOOK_PAGE_ID'],
    docsUrl: 'https://developers.facebook.com/apps',
  },
  {
    key: 'instagram',
    label: 'Instagram',
    icon: Instagram,
    color: 'text-pink-500',
    envVars: ['INSTAGRAM_ACCESS_TOKEN', 'INSTAGRAM_ACCOUNT_ID'],
    docsUrl: 'https://developers.facebook.com/docs/instagram-api',
  },
  {
    key: 'whatsapp',
    label: 'WhatsApp',
    icon: MessageCircle,
    color: 'text-green-500',
    envVars: ['WHATSAPP_PHONE_NUMBER_ID', 'WHATSAPP_ACCESS_TOKEN', 'WHATSAPP_RECIPIENT_PHONE'],
    docsUrl: 'https://developers.facebook.com/docs/whatsapp',
  },
];

export default function SettingsPage() {
  const [secret, setSecret] = useState(getApiSecret());
  const [showSecret, setShowSecret] = useState(false);
  const [backendUrl, setBackendUrl] = useState(import.meta.env.VITE_API_URL || window.location.origin);
  const [saved, setSaved] = useState(false);
  const [healthOk, setHealthOk] = useState(null);

  useEffect(() => {
    checkHealth();
  }, []);

  const checkHealth = async () => {
    try {
      await api.health();
      setHealthOk(true);
    } catch {
      setHealthOk(false);
    }
  };

  const handleSave = () => {
    setApiSecret(secret);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    checkHealth();
  };

  return (
    <div className="max-w-3xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Configure your backend connection and platform credentials</p>
      </div>

      {/* Backend Connection */}
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50 flex items-center gap-2">
          <Server size={18} className="text-gray-500" />
          <h2 className="font-semibold text-gray-800">Backend Connection</h2>
          <div className="ml-auto flex items-center gap-2">
            {healthOk === true && (
              <span className="flex items-center gap-1 text-xs text-green-600">
                <CheckCircle size={14} /> Connected
              </span>
            )}
            {healthOk === false && (
              <span className="flex items-center gap-1 text-xs text-red-500">
                <XCircle size={14} /> Disconnected
              </span>
            )}
          </div>
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Backend URL</label>
            <input
              type="text"
              value={backendUrl}
              onChange={(e) => setBackendUrl(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-4 py-2.5 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none"
              placeholder="http://localhost:3001"
            />
            <p className="text-xs text-gray-400 mt-1">
              Set via VITE_API_URL env variable, or defaults to same origin with proxy
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">API Secret</label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-4 py-2.5 pr-20 text-sm focus:border-primary-500 focus:ring-2 focus:ring-primary-200 outline-none font-mono"
                placeholder="Enter your API secret"
              />
              <button
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded text-gray-400 hover:text-gray-600"
              >
                {showSecret ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <p className="text-xs text-gray-400 mt-1">
              This is stored locally in your browser. Must match the API_SECRET in your backend .env file.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleSave}
              className="inline-flex items-center gap-2 rounded-lg bg-primary-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-primary-700 transition"
            >
              <Save size={16} />
              {saved ? 'Saved!' : 'Save Settings'}
            </button>
            <button
              onClick={checkHealth}
              className="rounded-lg border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"
            >
              Test Connection
            </button>
          </div>
        </div>
      </div>

      {/* Platform Credentials */}
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50 flex items-center gap-2">
          <Key size={18} className="text-gray-500" />
          <h2 className="font-semibold text-gray-800">Platform Credentials</h2>
        </div>
        <div className="p-6 space-y-1">
          <p className="text-sm text-gray-500 mb-4">
            Platform API credentials are configured in the backend <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">.env</code> file for security. Below is a reference for each platform.
          </p>

          <div className="space-y-3">
            {PLATFORM_SETTINGS.map(({ key, label, icon: Icon, color, envVars, docsUrl }) => (
              <div
                key={key}
                className="flex items-start gap-4 rounded-lg border border-gray-100 p-4 hover:bg-gray-50 transition"
              >
                <div className={clsx('flex h-10 w-10 items-center justify-center rounded-lg bg-gray-50 shrink-0', color)}>
                  <Icon size={20} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-gray-800">{label}</h3>
                    <a
                      href={docsUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary-500 hover:text-primary-600"
                    >
                      <ExternalLink size={12} />
                    </a>
                  </div>
                  <div className="flex flex-wrap gap-1 mt-1.5">
                    {envVars.map((v) => (
                      <code key={v} className="bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded text-xs">
                        {v}
                      </code>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Instructions */}
      <div className="rounded-xl border border-gray-200 bg-white p-6">
        <h2 className="font-semibold text-gray-800 mb-3">Quick Setup Guide</h2>
        <ol className="space-y-2 text-sm text-gray-600 list-decimal list-inside">
          <li>Copy <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">.env.example</code> to <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">.env</code> in the backend</li>
          <li>Fill in your platform API credentials in the <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">.env</code> file</li>
          <li>Set your <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">API_SECRET</code> to a random string</li>
          <li>Enter the same API secret above and click "Save Settings"</li>
          <li>Start the backend: <code className="bg-gray-100 px-1.5 py-0.5 rounded text-xs">npm start</code></li>
          <li>Click "Test Connection" to verify everything works</li>
        </ol>
      </div>
    </div>
  );
}
