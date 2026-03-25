import { useEffect, useRef, useCallback } from "react";
import type { WsEvent } from "./types";

export type WsStatus = "connecting" | "connected" | "disconnected";

export function useWebSocket(onEvent: (event: WsEvent) => void) {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusRef = useRef<WsStatus>("disconnected");

  const clearReconnect = useCallback(() => {
    if (reconnectTimer.current !== null) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    clearReconnect();

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    statusRef.current = "connecting";

    ws.onopen = () => {
      statusRef.current = "connected";
    };

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
      statusRef.current = "disconnected";
      // Auto-reconnect after 2s
      reconnectTimer.current = setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };

    wsRef.current = ws;
  }, [clearReconnect]);

  useEffect(() => {
    connect();
    return () => {
      clearReconnect();
      wsRef.current?.close();
    };
  }, [connect, clearReconnect]);

  const send = useCallback((data: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }, []);

  return { wsRef, send, statusRef };
}
