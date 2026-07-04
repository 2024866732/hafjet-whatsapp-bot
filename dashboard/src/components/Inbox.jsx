/**
 * Inbox — Multi-Agent Inbox with filters + status workflow
 */
import { useState, useEffect } from 'react';
import { fetchInbox, assignConversation, fetchStaffList, updateConversationStatus } from '../api/api';

const FILTERS = [
  { key: 'all', label: 'Semua' },
  { key: 'me', label: 'Saya' },
  { key: 'unassigned', label: 'Belum Ditugaskan' },
  { key: 'escalated', label: '🚨 Escalated' },
  { key: 'resolved', label: '✅ Resolved' },
];

const STATUS_COLORS = {
  online: 'bg-[#00d563]',
  busy: 'bg-yellow-500',
  offline: 'bg-gray-500',
};

const STATUS_BADGES = {
  bot_active: { emoji: '🤖', color: 'bg-blue-500', label: 'Bot' },
  assigned: { emoji: '👤', color: 'bg-yellow-600', label: 'Assigned' },
  escalated: { emoji: '🚨', color: 'bg-red-500', label: 'Escalated' },
  resolved: { emoji: '✅', color: 'bg-green-600', label: 'Resolved' },
};

export default function Inbox({ staffInfo, onLogout }) {
  const [filter, setFilter] = useState('all');
  const [conversations, setConversations] = useState([]);
  const [staffList, setStaffList] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedPhone, setSelectedPhone] = useState(null);

  const loadData = async () => {
    try {
      const [convData, staffData] = await Promise.all([
        fetchInbox(filter),
        fetchStaffList().catch(() => ({ staff: [] })),
      ]);
      setConversations(convData.conversations || []);
      setStaffList(staffData.staff || []);
    } catch (e) {
      console.error('Failed to load inbox', e);
      if (e.message?.includes('401') || e.message?.includes('token')) {
        onLogout();
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 30000);
    return () => clearInterval(interval);
  }, [filter]);

  const getAgent = (phone) => {
    if (!phone) return null;
    return staffList.find((s) => s.assigned_phone === phone);
  };

  const handleAssign = async (phone, staffId) => {
    try {
      await assignConversation(phone, staffId);
      loadData();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleStatusAction = async (phone, newStatus) => {
    try {
      await updateConversationStatus(phone, newStatus);
      loadData();
    } catch (e) {
      alert(e.message);
    }
  };

  const formatTime = (ts) => {
    if (!ts) return '';
    const d = new Date(ts);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    if (isToday) return d.toLocaleTimeString('ms-MY', { hour: '2-digit', minute: '2-digit' });
    return d.toLocaleDateString('ms-MY', { day: 'numeric', month: 'short' });
  };

  const getStatusBadge = (conv) => {
    const status = conv.status || 'bot_active';
    const badge = STATUS_BADGES[status] || STATUS_BADGES.bot_active;
    return (
      <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full ${badge.color} text-white font-medium`}>
        <span>{badge.emoji}</span>
        <span>{badge.label}</span>
      </span>
    );
  };

  const selectedConv = conversations.find(c => c.phone === selectedPhone);
  const selectedStatus = selectedConv?.status || 'bot_active';

  return (
    <div className="h-full flex overflow-hidden bg-[#0f1117]">
      {/* Sidebar - Conversation List */}
      <div className="w-80 flex-shrink-0 bg-[#1a1d27] border-r border-gray-800 flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-gray-800">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-white font-semibold text-sm">Inbox</h2>
              <p className="text-gray-500 text-xs">{staffInfo?.name} ({staffInfo?.role})</p>
            </div>
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${STATUS_COLORS[staffInfo?.status] || STATUS_COLORS.offline}`} />
              <button
                onClick={onLogout}
                className="text-gray-400 hover:text-red-400 text-xs transition"
              >
                Logout
              </button>
            </div>
          </div>
          {/* Filter Tabs */}
          <div className="flex flex-wrap gap-1 bg-[#0f1117] rounded-lg p-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => { setFilter(f.key); setLoading(true); }}
                className={`flex-1 text-[10px] py-1.5 rounded-md transition ${
                  filter === f.key
                    ? 'bg-[#00d563] text-black font-medium'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* Conversation List */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="text-gray-500 text-sm text-center py-8">Loading...</div>
          ) : conversations.length === 0 ? (
            <div className="text-gray-500 text-sm text-center py-8">Tiada perbualan</div>
          ) : (
            conversations.map((conv) => {
              const agent = getAgent(conv.phone);
              const isMe = conv.assigned_to === String(staffInfo?.id) || agent?.id === staffInfo?.id;
              return (
                <div
                  key={conv.phone}
                  onClick={() => { setSelectedPhone(conv.phone); }}
                  className={`p-3 border-b border-gray-800 cursor-pointer hover:bg-gray-800/50 transition ${
                    selectedPhone === conv.phone
                      ? 'bg-[#00d563]/10 border-l-2 border-l-[#00d563]'
                      : ''
                  }`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-white text-sm font-medium flex-1 truncate">
                      {conv.name || conv.phone}
                    </span>
                    <div className="flex items-center gap-1">
                      {isMe && <span className="text-[10px] text-[#00d563]">Saya</span>}
                      {getStatusBadge(conv)}
                      {agent && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${STATUS_COLORS[agent.status] || STATUS_COLORS.offline} text-black font-medium`}>
                          {agent.name}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-gray-500">
                    <span>{formatTime(conv.last_contact)}</span>
                    <span>•</span>
                    <span>{conv.total_messages} mesej</span>
                    {conv.tags && <span className="text-blue-400">#{conv.tags.split(',')[0]}</span>}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Main Content - Chat / Placeholder */}
      <div className="flex-1 flex flex-col">
        {selectedPhone ? (
          <>
            <div className="bg-[#1a1d27] border-b border-gray-800 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-white font-medium">{selectedConv?.name || selectedPhone}</h3>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-gray-500 text-xs">{selectedPhone}</span>
                    {getStatusBadge(selectedConv || {})}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {/* Status Workflow Buttons */}
                  {selectedStatus === 'bot_active' && (
                    <button
                      onClick={() => handleStatusAction(selectedPhone, 'escalated')}
                      className="bg-red-600 hover:bg-red-700 text-white text-xs px-3 py-1.5 rounded-lg transition"
                    >
                      🚨 Escalate
                    </button>
                  )}
                  {selectedStatus === 'assigned' && (
                    <>
                      <button
                        onClick={() => handleStatusAction(selectedPhone, 'escalated')}
                        className="bg-red-600 hover:bg-red-700 text-white text-xs px-3 py-1.5 rounded-lg transition"
                      >
                        🚨 Escalate
                      </button>
                      <button
                        onClick={() => handleStatusAction(selectedPhone, 'resolved')}
                        className="bg-green-600 hover:bg-green-700 text-white text-xs px-3 py-1.5 rounded-lg transition"
                      >
                        ✅ Resolve
                      </button>
                    </>
                  )}
                  {selectedStatus === 'escalated' && (
                    <button
                      onClick={() => handleStatusAction(selectedPhone, 'resolved')}
                      className="bg-green-600 hover:bg-green-700 text-white text-xs px-3 py-1.5 rounded-lg transition"
                    >
                      ✅ Mark Resolved
                    </button>
                  )}
                  {selectedStatus === 'resolved' && (
                    <button
                      onClick={() => handleStatusAction(selectedPhone, 'bot_active')}
                      className="bg-blue-600 hover:bg-blue-700 text-white text-xs px-3 py-1.5 rounded-lg transition"
                    >
                      🔄 Re-open
                    </button>
                  )}
                  {/* Assign Dropdown */}
                  <select
                    onChange={(e) => handleAssign(selectedPhone, e.target.value ? parseInt(e.target.value) : null)}
                    defaultValue={selectedConv?.assigned_to || ''}
                    className="bg-[#0f1117] border border-gray-700 rounded-lg px-2 py-1 text-white text-xs focus:outline-none focus:border-[#00d563]"
                  >
                    <option value="">-- Assign to --</option>
                    <option value="">Unassign</option>
                    {staffList.map((s) => (
                      <option key={s.id} value={s.id}>{s.name} ({s.role})</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
            <div className="flex-1 flex items-center justify-center text-gray-500">
              <div className="text-center">
                <p className="text-lg mb-2">💬</p>
                <p>Chat panel akan loading sini</p>
                <p className="text-xs mt-1">Sprint v2.1.1: Status workflow aktif</p>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-500">
            <div className="text-center">
              <p className="text-4xl mb-3">📥</p>
              <p className="text-lg font-medium">Inbox Multi-Agent</p>
              <p className="text-sm mt-1">Pilih perbualan dari senarai di sebelah kiri</p>
              <p className="text-xs mt-3 text-gray-600">
                Filters: Semua / Saya / Belum Ditugaskan / Escalated / Resolved
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
