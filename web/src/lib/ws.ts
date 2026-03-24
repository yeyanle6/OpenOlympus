import { useEffect, useRef, useCallback } from "react";
import type { WsEvent } from "./types";

export function useWebSocket(onEvent: (event: WsEvent) => void) {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  const wsRef = useRef<WebSocket | null>(null);

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(url);

    ws.onmessage = (e) => {
      try {
        const event: WsEvent = JSON.parse(e.data);
        handlerRef.current(event);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      // Auto-reconnect after 2s
      setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, []);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }, []);

  return { wsRef, send };
}
