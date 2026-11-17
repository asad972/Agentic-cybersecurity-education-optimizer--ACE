import { useEffect, useState } from 'react';
import { addAlert, liveURL, openLiveFeed } from '../services/api';
import type { FeedStatus, LiveAlert } from '../services/api';

/** Alerts raised since the page was opened, latest first, with the state of the feed. */
export function useLiveAlerts() {
  const [alerts, setAlerts] = useState<LiveAlert[]>([]);
  // Set once the API reports that this page fell behind and missed alerts; it stays until the page is reloaded.
  const [skipped, setSkipped] = useState(false);
  const [status, setStatus] = useState<FeedStatus>({ state: 'connecting', message: 'Connecting to the alert feed.' });
  useEffect(() => openLiveFeed(liveURL(), window.location.origin, {
    alert: alert => setAlerts(previous => addAlert(previous, alert)),
    status: setStatus,
    skipped: () => setSkipped(true),
  }), []);
  return { alerts, skipped, ...status };
}
