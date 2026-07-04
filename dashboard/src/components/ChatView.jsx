/**
 * ChatView — Panel tengah: active conversation
 */
import { useState, useEffect, useRef } from 'react';
import { fetchMessages } from '../api/api';
import MessageBubble from './MessageBubble';
import { formatPhone, formatDate } from '../utils/format';

export default function ChatView({ phone, wsConnected }) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const bottomRef = useRef(null);
  const prevPhoneRef = useRef(null);

  const loadMessages = async (targetPhone) => {
    if (!targetPhone) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchMessages(targetPhone);
      setMessages(data);
    } catch (err) {
      setError('Gagal muat mesej');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (phone) {
      loadMessages(phone);
      prevPhoneRef.current = phone;
    } else {
      setMessages([]);
    }
  }, [phone]);

  // Auto-scroll to bottom
  useEffect(() => {
    if (messages.length > 0) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // WebSocket: append new message if it belongs to active chat
  useEffect(() => {
    if (!phone || !wsConnected) return;

    const handleWsMessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        // Support multiple event format (backward compat)
        const isNewMsg = data.event === 'inbound_message' || data.event === 'outbound_message';
        if (isNewMsg && data.data) {
          const msg = data.data;
          // Only append if it's from the active conversation
          if (msg.phone === phone) {
            setMessages((prev) => {
              // Dedup by wamid or id
              if (prev.some((m) => m.wamid === msg.wamid || m.id === msg.id)) return prev;
              // Ensure message & source aliases for StatusBadge
              msg.message = msg.message || msg.content || '';
              msg.source = msg.source || msg.direction || 'bot';
              return [...prev, msg];
            });
          }
        }
      } catch (e) {
        // ignore parse errors
      }
    };

    // Use native WebSocket for this component
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProto}//${window.location.host}/ws`);
    ws.onmessage = handleWsMessage;

    return () => ws.close();
  }, [phone, wsConnected]);

  // No customer selected — empty state
  if (!phone) {
    return (
      <div className="flex-1 flex items-center justify-center bg-gray-850 bg-gradient-to-br from-gray-800 to-gray-900">
        <div className="text-center px-8">
          <div className="w-20 h-20 bg-gray-700/50 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-10 h-10 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
          </div>
          <h3 className="text-gray-400 text-lg font-medium mb-2">Pilih perbualan</h3>
          <p className="text-gray-500 text-sm">
            Pilih pelanggan dari senarai di kiri untuk mula chat
          </p>
        </div>
      </div>
    );
  }

  // Get customer name from first message
  const customerName = messages.find((m) => m.from_name)?.name || formatPhone(phone);

  return (
    <div className="flex-1 flex flex-col bg-gray-800 h-full">
      {/* Chat Header */}
      <div className="px-4 py-3 border-b border-gray-700 bg-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 bg-emerald-600/20 rounded-full flex items-center justify-center">
            <span className="text-emerald-400 text-sm font-medium">
              {customerName[0]?.toUpperCase() || '?'}
            </span>
          </div>
          <div>
            <h2 className="text-gray-200 font-medium text-sm">{customerName}</h2>
            <p className="text-gray-500 text-xs">{formatPhone(phone)}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {wsConnected && (
            <span className="text-[10px] text-emerald-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
              Realtime
            </span>
          )}
        </div>
      </div>

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 bg-gradient-to-b from-gray-800 to-gray-850">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-6 h-6 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : error ? (
          <div className="text-center py-12">
            <p className="text-red-400 text-sm">{error}</p>
            <button
              onClick={() => loadMessages(phone)}
              className="mt-2 text-xs text-emerald-400 hover:underline"
            >
              Cuba lagi
            </button>
          </div>
        ) : messages.length === 0 ? (
          <div className="text-center py-12">
            <p className="text-gray-500 text-sm">Tiada mesej lagi untuk pelanggan ini</p>
          </div>
        ) : (
          <>
            {/* Date separator */}
            {messages.length > 0 && (
              <div className="flex items-center justify-center mb-4">
                <span className="bg-gray-700 text-gray-400 text-[10px] px-2 py-1 rounded-full">
                  {formatDate(messages[0].timestamp)}
                </span>
              </div>
            )}

            {messages.map((msg, idx) => (
              <MessageBubble key={msg.id || idx} message={msg} />
            ))}
            <div ref={bottomRef} />
          </>
        )}
      </div>

      {/* Bottom info bar */}
      <div className="px-4 py-2 border-t border-gray-700 bg-gray-800">
        <p className="text-gray-500 text-[10px] text-center">
          {messages.length} mesej • Auto-scroll aktif
        </p>
      </div>
    </div>
  );
}
