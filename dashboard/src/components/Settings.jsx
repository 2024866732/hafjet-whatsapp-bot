/**
 * Settings — Bot configuration page
 * Grouped: General / AI / Routing
 */
import { useState, useEffect } from 'react';

export default function Settings() {
  const [settings, setSettings] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState(null);
  const [form, setForm] = useState({});
  const [errors, setErrors] = useState([]);

  const loadSettings = async () => {
    try {
      const res = await fetch('/api/settings', {
        headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
      });
      const data = await res.json();
      setSettings(data);
      setForm(data);
    } catch (e) {
      setToast({ message: '❌ Gagal muat tetapan', type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleChange = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors([]);
  };

  const handleSave = async () => {
    setSaving(true);
    setErrors([]);
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
        body: JSON.stringify(form),
      });
      const result = await res.json();
      if (!res.ok) throw new Error('Failed to save');
      if (result.errors && result.errors.length > 0) {
        setErrors(result.errors);
        setToast({ message: '⚠️ Ada ralat dalam tetapan', type: 'error' });
      } else {
        setSettings(form);
        setToast({ message: '✅ Tetapan disimpan berjaya', type: 'success' });
      }
    } catch (e) {
      setToast({ message: '❌ Gagal simpan tetapan', type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setForm(settings);
    setErrors([]);
  };

  if (loading) {
    return (
      <div className="flex-1 bg-gray-800 p-8 flex items-center justify-center">
        <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex-1 bg-gray-800 p-6 overflow-y-auto">
      {toast && (
        <div
          className={`fixed top-4 right-4 px-4 py-2 rounded-lg shadow-lg text-sm z-50 animate-fade-in cursor-pointer ${
            toast.type === 'success' ? 'bg-emerald-600 text-white' : toast.type === 'error' ? 'bg-red-600 text-white' : 'bg-blue-600 text-white'
          }`}
          onClick={() => setToast(null)}
        >
          {toast.message}
        </div>
      )}

      <div className="max-w-2xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-white text-xl font-bold mb-1">Bot Settings</h2>
            <p className="text-gray-400 text-sm">Konfigurasi bot WhatsApp • Perubahan berkuat kuasa serta-merta</p>
          </div>
          <button
            onClick={handleReset}
            className="text-gray-400 hover:text-gray-200 text-xs px-3 py-1.5 rounded-lg border border-gray-700 hover:border-gray-500 transition-colors"
          >
            Reset
          </button>
        </div>

        {/* Validation Errors */}
        {errors.length > 0 && (
          <div className="bg-red-900/30 border border-red-700 rounded-xl p-4 mb-4">
            <p className="text-red-400 text-sm font-medium mb-1">Ralat:</p>
            <ul className="text-red-300 text-xs space-y-0.5">
              {errors.map((err, i) => <li key={i}>• {err}</li>)}
            </ul>
          </div>
        )}

        {/* ═══ GENERAL ═══ */}
        <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 mb-4">
          <h3 className="text-gray-200 text-sm font-semibold mb-4 flex items-center gap-2">
            <span className="w-5 h-5 bg-emerald-600 rounded flex items-center justify-center text-[10px]">⚙️</span>
            General
          </h3>

          <div className="space-y-4">
            {/* Bot Active */}
            <div className="flex items-center justify-between">
              <div>
                <label className="text-gray-300 text-sm font-medium">Bot Active</label>
                <p className="text-gray-500 text-xs">Aktifkan/matikan bot WhatsApp</p>
              </div>
              <button
                onClick={() => handleChange('bot_active', form.bot_active === 'true' ? 'false' : 'true')}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  form.bot_active === 'true' ? 'bg-emerald-600' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  form.bot_active === 'true' ? 'translate-x-5' : ''
                }`} />
              </button>
            </div>

            {/* AI Enabled */}
            <div className="flex items-center justify-between">
              <div>
                <label className="text-gray-300 text-sm font-medium">AI Mode</label>
                <p className="text-gray-500 text-xs">Aktifkan AI untuk jawab soalan (restart required)</p>
              </div>
              <button
                onClick={() => handleChange('ai_enabled', form.ai_enabled === 'true' ? 'false' : 'true')}
                className={`relative w-11 h-6 rounded-full transition-colors ${
                  form.ai_enabled === 'true' ? 'bg-emerald-600' : 'bg-gray-600'
                }`}
              >
                <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                  form.ai_enabled === 'true' ? 'translate-x-5' : ''
                }`} />
              </button>
            </div>

            {/* Greeting Message */}
            <div>
              <label className="block text-gray-300 text-sm font-medium mb-1.5">Greeting Message</label>
              <p className="text-gray-500 text-xs mb-2">Mesej selamat datang bila user mula perbualan</p>
              <textarea
                value={form.greeting_message || ''}
                onChange={(e) => handleChange('greeting_message', e.target.value)}
                rows={3}
                className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none resize-none font-mono"
                placeholder="Waalaikumsalam! Selamat datang ke HAFJET..."
              />
            </div>

            {/* Fallback Message */}
            <div>
              <label className="block text-gray-300 text-sm font-medium mb-1.5">Fallback Reply</label>
              <p className="text-gray-500 text-xs mb-2">Mesej default bila AI gagal jawab</p>
              <textarea
                value={form.fallback_message || ''}
                onChange={(e) => handleChange('fallback_message', e.target.value)}
                rows={2}
                className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none resize-none font-mono"
                placeholder="Maaf, saya tak faham. Cuba lagi..."
              />
            </div>
          </div>
        </div>

        {/* ═══ AI ═══ */}
        <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 mb-4">
          <h3 className="text-gray-200 text-sm font-semibold mb-4 flex items-center gap-2">
            <span className="w-5 h-5 bg-purple-600 rounded flex items-center justify-center text-[10px]">🤖</span>
            AI Configuration
          </h3>

          <div className="space-y-4">
            {/* Model */}
            <div>
              <label className="block text-gray-300 text-sm font-medium mb-1.5">Model</label>
              <input
                type="text"
                value={form.ai_model || ''}
                onChange={(e) => handleChange('ai_model', e.target.value)}
                className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none font-mono"
                placeholder="nvidia/nemotron-3-super-120b-a12b:free"
              />
              <p className="text-gray-500 text-[10px] mt-1">Restart required to apply</p>
            </div>

            {/* Timeout + Temperature + Max Tokens in grid */}
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-gray-300 text-sm font-medium mb-1.5">Timeout (s)</label>
                <input
                  type="number"
                  min="3"
                  max="60"
                  value={form.ai_timeout || '10'}
                  onChange={(e) => handleChange('ai_timeout', e.target.value)}
                  className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-gray-300 text-sm font-medium mb-1.5">Temperature</label>
                <input
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
                  value={form.ai_temperature || '0.3'}
                  onChange={(e) => handleChange('ai_temperature', e.target.value)}
                  className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-gray-300 text-sm font-medium mb-1.5">Max Tokens</label>
                <input
                  type="number"
                  min="50"
                  max="1000"
                  step="50"
                  value={form.ai_max_tokens || '150'}
                  onChange={(e) => handleChange('ai_max_tokens', e.target.value)}
                  className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none"
                />
              </div>
            </div>

            {/* OpenRouter Key */}
            <div>
              <label className="block text-gray-300 text-sm font-medium mb-1.5">OpenRouter API Key</label>
              <input
                type="password"
                value={form.openrouter_key || ''}
                onChange={(e) => handleChange('openrouter_key', e.target.value)}
                className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none font-mono"
                placeholder="sk-or-..."
              />
              <p className="text-gray-500 text-[10px] mt-1">Restart required to apply</p>
            </div>
          </div>
        </div>

        {/* ═══ ROUTING ═══ */}
        <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 mb-6">
          <h3 className="text-gray-200 text-sm font-semibold mb-4 flex items-center gap-2">
            <span className="w-5 h-5 bg-amber-600 rounded flex items-center justify-center text-[10px]">🔀</span>
            Routing & Keywords
          </h3>

          <div className="space-y-4">
            {/* Dedup Window */}
            <div>
              <label className="block text-gray-300 text-sm font-medium mb-1.5">Dedup Window (saat)</label>
              <p className="text-gray-500 text-xs mb-2">Mesej dari ID yang sama dalam tempoh ini akan diabaikan (30-3600 saat)</p>
              <input
                type="number"
                min="30"
                max="3600"
                value={form.dedup_window || '300'}
                onChange={(e) => handleChange('dedup_window', e.target.value)}
                className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none"
              />
            </div>

            {/* Greeting Keywords */}
            <div>
              <label className="block text-gray-300 text-sm font-medium mb-1.5">Greeting Keywords</label>
              <p className="text-gray-500 text-xs mb-2">Keywords yang trigger greeting reply (comma-separated)</p>
              <input
                type="text"
                value={form.greeting_keywords || ''}
                onChange={(e) => handleChange('greeting_keywords', e.target.value)}
                className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none font-mono"
                placeholder="halo,hi,hello,selamat,assalamualaikum"
              />
            </div>

            {/* Escalation Keywords */}
            <div>
              <label className="block text-gray-300 text-sm font-medium mb-1.5">Escalation Keywords</label>
              <p className="text-gray-500 text-xs mb-2">Keywords yang trigger escalation (comma-separated)</p>
              <input
                type="text"
                value={form.escalation_keywords || ''}
                onChange={(e) => handleChange('escalation_keywords', e.target.value)}
                className="w-full bg-gray-800 text-gray-200 text-sm p-3 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none font-mono"
                placeholder="staff,mana,speaker,call,telefon"
              />
            </div>
          </div>
        </div>

        {/* Save Button */}
        <div className="flex gap-3">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium py-3 px-4 rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            {saving ? (
              <>
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Menyimpan...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
                Simpan Tetapan
              </>
            )}
          </button>
          <button
            onClick={loadSettings}
            className="px-4 bg-gray-700 hover:bg-gray-600 text-gray-200 font-medium py-3 rounded-lg transition-colors"
          >
            Cancel
          </button>
        </div>

        <p className="text-gray-600 text-[10px] text-center mt-3">
          * Settings dengan tanda "Restart required" perlu restart guna pakai untuk berkuat kuasa
        </p>
      </div>
    </div>
  );
}
