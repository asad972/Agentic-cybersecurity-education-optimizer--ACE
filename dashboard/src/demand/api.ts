import { useEffect, useState } from 'react';

// Requests go to /api, which the Vite dev server and the nginx image forward to the FastAPI service.
const API_BASE = '/api';

export type Kind = 'skill' | 'certification';

export type RankedItem = { rank: number; item: string; count: number };
export type Top10 = { country: string; kind: Kind; requested_date: string; date: string | null; items: RankedItem[] };
export type DemandMap = { kind: Kind; item: string; date: string; values: { country: string; count: number }[] };
export type DemandItems = { kind: Kind; date: string; items: { item: string; count: number }[] };
export type LatestDay = { date: string };

export type ErrorKind = 'unreachable' | 'database' | 'not-found' | 'other';

export class ApiError extends Error {
  constructor(readonly kind: ErrorKind, readonly status?: number) {
    super(kind);
  }
}

async function getJson<T>(path: string, params: Record<string, string>, signal: AbortSignal): Promise<T> {
  const query = new URLSearchParams(params).toString();
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}${query ? '?' + query : ''}`, { signal, headers: { Accept: 'application/json' } });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError('unreachable');
  }
  const isJson = response.headers.get('content-type')?.includes('application/json') ?? false;
  if (response.status === 503) throw new ApiError('database', 503);
  if (response.status === 404) throw new ApiError('not-found', 404);
  // 502/504 come from nginx when the API itself is down; the Vite dev proxy answers a plain-text 500 instead.
  if (response.status === 502 || response.status === 504 || (response.status >= 500 && !isJson)) {
    throw new ApiError('unreachable', response.status);
  }
  if (!response.ok) throw new ApiError('other', response.status);
  if (!isJson) throw new ApiError('unreachable', response.status);
  return response.json() as Promise<T>;
}

export const api = {
  latestDay: (signal: AbortSignal) => getJson<LatestDay>('/demand/latest-day', {}, signal),
  items: (kind: Kind, date: string, signal: AbortSignal) => getJson<DemandItems>('/demand/items', { kind, date }, signal),
  map: (kind: Kind, item: string, date: string, signal: AbortSignal) => getJson<DemandMap>('/demand/map', { kind, item, date }, signal),
  top10: (country: string, kind: Kind, date: string, signal: AbortSignal) => getJson<Top10>('/top10', { country, kind, date }, signal),
};

export type Load<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: ApiError };

/** Runs load whenever key changes; a null load means there is nothing to fetch yet. */
export function useApi<T>(load: ((signal: AbortSignal) => Promise<T>) | null, key: string): Load<T> {
  // The state remembers its key, so data loaded for an old key is never returned for a new one.
  const [state, setState] = useState<{ key: string; load: Load<T> }>({ key, load: { status: 'idle' } });
  const active = load !== null;
  useEffect(() => {
    if (!load) {
      setState({ key, load: { status: 'idle' } });
      return;
    }
    const controller = new AbortController();
    setState({ key, load: { status: 'loading' } });
    load(controller.signal).then(
      data => {
        // A cancelled request can still resolve; its data must not replace the current key's state.
        if (controller.signal.aborted) return;
        setState({ key, load: { status: 'ready', data } });
      },
      error => {
        if (controller.signal.aborted) return;
        setState({ key, load: { status: 'error', error: error instanceof ApiError ? error : new ApiError('other') } });
      },
    );
    return () => controller.abort();
    // The key lists everything load depends on, so load itself is not a dependency.
  }, [key, active]);
  if (state.key !== key) return active ? { status: 'loading' } : { status: 'idle' };
  return state.load;
}

const dayFormat = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const numberFormat = new Intl.NumberFormat('en-GB');

export function formatDay(iso: string) {
  return dayFormat.format(new Date(iso + 'T00:00:00Z'));
}

export function formatCount(value: number) {
  return numberFormat.format(value);
}

export function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

/** For text placed in ECharts tooltips, which are HTML. */
export function escapeHtml(text: string) {
  return text.replace(/[&<>"']/g, char => `&#${char.charCodeAt(0)};`);
}
