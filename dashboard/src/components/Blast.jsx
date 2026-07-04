/**
 * Blast — Broadcast / Blast message page
 * Compose, preview, schedule, and send blast messages to customers
 */
import { useState, useEffect } from 'react';
import { createBlast, fetchBlastHistory } from '../api/api';
import { formatPhone } from '../utils/format';

function Toast({ message, type, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 3000);
    return () => clearTimeout(t);
  }, [onClose]);
  const bg = type === 'success' ? 'bg-emerald-600' : type === 'error' ? 'bg-red-600' : 'bg-blue-600';
  return (
    <div className={`fixed top-4 right-4 ${bg} text-white px-4 py-2 rounded-lg shadow-lg text-sm z-50 animate-fade-in`}>
      {message}
    </div>
  );
}

export default function Blast() {
  const [message, setMessage] = useState('');
  const [recipientMode, setRecipientMode] = useState('all'); // 'all' | 'select'
  const [scheduleMode, setScheduleMode] = useState('now'); // 'now' | 'schedule'
  const [scheduleDate, setScheduleDate] = useState('');
  const [sending, setSending] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [toast, setToast] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(true);

  const showToast = (msg, type = 'success') => setToast({ message: msg, type });

  const loadHistory = async () => {
    setHistoryLoading(true);
    try {
      const data = await fetchBlastHistory(50);
      setHistory(data);
    } catch (e) {
      console.error('Failed to load blast history:', e);
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => { loadHistory(); }, []);

  const handleSend = async () => {
    if (!message.trim()) return;
    setSending(true);
    setShowConfirm(false);
    try {
      const scheduleAt = scheduleMode === 'schedule' && scheduleDate
        ? new Date(scheduleDate).toISOString()
        : null;
      const result = await createBlast(message.trim(), recipientMode === 'all' ? 'all' : [], scheduleAt);
      showToast(`📢 Blast dihantar ke ${result.total_recipients} pelanggan`);
      setMessage('');
      loadHistory();
    } catch (e) {
      showToast(`❌ Gagal hantar blast: ${e.message}`, 'error');
    } finally {
      setSending(false);
    }
  };

  const statusBadge = (status) => {
    const styles = {
      pending: 'bg-yellow-500/20 text-yellow-400',
      sending: 'bg-blue-500/20 text-blue-400',
      sent: 'bg-emerald-500/20 text-emerald-400',
      failed: 'bg-red-500/20 text-red-400',
    };
    return (
      <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${styles[status] || 'bg-gray-500/20 text-gray-400'}`}>
        {status?.toUpperCase() || 'UNKNOWN'}
      </span>
    );
  };

  return (
    <div className="flex-1 overflow-y-auto bg-gray-900">
      {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

      <div className="max-w-6xl mx-auto p-6">
        {/* Page Header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <svg className="w-6 h-6 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" />
            </svg>
            Blast Broadcast
          </h1>
          <p className="text-gray-400 text-sm mt-1">Hantar mesej pukal ke pelanggan</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Compose Panel */}
          <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
            <h2 className="text-white font-semibold text-sm mb-4">Compose Message</h2>

            {/* Message Textarea */}
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Tulis mesej blast..."
              className="w-full bg-gray-700 text-gray-200 text-sm p-3 rounded-lg border border-gray-600 focus:border-emerald-500 focus:outline-none resize-none"
              rows={5}
              maxLength={4096}
            />
            <p className="text-gray-500 text-[10px] mt-1 text-right">{message.length}/4096</p>

            {/* Recipients */}
            <div className="mt-4">
              <label className="text-gray-400 text-xs font-medium block mb-2">Recipients</label>
              <div className="flex gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="recipients"
                    checked={recipientMode === 'all'}
                    onChange={() => setRecipientMode('all')}
                    className="accent-emerald-500"
                  />
                  <span className="text-gray-300 text-sm">Semua contacts</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer opacity-50">
                  <input
                    type="radio"
                    name="recipients"
                    checked={recipientMode === 'select'}
                    onChange={() => setRecipientMode('select')}
                    disabled
                  />
                  <span className="text-gray-300 text-sm">Pilih manually (coming soon)</span>
                </label>
              </div>
            </div>

            {/* Schedule */}
            <div className="mt-4">
              <label className="text-gray-400 text-xs font-medium block mb-2">Schedule</label>
              <div className="flex gap-3">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="schedule"
                    checked={scheduleMode === 'now'}
                    onChange={() => setScheduleMode('now')}
                    className="accent-emerald-500"
                  />
                  <span className="text-gray-300 text-sm">Hantar sekarang</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="schedule"
                    checked={scheduleMode === 'schedule'}
                    onChange={() => setScheduleMode('schedule')}
                    className="accent-emerald-500"
                  />
                  <span className="text-gray-300 text-sm">Schedule</span>
                </label>
              </div>
              {scheduleMode === 'schedule' && (
                <input
                  type="datetime-local"
                  value={scheduleDate}
                  onChange={(e) => setScheduleDate(e.target.value)}
                  className="mt-2 w-full bg-gray-700 text-gray-200 text-sm p-2 rounded-lg border border-gray-600 focus:border-emerald-500 focus:outline-none"
                />
              )}
            </div>

            {/* Send Button */}
            <button
              onClick={() => setShowConfirm(true)}
              disabled={!message.trim() || sending}
              className="mt-4 w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
            >
              {sending ? (
                <>
                  <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Menghantar...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                  </svg>
                  Hantar Blast
                </>
              )}
            </button>
          </div>

          {/* Preview Panel */}
          <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
            <h2 className="text-white font-semibold text-sm mb-4">Preview</h2>
            <div className="bg-gray-850 rounded-lg p-4 min-h-[200px] border border-gray-700">
              {message.trim() ? (
                <div className="flex gap-3">
                  <div className="w-8 h-8 bg-emerald-600 rounded-full flex items-center justify-center flex-shrink-0">
                    <span className="text-white text-xs font-bold">B</span>
                  </div>
                  <div>
                    <div className="bg-gray-700 rounded-lg rounded-tl-none px-3 py-2 max-w-md">
                      <p className="text-gray-200 text-sm whitespace-pre-wrap">{message}</p>
                    </div>
                    <p className="text-gray-500 text-[10px] mt-1">Blast message</p>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-center h-full">
                  <p className="text-gray-500 text-sm">Taip mesej untuk preview</p>
                </div>
              )}
            </div>
            <div className="mt-3 flex items-center gap-3 text-xs text-gray-400">
              <div className="flex items-center gap-1">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
                {recipientMode === 'all' ? 'Semua pelanggan' : 'Pilihan manual'}
              </div>
              <div className="flex items-center gap-1">
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                {scheduleMode === 'now' ? 'Sekarang' : scheduleDate || 'Pilih masa'}
              </div>
            </div>
          </div>
        </div>

        {/* Blast History */}
        <div className="mt-8">
          <h2 className="text-white font-semibold text-sm mb-4">History</h2>
          <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
            {historyLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
              </div>
            ) : history.length === 0 ? (
              <div className="text-center py-12 px-4">
                <svg className="w-12 h-12 mx-auto text-gray-600 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" />
                </svg>
                <p className="text-gray-500 text-sm">Tiada blast history lagi</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-700 text-gray-400 text-xs uppercase">
                      <th className="text-left p-3 font-medium">Tarikh</th>
                      <th className="text-left p-3 font-medium">Message</th>
                      <th className="text-center p-3 font-medium">Recipients</th>
                      <th className="text-center p-3 font-medium">Sent</th>
                      <th className="text-center p-3 font-medium">Failed</th>
                      <th className="text-center p-3 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((blast) => (
                      <tr key={blast.id} className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors">
                        <td className="p-3 text-gray-300 whitespace-nowrap text-xs">
                          {blast.created_at || '-'}
                        </td>
                        <td className="p-3 text-gray-300 max-w-xs truncate">
                          {blast.message || '-'}
                        </td>
                        <td className="p-3 text-gray-300 text-center">
                          {blast.total_recipients || 0}
                        </td>
                        <td className="p-3 text-emerald-400 text-center font-medium">
                          {blast.sent_count || 0}
                        </td>
                        <td className="p-3 text-red-400 text-center">
                          {blast.failed_count || 0}
                        </td>
                        <td className="p-3 text-center">
                          {statusBadge(blast.status)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Confirm Dialog */}
      {showConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-xl p-6 max-w-md mx-4 border border-gray-700 shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 bg-amber-500/20 rounded-full flex items-center justify-center">
                <svg className="w-5 h-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <div>
                <h3 className="text-white font-semibold">Confirm Blast</h3>
                <p className="text-gray-400 text-sm">Tuan pasti nak hantar blast ni?</p>
              </div>
            </div>
            <div className="bg-gray-850 rounded-lg p-3 mb-4 border border-gray-700">
              <p className="text-gray-300 text-sm whitespace-pre-wrap line-clamp-3">{message}</p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setShowConfirm(false)}
                className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm py-2 rounded-lg transition-colors"
              >
                Batal
              </button>
              <button
                onClick={handleSend}
                className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white text-sm py-2 rounded-lg transition-colors"
              >
                Ya, Hantar!
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
