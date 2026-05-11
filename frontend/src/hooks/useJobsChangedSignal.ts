import { useEffect, useRef } from "react";
import { useApi } from "@/contexts/ApiContext";

/**
 * Subscribe to backend job-state events on the shared /ws/joint-data
 * channel. The backend pushes `{type: "jobs_changed"}` on submit /
 * watchdog finalisation / delete, so we can refetch on-event instead
 * of polling.
 *
 * The callback ref is captured so its identity changing doesn't tear
 * down the socket. Auto-reconnects with a 3s delay if the server bounces.
 */
export const useJobsChangedSignal = (onChange: () => void) => {
  const { wsBaseUrl } = useApi();
  const cbRef = useRef(onChange);
  cbRef.current = onChange;

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      try {
        ws = new WebSocket(`${wsBaseUrl}/ws/joint-data`);
      } catch {
        reconnectTimer = setTimeout(connect, 3000);
        return;
      }
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data?.type === "jobs_changed") cbRef.current();
        } catch {
          /* ignore non-JSON or unexpected payloads */
        }
      };
      ws.onclose = () => {
        if (cancelled) return;
        reconnectTimer = setTimeout(connect, 3000);
      };
    };
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [wsBaseUrl]);
};
