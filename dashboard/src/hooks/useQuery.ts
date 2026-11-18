import { useEffect, useState } from 'react';
import { getJSON } from '../services/api';
export function useQuery<T>(path: string | null, params: Record<string, string> = {}) {
  const key = JSON.stringify(params);
  const [result, setResult] = useState<{ data?: T; error?: string; loading: boolean; key?: string }>({ loading: true });
  const [revision, setRevision] = useState(0);
  const requestKey = `${path}:${key}:${revision}`;
  useEffect(() => {
    if (!path) { setResult({ loading: false, key: requestKey }); return; }
    const controller = new AbortController();
    setResult({ loading: true, key: requestKey });
    getJSON<T>(path, JSON.parse(key), controller.signal)
      .then(data => { if (!controller.signal.aborted) setResult({ data, loading: false, key: requestKey }); })
      .catch(error => { if (!controller.signal.aborted) setResult({ error: error instanceof Error ? error.message : 'Request failed', loading: false, key: requestKey }); });
    return () => controller.abort();
  }, [path, key, revision, requestKey]);
  // Avoid a single render of old-country/old-date data before the effect runs.
  return { ...(result.key === requestKey ? result : { loading: Boolean(path) }), retry: () => setRevision(value => value + 1) };
}
