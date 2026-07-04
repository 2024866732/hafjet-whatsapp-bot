/** 
 * Keywords — Keyword Bot Builder 
 * Auto-reply rules without AI
 */
import { useState, useEffect } from 'react';
import { fetchKeywords, createKeyword, updateKeyword, deleteKeyword, testKeyword } from '../api/api';

const EMPTY_FORM = { keyword: '', reply: '', priority: 10, match_type: 'contains' };

export default function Keywords() {
  const [keywords, setKeywords] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [editRule, setEditRule] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [testMsg, setTestMsg] = useState('');
  const [testResult, setTestResult] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadKeywords = async () => {
    try {
      const data = await fetchKeywords();
      setKeywords(data.keywords || []);
    } catch (e) {
      console.error('Failed to fetch keywords', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadKeywords();
  }, []);

  const handleSave = async () => {
    try {
      if (editRule) {
        await updateKeyword(editRule.id, form);
      } else {
        await createKeyword(form);
      }
      setShowModal(false);
      setEditRule(null);
      setForm(EMPTY_FORM);
      loadKeywords();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleEdit = (rule) => {
    setEditRule(rule);
    setForm({ keyword: rule.keyword, reply: rule.reply, priority: rule.priority, match_type: rule.match_type });
    setShowModal(true);
  };

  const handleDelete = async (id) => {
    if (!confirm('Padam keyword rule ini?')) return;
    try {
      await deleteKeyword(id);
      loadKeywords();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleToggle = async (rule) => {
    try {
      await updateKeyword(rule.id, { is_active: rule.is_active ? 0 : 1 });
      loadKeywords();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleTest = async () => {
    if (!testMsg.trim()) return;
    try {
      const result = await testKeyword(testMsg);
      setTestResult(result);
    } catch (e) {
      alert(e.message);
    }
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Keyword Bot</h1>
          <p className="text-gray-400 text-sm mt-1">Auto-reply berdasarkan keyword tanpa AI</p>
        </div>
        <button
          onClick={() => { setEditRule(null); setForm(EMPTY_FORM); setShowModal(true); }}
          className="bg-[#00d563] hover:bg-[#00c255] text-black px-4 py-2 rounded-lg text-sm font-medium transition"
        >
          + Tambah Keyword Rule
        </button>
      </div>

      {/* Test Panel */}
      <div className="bg-[#1a1d27] rounded-xl p-4 mb-6">
        <h3 className="text-white font-medium mb-3">Test Keyword</h3>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Taip mesej untuk test..."
            value={testMsg}
            onChange={(e) => setTestMsg(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleTest()}
            className="flex-1 bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-2 text-white text-sm placeholder-gray-500 focus:outline-none focus:border-[#00d563]"
          />
          <button
            onClick={handleTest}
            className="bg-[#00d563] hover:bg-[#00c255] text-black px-4 py-2 rounded-lg text-sm font-medium transition"
          >
            Test
          </button>
        </div>
        {testResult && (
          <div className={`mt-3 p-3 rounded-lg border ${testResult.matched ? 'bg-green-900/20 border-green-500/50' : 'bg-gray-800 border-gray-700'}`}>
            {testResult.matched ? (
              <div>
                <span className="text-green-400 font-medium">✅ Match!</span>
                <p className="text-gray-300 text-sm mt-1">
                  Keyword: <span className="font-mono text-blue-300">"{testResult.keyword}"</span> ({testResult.match_type})
                </p>
                <p className="text-gray-300 text-sm">
                  Reply: <span className="text-white">"{testResult.reply}"</span>
                </p>
              </div>
            ) : (
              <span className="text-gray-400">❌ Tiada keyword match — akan dihantar ke AI</span>
            )}
          </div>
        )}
      </div>

      {/* Rules List */}
      {loading ? (
        <div className="text-gray-400 text-sm">Loading keyword rules...</div>
      ) : keywords.length === 0 ? (
        <div className="text-gray-500 text-sm">Tiada keyword rule. Klik "+ Tambah Keyword Rule" untuk mula.</div>
      ) : (
        <div className="rules-list space-y-3">
          {keywords.map((rule, index) => (
            <div
              key={rule.id}
              className={`bg-[#1a1d27] rounded-xl p-4 flex items-center gap-4 ${!rule.is_active ? 'opacity-50' : ''}`}
            >
              <span className="text-gray-500 text-sm w-6 text-center font-medium">{index + 1}</span>
              <button
                onClick={() => handleToggle(rule)}
                className={`w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${rule.is_active ? 'bg-[#00d563]' : 'bg-gray-600'}`}
                title={rule.is_active ? 'Active — click to disable' : 'Inactive — click to enable'}
              >
                <div className={`w-3 h-3 bg-white rounded-full shadow transition-transform duration-200 ${rule.is_active ? 'translate-x-5' : 'translate-x-1'}`} />
              </button>
              <span className="bg-blue-900/40 text-blue-300 px-3 py-1 rounded-full text-sm font-mono whitespace-nowrap">
                {rule.match_type === 'exact' ? '=' : '~'} {rule.keyword}
              </span>
              <span className="text-gray-500">→</span>
              <span className="text-gray-300 flex-1 truncate text-sm">{rule.reply}</span>
              <div className="flex gap-2 flex-shrink-0">
                <button
                  onClick={() => handleEdit(rule)}
                  className="text-gray-400 hover:text-white p-1.5 rounded-lg hover:bg-gray-700 transition"
                  title="Edit"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                </button>
                <button
                  onClick={() => handleDelete(rule.id)}
                  className="text-gray-400 hover:text-red-400 p-1.5 rounded-lg hover:bg-gray-700 transition"
                  title="Delete"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-[#1a1d27] rounded-xl p-6 w-full max-w-lg border border-gray-700">
            <h3 className="text-white font-semibold text-lg mb-4">
              {editRule ? 'Edit Keyword Rule' : 'Tambah Keyword Rule Baru'}
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-gray-400 text-sm mb-1">Keyword Trigger</label>
                <input
                  type="text"
                  placeholder="contoh: harga, lokasi, waktu"
                  value={form.keyword}
                  onChange={(e) => setForm({ ...form, keyword: e.target.value })}
                  className="w-full bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-2 text-white text-sm placeholder-gray-500 focus:outline-none focus:border-[#00d563]"
                />
              </div>
              <div>
                <label className="block text-gray-400 text-sm mb-1">Match Type</label>
                <select
                  value={form.match_type}
                  onChange={(e) => setForm({ ...form, match_type: e.target.value })}
                  className="w-full bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00d563]"
                >
                  <option value="contains">Contains — keyword dalam mesej</option>
                  <option value="exact">Exact — mesej persis sama keyword</option>
                </select>
              </div>
              <div>
                <label className="block text-gray-400 text-sm mb-1">Auto-Reply Message</label>
                <textarea
                  rows={4}
                  placeholder="Mesej yang akan dihantar bila keyword ini trigger..."
                  value={form.reply}
                  onChange={(e) => setForm({ ...form, reply: e.target.value })}
                  className="w-full bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-2 text-white text-sm placeholder-gray-500 focus:outline-none focus:border-[#00d563] resize-none"
                />
              </div>
              <div>
                <label className="block text-gray-400 text-sm mb-1">Priority (nombor kecil = lebih tinggi)</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={form.priority}
                  onChange={(e) => setForm({ ...form, priority: parseInt(e.target.value) || 1 })}
                  className="w-full bg-[#0f1117] border border-gray-700 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00d563]"
                />
              </div>
            </div>
            <div className="flex gap-2 mt-6">
              <button
                onClick={handleSave}
                className="bg-[#00d563] hover:bg-[#00c255] text-black px-4 py-2 rounded-lg text-sm font-medium transition flex-1"
              >
                Simpan
              </button>
              <button
                onClick={() => { setShowModal(false); setEditRule(null); setForm(EMPTY_FORM); }}
                className="bg-gray-700 hover:bg-gray-600 text-white px-4 py-2 rounded-lg text-sm font-medium transition flex-1"
              >
                Batal
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
