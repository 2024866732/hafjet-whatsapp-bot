import { useEffect, useRef, useState, useCallback } from 'react';

/**
 * WebSocket hook with auto-reconnect.
 * Returns: { connected, lastMessage, sendMessage }
 */
export function useWebSocket() {
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState(null);
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const reconnectDelay = 3000;

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    try {
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setConnected(true);
        console.log('🟢 WebSocket connected');
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setLastMessage(data);
        } catch (e) {
          // ignore non-JSON (e.g., "pong")
        }
      };

      ws.onclose = () => {
        setConnected(false);
        console.log('🔴 WebSocket disconnected — reconnecting in', reconnectDelay / 1000, 's');
        reconnectTimer.current = setTimeout(connect, reconnectDelay);
      };

      ws.onerror = () => {
        ws.close();
      };

      wsRef.current = ws;
    } catch (e) {
      console.error('WebSocket connect failed:', e);
      reconnectTimer.current = setTimeout(connect, reconnectDelay);
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const sendMessage = useCallback((msg) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  return { connected, lastMessage, sendMessage };
}
