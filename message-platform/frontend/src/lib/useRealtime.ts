"use client";

import { useCallback, useEffect, useRef } from "react";
import { wsUrl } from "./api";
import type { ConnectionState, ServerEvent } from "./types";

const HEARTBEAT_MS = 20_000;
const STALE_MS = 45_000;
const MAX_BACKOFF_MS = 15_000;

/**
 * Keeps one WebSocket open for the current demo user.
 * - Reconnects with exponential backoff (1s, 2s, 4s ... 15s) after any drop.
 * - Sends a heartbeat ping; a socket that stops answering is recycled.
 * - Calls onReconnect after a reconnection so callers can resync missed data over REST.
 */
export function useRealtime(
  userId: string | null,
  onEvent: (event: ServerEvent) => void,
  onState: (state: ConnectionState) => void,
  onReconnect: () => void,
) {
  const socketRef = useRef<WebSocket | null>(null);
  const handlers = useRef({ onEvent, onState, onReconnect });
  handlers.current = { onEvent, onState, onReconnect };

  useEffect(() => {
    if (!userId) return;
    let disposed = false;
    let attempt = 0;
    let everOpened = false;
    let retryTimer: number | undefined;
    let heartbeat: number | undefined;
    let lastSeen = Date.now();

    const connect = () => {
      if (disposed) return;
      handlers.current.onState(everOpened ? "reconnecting" : "connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl(userId));
      } catch {
        schedule();
        return;
      }
      socketRef.current = ws;

      ws.onopen = () => {
        const wasReconnect = everOpened;
        attempt = 0;
        everOpened = true;
        lastSeen = Date.now();
        handlers.current.onState("open");
        if (wasReconnect) handlers.current.onReconnect();
        window.clearInterval(heartbeat);
        heartbeat = window.setInterval(() => {
          if (Date.now() - lastSeen > STALE_MS) {
            ws.close();
            return;
          }
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping", ts: Date.now() }));
        }, HEARTBEAT_MS);
      };
      ws.onmessage = (msg) => {
        lastSeen = Date.now();
        try {
          handlers.current.onEvent(JSON.parse(msg.data as string) as ServerEvent);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        window.clearInterval(heartbeat);
        if (socketRef.current === ws) socketRef.current = null;
        schedule();
      };
      ws.onerror = () => ws.close();
    };

    const schedule = () => {
      if (disposed) return;
      handlers.current.onState(everOpened ? "reconnecting" : "connecting");
      const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** attempt) + Math.random() * 300;
      attempt += 1;
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connect, delay);
    };

    const onOnline = () => {
      if (!socketRef.current) {
        attempt = 0;
        window.clearTimeout(retryTimer);
        connect();
      }
    };
    window.addEventListener("online", onOnline);
    connect();

    return () => {
      disposed = true;
      window.removeEventListener("online", onOnline);
      window.clearTimeout(retryTimer);
      window.clearInterval(heartbeat);
      const ws = socketRef.current;
      socketRef.current = null;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [userId]);

  return useCallback((event: Record<string, unknown>) => {
    const ws = socketRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(event));
  }, []);
}
