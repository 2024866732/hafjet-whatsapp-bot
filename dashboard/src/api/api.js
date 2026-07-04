/**
 * API helpers for HAFJET Dashboard
 * REST calls + WebSocket connection
 */

const API_BASE = import.meta.env.VITE_API_BASE || '';

export async function fetchCustomers() {
  const res = await fetch(`${API_BASE}/api/customers`);
  if (!res.ok) throw new Error('Failed to fetch customers');
  return res.json();
}

export async function fetchMessages(phone) {
  const res = await fetch(`${API_BASE}/api/messages/${encodeURIComponent(phone)}`);
  if (!res.ok) throw new Error('Failed to fetch messages');
  return res.json();
}

export async function fetchStats(date = "") {
  const url = date
    ? `${API_BASE}/api/stats?date=${encodeURIComponent(date)}`
    : `${API_BASE}/api/stats`;
  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to fetch stats');
  return res.json();
}

export async function resolveCustomer(phone) {
  const res = await fetch(`${API_BASE}/api/customers/${encodeURIComponent(phone)}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to resolve');
  return res.json();
}

export async function escalateCustomer(phone) {
  const res = await fetch(`${API_BASE}/api/customers/${encodeURIComponent(phone)}/escalate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to escalate');
  return res.json();
}

export async function updateNote(phone, note) {
  const res = await fetch(`${API_BASE}/api/customers/${encodeURIComponent(phone)}/note`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify({ note }),
  });
  if (!res.ok) throw new Error('Failed to update note');
  return res.json();
}

export async function handoffCustomer(phone, data = {}) {
  const res = await fetch(`${API_BASE}/api/customers/${encodeURIComponent(phone)}/handoff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed handoff');
  return res.json();
}

export async function staffReply(phone, message) {
  const res = await fetch(`${API_BASE}/api/customers/${encodeURIComponent(phone)}/reply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error('Failed to send reply');
  const result = await res.json();
  if (!result.sent) throw new Error('WhatsApp API rejected the message');
  return result;
}

// === BLAST / BROADCAST ===

export async function createBlast(message, recipients = "all", scheduleAt = null) {
  const res = await fetch(`${API_BASE}/api/blast`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify({ message, recipients, schedule_at: scheduleAt }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create blast');
  }
  return res.json();
}

export async function fetchBlastHistory(limit = 50) {
  const res = await fetch(`${API_BASE}/api/blast/history?limit=${limit}`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch blast history');
  return res.json();
}

// === CONTACTS ===

export async function fetchContacts({ tag = '', search = '', page = 1, limit = 20 } = {}) {
  const params = new URLSearchParams();
  if (tag) params.set('tag', tag);
  if (search) params.set('search', search);
  params.set('page', page);
  params.set('limit', limit);
  const res = await fetch(`${API_BASE}/api/contacts?${params}`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch contacts');
  return res.json();
}

export async function updateContact(phone, data) {
  const res = await fetch(`${API_BASE}/api/contacts/${encodeURIComponent(phone)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to update contact');
  return res.json();
}

export async function exportContacts() {
  const res = await fetch(`${API_BASE}/api/contacts/export`, {
    method: 'POST',
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to export contacts');
  return res.blob();
}

export async function importContacts(csvText) {
  const res = await fetch(`${API_BASE}/api/contacts/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify({ csv: csvText }),
  });
  if (!res.ok) throw new Error('Failed to import contacts');
  return res.json();
}

// === ANALYTICS ===

export async function fetchAnalyticsOverview() {
  const res = await fetch(`${API_BASE}/api/analytics/overview`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch analytics overview');
  return res.json();
}

export async function fetchAnalyticsChart(days = 7) {
  const res = await fetch(`${API_BASE}/api/analytics/chart?days=${encodeURIComponent(days)}`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch analytics chart');
  return res.json();
}

// === KEYWORDS ===

export async function fetchKeywords() {
  const res = await fetch(`${API_BASE}/api/keywords`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch keywords');
  return res.json();
}

export async function createKeyword(data) {
  const res = await fetch(`${API_BASE}/api/keywords`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to create keyword');
  return res.json();
}

export async function updateKeyword(id, data) {
  const res = await fetch(`${API_BASE}/api/keywords/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error('Failed to update keyword');
  return res.json();
}

export async function deleteKeyword(id) {
  const res = await fetch(`${API_BASE}/api/keywords/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to delete keyword');
  return res.json();
}

export async function testKeyword(message) {
  const res = await fetch(`${API_BASE}/api/keywords/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) throw new Error('Failed to test keyword');
  return res.json();
}

// === WEBSOCKET ===

export function connectWebSocket(onMessage, onConnect, onDisconnect) {
  const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProto}//${window.location.host}/ws`;
  
  let ws;
  let reconnectTimer = null;
  let intentionalClose = false;

  function connect() {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('🟢 WebSocket connected');
      if (onConnect) onConnect();
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (onMessage) onMessage(data);
      } catch (e) {
        console.error('WS parse error:', e);
      }
    };

    ws.onclose = () => {
      console.log('🔴 WebSocket disconnected');
      if (onDisconnect) onDisconnect();
      if (!intentionalClose) {
        reconnectTimer = setTimeout(connect, 3000);
      }
    };

    ws.onerror = (err) => {
      console.error('WS error:', err);
    };
  }

  connect();

  return {
    close: () => {
      intentionalClose = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    },
    getReadyState: () => ws?.readyState,
  };
}

// === STAFF AUTH ===

export async function staffLogin(email, password) {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || data.error || 'Login failed');
  return data;
}

export async function fetchStaffMe() {
  const res = await fetch(`${API_BASE}/api/auth/me`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch staff me');
  return res.json();
}

export async function fetchStaffList() {
  const res = await fetch(`${API_BASE}/api/staff`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch staff');
  return res.json();
}

export async function createStaff(data) {
  const res = await fetch(`${API_BASE}/api/staff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify(data),
  });
  const result = await res.json();
  if (!res.ok) throw new Error(result.detail || result.error || 'Failed to create staff');
  return result;
}

// === INBOX ===

export async function fetchInbox(filterType = 'all') {
  const res = await fetch(`${API_BASE}/api/inbox?filter=${encodeURIComponent(filterType)}`, {
    headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
  });
  if (!res.ok) throw new Error('Failed to fetch inbox');
  return res.json();
}

export async function assignConversation(phone, staffId) {
  const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(phone)}/assign`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify({ staff_id: staffId }),
  });
  const result = await res.json();
  if (!res.ok) throw new Error(result.detail || result.error || 'Failed to assign');
  return result;
}

export async function updateConversationStatus(phone, status) {
  const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(phone)}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-API-Key': import.meta.env.VITE_API_KEY || '' },
    body: JSON.stringify({ status }),
  });
  const result = await res.json();
  if (!res.ok) throw new Error(result.error || result.detail || 'Failed to update status');
  return result;
}
