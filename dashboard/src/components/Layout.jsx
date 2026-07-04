/**
 * Layout — Main app shell: icon nav sidebar + content area
 * Reply.la inspired: collapsed icon sidebar, expand on hover
 */
import { useState, useEffect } from 'react';
import { fetchStats } from '../api/api';
import { connectWebSocket } from '../api/api';
import Sidebar from './Sidebar';
import ChatView from './ChatView';
import CustomerInfo from './CustomerInfo';
import Analytics from './Analytics';
import Settings from './Settings';
import Blast from './Blast';
import Contacts from './Contacts';
import Keywords from './Keywords';
import Inbox from './Inbox';

const NAV_ITEMS = [
  {
    id: 'inbox',
    label: 'Inbox',
    icon: (active) => (
      <svg className="w-5 h-5" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
      </svg>
    ),
  },
  {
    id: 'chats',
    label: 'Chats',
    icon: (active) => (
      <svg className="w-5 h-5" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    id: 'contacts',
    label: 'Contacts',
    icon: (active) => (
      <svg className="w-5 h-5" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
      </svg>
    ),
  },
  {
    id: 'analytics',
    label: 'Analytics',
    icon: (active) => (
      <svg className="w-5 h-5" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    ),
  },
  {
    id: 'blast',
    label: 'Blast',
    icon: (active) => (
      <svg className="w-5 h-5" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" />
      </svg>
    ),
  },
  {
    id: 'keywords',
    label: 'Keywords',
    icon: (active) => (
      <svg className="w-5 h-5" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
      </svg>
    ),
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: (active) => (
      <svg className="w-5 h-5" fill={active ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-1.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={active ? 0 : 2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    ),
  },
];

export default function Layout({ onLogout }) {
  const [activeTab, setActiveTab] = useState('inbox');
  const [selectedPhone, setSelectedPhone] = useState(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [todayInbound, setTodayInbound] = useState(0);
  const [navExpanded, setNavExpanded] = useState(false);

  // WebSocket connection
  useEffect(() => {
    const ws = connectWebSocket(
      (data) => {
        if (data.type === 'new_message') {
          fetchStats()
            .then((s) => setTodayInbound(s.today_inbound || 0))
            .catch(() => {});
        }
      },
      () => setWsConnected(true),
      () => setWsConnected(false)
    );
    return () => ws.close();
  }, []);

  // Load today count on mount
  useEffect(() => {
    fetchStats()
      .then((s) => setTodayInbound(s.today_inbound || 0))
      .catch(() => {});
  }, []);

  // Background #0f1117 , card #1a1d27 , accent #00d563
  return (
    <div className="h-screen flex bg-[#0f1117] text-gray-200 overflow-hidden">
      {/* Navigation Sidebar — icon-only collapsed, expand on hover */}
      <nav
        className="bg-[#1a1d27] border-r border-gray-800 flex flex-col items-center py-3 flex-shrink-0 transition-all duration-200 relative group"
        style={{ width: navExpanded ? '180px' : '56px' }}
        onMouseEnter={() => setNavExpanded(true)}
        onMouseLeave={() => setNavExpanded(false)}
      >
        {/* Logo */}
        <div className="w-9 h-9 bg-[#00d563] rounded-lg flex items-center justify-center mb-4 flex-shrink-0">
          <span className="text-white font-bold text-sm">H</span>
        </div>

        {/* Nav Items */}
        <div className="flex flex-col gap-1 w-full px-2">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`flex items-center gap-3 px-2.5 py-2.5 rounded-lg transition-all duration-150 w-full ${
                activeTab === item.id
                  ? 'bg-[#00d563]/10 text-[#00d563]'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
              }`}
              title={item.label}
            >
              <span className="flex-shrink-0">{item.icon(activeTab === item.id)}</span>
              <span
                className={`text-xs font-medium transition-opacity duration-200 ${
                  navExpanded ? 'opacity-100' : 'opacity-0 w-0 overflow-hidden'
                }`}
              >
                {item.label}
              </span>
            </button>
          ))}
        </div>

        {/* Bottom status */}
        <div className="mt-auto flex flex-col items-center gap-1 px-2 w-full">
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-[#00d563] animate-pulse' : 'bg-red-400'}`} />
            <span
              className={`text-[10px] font-medium transition-opacity duration-200 ${
                navExpanded ? 'opacity-100' : 'opacity-0 w-0 overflow-hidden'
              }`}
            >
              {wsConnected ? 'Live' : 'Offline'}
            </span>
          </div>
          <span
            className={`text-[10px] text-gray-500 transition-opacity duration-200 ${
              navExpanded ? 'opacity-100' : 'opacity-0 w-0 overflow-hidden'
            }`}
          >
            {todayInbound} hari ini
          </span>
        </div>
      </nav>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Bar (only for non-chats pages) */}
        {activeTab === 'chats' ? null : (
          <div className="bg-[#1a1d27] border-b border-gray-800 px-6 py-3 flex items-center justify-between flex-shrink-0">
            <h2 className="text-white font-semibold text-sm">
              {NAV_ITEMS.find((i) => i.id === activeTab)?.label || activeTab}
            </h2>
            <div className="flex items-center gap-3 text-xs text-gray-400">
              <span>{todayInbound} mesej hari ini</span>
              <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-[#00d563] animate-pulse' : 'bg-red-400'}`} />
            </div>
          </div>
        )}

        {/* Page Content */}
        <div className="flex-1 flex overflow-hidden">
          {activeTab === 'inbox' ? (
            <Inbox onLogout={onLogout} />
          ) : activeTab === 'chats' ? (
            <>
              <Sidebar
                selectedPhone={selectedPhone}
                onSelect={setSelectedPhone}
                wsConnected={wsConnected}
              />
              <ChatView phone={selectedPhone} wsConnected={wsConnected} />
              <CustomerInfo phone={selectedPhone} onCustomerUpdate={() => {}} />
            </>
          ) : activeTab === 'blast' ? (
            <Blast />
          ) : activeTab === 'keywords' ? (
            <Keywords />
          ) : activeTab === 'contacts' ? (
            <Contacts />
          ) : activeTab === 'settings' ? (
            <Settings />
          ) : (
            <Analytics />
          )}
        </div>
      </div>
    </div>
  );
}
