/**
 * Sidebar — Panel kiri: conversation list
 */
import { useState, useEffect } from 'react';
import { fetchCustomers } from '../api/api';
import { timeAgo, formatPhone } from '../utils/format';

export default function Sidebar({ selectedPhone, onSelect, wsConnected }) {
  const [customers, setCustomers] = useState([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadCustomers = async () => {
    try {
      const data = await fetchCustomers();
      setCustomers(data);
      setError(null);
    } catch (err) {
      setError('Gagal muat data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCustomers();
  }, []);

  // Filter by search
  const filtered = customers.filter((c) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      c.phone?.toLowerCase().includes(q) ||
      c.name?.toLowerCase().includes(q) ||
      c.last_message?.toLowerCase().includes(q)
    );
  });

  return (
    <div className="w-[280px] min-w-[280px] bg-gray-900 border-r border-gray-700 flex flex-col h-full">
      {/* Header */}
      <div className="p-4 border-b border-gray-700">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 bg-emerald-600 rounded-lg flex items-center justify-center">
              <span className="text-white font-bold text-sm">H</span>
            </div>
            <h1 className="text-white font-bold text-lg">HAFJET Bot</h1>
          </div>
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full ${
                wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'
              }`}
            />
            <span className="text-[10px] text-gray-400">
              {wsConnected ? 'Live' : 'Offline'}
            </span>
          </div>
        </div>

        {/* Search */}
        <div className="relative">
          <svg
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
          <input
            type="text"
            placeholder="Cari perbualan..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-gray-800 text-gray-200 text-sm rounded-lg pl-9 pr-3 py-2 border border-gray-700 focus:border-emerald-500 focus:outline-none placeholder-gray-500"
          />
        </div>
      </div>

      {/* Conversation List */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : error ? (
          <div className="text-center py-12 px-4">
            <p className="text-red-400 text-sm">{error}</p>
            <button
              onClick={loadCustomers}
              className="mt-2 text-xs text-emerald-400 hover:underline"
            >
              Cuba lagi
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12 px-4">
            <svg
              className="w-12 h-12 mx-auto text-gray-600 mb-3"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
              />
            </svg>
            <p className="text-gray-500 text-sm">
              {search ? 'Tiada hasil' : 'Tiada perbualan lagi'}
            </p>
          </div>
        ) : (
          filtered.map((customer) => (
            <button
              key={customer.phone}
              onClick={() => onSelect(customer.phone)}
              className={`w-full text-left px-4 py-3 border-b border-gray-800 hover:bg-gray-800 transition-colors ${
                selectedPhone === customer.phone ? 'bg-gray-800 border-l-2 border-l-emerald-500' : ''
              }`}
            >
              <div className="flex items-start gap-3">
                {/* Avatar */}
                <div className="w-10 h-10 bg-gray-700 rounded-full flex items-center justify-center flex-shrink-0">
                  <span className="text-gray-300 text-sm font-medium">
                    {customer.name ? customer.name[0].toUpperCase() : customer.phone?.slice(-2) || '?'}
                  </span>
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="text-gray-200 text-sm font-medium truncate">
                        {customer.name || formatPhone(customer.phone)}
                      </span>
                      {customer.status === 'escalated' && (
                        <span className="flex-shrink-0 text-[9px] bg-amber-500/20 text-amber-400 border border-amber-500/30 px-1 py-0 rounded font-bold">ESCALATED</span>
                      )}
                      {customer.status === 'handoff' && (
                        <span className="flex-shrink-0 text-[9px] bg-blue-500/20 text-blue-400 border border-blue-500/30 px-1 py-0 rounded font-bold">HANDOFF</span>
                      )}
                      {customer.status === 'resolved' && (
                        <span className="flex-shrink-0 text-[9px] bg-gray-500/20 text-gray-400 border border-gray-500/30 px-1 py-0 rounded font-bold">RESOLVED</span>
                      )}
                    </div>
                    <span className="text-gray-500 text-[10px] flex-shrink-0 ml-2">
                      {timeAgo(customer.last_timestamp)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between mt-0.5">
                    <p className="text-gray-400 text-xs truncate">
                      {customer.last_message || 'Tiada mesej'}
                    </p>
                    {customer.unread_count > 0 && (
                      <span className="bg-emerald-500 text-white text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center flex-shrink-0 ml-2">
                        {customer.unread_count}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </button>
          ))
        )}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-gray-700">
        <p className="text-gray-500 text-[10px] text-center">
          {filtered.length} perbualan
        </p>
      </div>
    </div>
  );
}
