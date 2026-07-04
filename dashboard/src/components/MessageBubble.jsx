/**
 * MessageBubble — Single chat message display
 */
import StatusBadge from './StatusBadge';
import { formatTime } from '../utils/format';

export default function MessageBubble({ message }) {
  const isOutbound = message.direction === 'outbound' || message.source === 'bot' || message.source === 'staff';

  return (
    <div className={`flex ${isOutbound ? 'justify-end' : 'justify-start'} mb-2`}>
      <div
        className={`max-w-[75%] rounded-2xl px-3.5 py-2 shadow-sm ${
          isOutbound
            ? 'bg-emerald-600 text-white rounded-br-md'
            : 'bg-gray-700 text-gray-100 rounded-bl-md'
        }`}
      >
        {/* Source label */}
        <div className={`flex items-center gap-1.5 mb-0.5 ${isOutbound ? 'justify-end' : 'justify-start'}`}>
          <StatusBadge source={message.source} />
        </div>

        {/* Message text */}
        <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
          {message.message}
        </p>

        {/* Timestamp */}
        <div className={`flex items-center gap-1 mt-1 ${isOutbound ? 'justify-end' : 'justify-start'}`}>
          <span className={`text-[10px] ${isOutbound ? 'text-emerald-200' : 'text-gray-400'}`}>
            {formatTime(message.timestamp)}
          </span>
          {isOutbound && message.status === 'sent' && (
            <span className="text-[10px] text-emerald-200">✓✓</span>
          )}
        </div>
      </div>
    </div>
  );
}
