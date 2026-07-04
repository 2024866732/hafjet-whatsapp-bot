/**
 * CustomerInfo — Panel kanan: customer details + actions
 * Wired to backend operator APIs
 */
import { useState, useEffect } from 'react';
import { fetchMessages, resolveCustomer, escalateCustomer, updateNote, staffReply } from '../api/api';
import { formatPhone, formatDate, timeAgo } from '../utils/format';

// Status badge component
function StatusBadge({ status }) {
  const styles = {
    active: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    escalated: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    resolved: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    handoff: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  };
  const labels = {
    active: 'ACTIVE',
    escalated: 'ESCALATED',
    resolved: 'RESOLVED',
    handoff: 'HANDOFF',
  };
  return (
    <span className={`inline-block px-2 py-0.5 text-[10px] font-bold rounded border ${styles[status] || styles.active}`}>
      {labels[status] || status?.toUpperCase() || 'ACTIVE'}
    </span>
  );
}

// Toast notification
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

export default function CustomerInfo({ phone, onCustomerUpdate }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(null); // 'resolve' | 'escalate' | 'note'
  const [toast, setToast] = useState(null);
  const [customerStatus, setCustomerStatus] = useState('active');
  const [showNoteInput, setShowNoteInput] = useState(false);
  const [noteText, setNoteText] = useState('');
  const [showHandoff, setShowHandoff] = useState(false);
  const [handoffMessage, setHandoffMessage] = useState('');

  const loadMessages = async () => {
    if (!phone) return;
    setLoading(true);
    try {
      const data = await fetchMessages(phone);
      setMessages(data);
      // Derive status from customer data (latest message source tracking)
      if (data.length > 0) {
        const lastMsg = data[data.length - 1];
        if (lastMsg?.direction === 'inbound') {
          setCustomerStatus('active');
        }
      }
    } catch (e) {
      console.error('Failed to load messages:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMessages();
  }, [phone]);

  const showToast = (message, type = 'success') => {
    setToast({ message, type });
  };

  const handleResolve = async () => {
    setActionLoading('resolve');
    try {
      const result = await resolveCustomer(phone);
      setCustomerStatus('resolved');
      showToast('✅ Pelanggan ditandai selesai');
      if (onCustomerUpdate) onCustomerUpdate(result.customer);
    } catch (e) {
      showToast('❌ Gagal tandai selesai', 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleEscalate = async () => {
    setActionLoading('escalate');
    try {
      const result = await escalateCustomer(phone);
      setCustomerStatus('escalated');
      showToast('⚠️ Pelanggan dieskalate ke staff');
      if (onCustomerUpdate) onCustomerUpdate(result.customer);
    } catch (e) {
      showToast('❌ Gagal escalate', 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleHandoff = async () => {
    setActionLoading('handoff');
    try {
      const result = await staffReply(phone, handoffMessage);
      setCustomerStatus('handoff');
      showToast(result.sent ? '✅ Mesej dihantar' : '⚠️ Mesej mungkin gagal');
      setShowHandoff(false);
      setHandoffMessage('');
      // Reload messages to show the sent message
      loadMessages();
      if (onCustomerUpdate) onCustomerUpdate(result.customer);
    } catch (e) {
      showToast('❌ Gagal hantar mesej', 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleSaveNote = async () => {
    if (!noteText.trim()) return;
    setActionLoading('note');
    try {
      await updateNote(phone, noteText.trim());
      setShowNoteInput(false);
      setNoteText('');
      showToast('📝 Note disimpan');
    } catch (e) {
      showToast('❌ Gagal simpan note', 'error');
    } finally {
      setActionLoading(null);
    }
  };

  // Placeholder state
  if (!phone) {
    return (
      <div className="w-[300px] min-w-[300px] bg-gray-900 border-l border-gray-700 flex items-center justify-center h-full">
        <div className="text-center px-6">
          <svg className="w-12 h-12 mx-auto text-gray-600 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
          <p className="text-gray-500 text-sm">Pilih pelanggan untuk lihat info</p>
        </div>
      </div>
    );
  }

  const customerName = messages.find((m) => m.from_name)?.name || formatPhone(phone);
  const totalMessages = messages.length;
  const inboundCount = messages.filter((m) => m.direction === 'inbound').length;
  const outboundCount = messages.filter((m) => m.direction === 'outbound').length;
  const firstContact = messages[0] ? timeAgo(messages[0].timestamp) : '-';
  const lastContact = messages.length > 0 ? timeAgo(messages[messages.length - 1].timestamp) : '-';
  const botReplies = messages.filter((m) => m.source === 'bot').length;

  return (
    <div className="w-[300px] min-w-[300px] bg-gray-900 border-l border-gray-700 flex flex-col h-full overflow-y-auto">
      {/* Toast */}
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onClose={() => setToast(null)}
        />
      )}

      {/* Customer Header */}
      <div className="p-4 border-b border-gray-700 text-center">
        <div className="w-16 h-16 bg-emerald-600 rounded-full flex items-center justify-center mx-auto mb-3">
          <span className="text-white text-2xl font-bold">
            {customerName[0]?.toUpperCase() || '?'}
          </span>
        </div>
        <div className="flex items-center justify-center gap-2 mb-1">
          <h3 className="text-white font-medium text-sm">{customerName}</h3>
          <StatusBadge status={customerStatus} />
        </div>
        <p className="text-gray-400 text-xs">{formatPhone(phone)}</p>
      </div>

      {/* Quick Stats */}
      <div className="p-4 border-b border-gray-700">
        <h4 className="text-gray-400 text-[10px] font-semibold uppercase tracking-wider mb-3">
          Statistik
        </h4>
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-gray-800 rounded-lg p-2.5 text-center">
            <p className="text-white font-bold text-lg">{totalMessages}</p>
            <p className="text-gray-500 text-[10px]">Total Mesej</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-2.5 text-center">
            <p className="text-emerald-400 font-bold text-lg">{outboundCount}</p>
            <p className="text-gray-500 text-[10px]">Balasan Bot</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-2.5 text-center">
            <p className="text-blue-400 font-bold text-lg">{inboundCount}</p>
            <p className="text-gray-500 text-[10px]">Mesej Masuk</p>
          </div>
          <div className="bg-gray-800 rounded-lg p-2.5 text-center">
            <p className="text-yellow-400 font-bold text-lg">{botReplies}</p>
            <p className="text-gray-500 text-[10px]">AI/Bot Reply</p>
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="p-4 border-b border-gray-700">
        <h4 className="text-gray-400 text-[10px] font-semibold uppercase tracking-wider mb-3">
          Timeline
        </h4>
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-emerald-400 rounded-full" />
            <div>
              <p className="text-gray-300 text-xs">Pertama contact</p>
              <p className="text-gray-500 text-[10px]">{firstContact}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-blue-400 rounded-full" />
            <div>
              <p className="text-gray-300 text-xs">Terakhir aktif</p>
              <p className="text-gray-500 text-[10px]">{lastContact}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Note Section */}
      <div className="p-4 border-b border-gray-700">
        <h4 className="text-gray-400 text-[10px] font-semibold uppercase tracking-wider mb-2">
          Note
        </h4>
        {showNoteInput ? (
          <div className="space-y-2">
            <textarea
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              placeholder="Tulis note..."
              className="w-full bg-gray-800 text-gray-200 text-xs p-2 rounded-lg border border-gray-700 focus:border-emerald-500 focus:outline-none resize-none"
              rows={3}
            />
            <div className="flex gap-2">
              <button
                onClick={handleSaveNote}
                disabled={actionLoading === 'note'}
                className="flex-1 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-xs py-1.5 rounded transition-colors"
              >
                {actionLoading === 'note' ? 'Menyimpan...' : 'Simpan'}
              </button>
              <button
                onClick={() => { setShowNoteInput(false); setNoteText(''); }}
                className="px-3 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs py-1.5 rounded transition-colors"
              >
                Batal
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setShowNoteInput(true)}
            className="w-full text-left text-gray-500 hover:text-gray-300 text-xs py-1 transition-colors"
          >
            + Tambah note
          </button>
        )}
      </div>

      {/* Actions */}
      <div className="p-4">
        <h4 className="text-gray-400 text-[10px] font-semibold uppercase tracking-wider mb-3">
          Tindakan
        </h4>
        <div className="space-y-2">
          {/* Take Over / Handoff */}
          <div className="bg-gray-800 rounded-lg p-3">
            <p className="text-gray-400 text-[10px] mb-2">Manual Takeover — hantar mesej sebagai staff</p>
            {showHandoff ? (
              <div className="space-y-2">
                <textarea
                  value={handoffMessage}
                  onChange={(e) => setHandoffMessage(e.target.value)}
                  placeholder="Tulis mesej untuk pelanggan..."
                  className="w-full bg-gray-700 text-gray-200 text-xs p-2 rounded border border-gray-600 focus:border-emerald-500 focus:outline-none resize-none"
                  rows={2}
                />
                <div className="flex gap-2">
                  <button
                    onClick={handleHandoff}
                    disabled={actionLoading === 'handoff' || !handoffMessage.trim()}
                    className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs py-2 rounded transition-colors flex items-center justify-center gap-1"
                  >
                    {actionLoading === 'handoff' ? (
                      <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                      </svg>
                    )}
                    Hantar & Take Over
                  </button>
                  <button
                    onClick={() => { setShowHandoff(false); setHandoffMessage(''); }}
                    className="px-3 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs py-2 rounded transition-colors"
                  >
                    Batal
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setShowHandoff(true)}
                disabled={customerStatus === 'handoff'}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium py-2 px-3 rounded-lg transition-colors flex items-center justify-center gap-2"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
                {customerStatus === 'handoff' ? 'Sedang Handoff' : 'Take Over'}
              </button>
            )}
          </div>

          <button
            onClick={handleResolve}
            disabled={actionLoading === 'resolve' || customerStatus === 'resolved'}
            className="w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium py-2 px-3 rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            {actionLoading === 'resolve' ? (
              <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            )}
            {customerStatus === 'resolved' ? '✓ Sudah Selesai' : 'Tandai Selesai'}
          </button>
          <button
            onClick={handleEscalate}
            disabled={actionLoading === 'escalate' || customerStatus === 'escalated'}
            className="w-full bg-amber-600 hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium py-2 px-3 rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            {actionLoading === 'escalate' ? (
              <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            )}
            {customerStatus === 'escalated' ? '⚡ Sudah Dieskalate' : 'Escalate ke Staff'}
          </button>
          <button
            onClick={loadMessages}
            className="w-full bg-gray-700 hover:bg-gray-600 text-gray-200 text-xs font-medium py-2 px-3 rounded-lg transition-colors flex items-center justify-center gap-2"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh Data
          </button>
        </div>
      </div>
    </div>
  );
}
