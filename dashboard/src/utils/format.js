/**
 * Format helpers for timestamps and display
 */

export function timeAgo(isoString) {
  if (!isoString) return '';
  const now = new Date();
  const date = new Date(isoString);
  const diffMs = now - date;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 60) return 'Baru saja';
  if (diffMin < 60) return `${diffMin} minit lalu`;
  if (diffHour < 24) return `${diffHour} jam lalu`;
  if (diffDay < 7) return `${diffDay} hari lalu`;
  
  // Show actual date if older than 7 days
  return date.toLocaleDateString('ms-MY', {
    day: 'numeric',
    month: 'short',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  });
}

export function formatTime(isoString) {
  if (!isoString) return '';
  const date = new Date(isoString);
  return date.toLocaleTimeString('ms-MY', {
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatDate(isoString) {
  if (!isoString) return '';
  const date = new Date(isoString);
  return date.toLocaleDateString('ms-MY', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });
}

export function formatPhone(phone) {
  if (!phone) return '';
  // Format: 601112345678 → +60 11-123 45678
  const cleaned = phone.replace(/\D/g, '');
  if (cleaned.startsWith('60') && cleaned.length >= 10) {
    return `+${cleaned.slice(0, 2)} ${cleaned.slice(2, 4)}-${cleaned.slice(4, 7)} ${cleaned.slice(7)}`;
  }
  return phone;
}

export function getSourceLabel(source) {
  if (!source) return 'SYSTEM';
  const map = {
    bot: 'BOT',
    staff: 'STAFF',
    user: 'USER',
    system: 'SYSTEM',
    menu: 'BOT',
    ai: 'BOT',
    fallback: 'BOT',
  };
  return map[source.toLowerCase()] || source.toUpperCase();
}
