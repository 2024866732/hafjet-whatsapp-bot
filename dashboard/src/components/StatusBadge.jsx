/**
 * StatusBadge — Label for message source
 */
export default function StatusBadge({ source }) {
  const label = source?.toUpperCase() || 'SYSTEM';

  const colors = {
    BOT: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    STAFF: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    USER: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    SYSTEM: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  };

  const colorClass = colors[label] || colors.SYSTEM;

  return (
    <span className={`inline-block px-1.5 py-0.5 text-[10px] font-semibold rounded border ${colorClass}`}>
      {label}
    </span>
  );
}
