import { useEffect, useState } from 'react';
import { addAlert, liveURL, parseLiveMessage } from '../services/api';
import type { LiveAlert, LiveState } from '../services/api';

// 'offline' is the browser's own state: the WebSocket to the API is closed.
export type FeedState = LiveState | 'offline';

/** Alerts raised since the page was opened, latest first, with the state of the feed. */
export function useLiveAlerts() {
  const [alerts, setAlerts] = useState<LiveAlert[]>([]);
  const [status, setStatus] = useState<{ state: FeedState; message: string }>({ state: 'connecting', message: 'Connecting to the alert feed.' });
  useEffect(() => {
    let active = true;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let disabled = false;
    const connect = () => {
      if (!active) return;
      socket = new WebSocket(liveURL());
      socket.onmessage = event => {
        const message = parseLiveMessage(event.data);
        if (!active || !message) return;
        if (message.type === 'alert') { setAlerts(previous => addAlert(previous, message.alert)); return; }
        attempt = 0;
        disabled = message.state === 'disabled';
        setStatus({ state: message.state, message: message.message });
      };
      socket.onclose = () => {
        // A switched-off feed closes on purpose; anything else is retried with a growing delay (1 s to 30 s).
        if (!active || disabled) return;
        setStatus({ state: 'offline', message: 'The connection to the ACE API was lost; reconnecting.' });
        timer = setTimeout(connect, Math.min(30_000, 1000 * 2 ** attempt++));
      };
    };
    connect();
    return () => { active = false; clearTimeout(timer); socket?.close(); };
  }, []);
  return { alerts, ...status };
}
