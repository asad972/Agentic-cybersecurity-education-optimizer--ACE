// Types and requests for the M4 Europe map endpoints (GET /map, /map/meta, /map/country).
export type Country = { code: string; name: string };
export type MapMeta = { countries: Country[]; metrics: { id: string; label: string; needs_item: boolean }[]; date_min: string; date_max: string };
export type MapData = { metric: string; date: string; item: string | null; unit: string; period: string; note: string; data: { country: string; value: number | null }[] };
export type Ranked = { rank: number; item: string; count: number };
export type CountryAlert = { created_at: string; alert_type: string; subject: string; score: number; details: string };
export type CountryData = {
  country: string;
  date: string;
  top_skills: Ranked[];
  top_certifications: Ranked[];
  workforce: { year: number; workforce_size: number | null; workforce_gap: number | null; source: string | null } | null;
  alerts: CountryAlert[];
  alerts_truncated: boolean;
};

// Same default as the other pages: a configured API URL in deployments, the local API otherwise.
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export function endpoint(path: string, params: Record<string, string> = {}) {
  const query = new URLSearchParams(params).toString();
  return `${API_BASE}${path}${query ? `?${query}` : ''}`;
}

/** FastAPI sends `detail` as text for our own errors and as a list for validation errors. */
function detailOf(body: unknown) {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && typeof detail[0]?.msg === 'string') return detail[0].msg as string;
  return undefined;
}

export async function getJSON<T>(path: string, params: Record<string, string>, signal: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(endpoint(path, params), { signal, headers: { Accept: 'application/json' } });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new Error(`The ACE API could not be reached at ${API_BASE}. Start it as described in api/README.md.`);
  }
  if (!response.ok) {
    let detail: string | undefined;
    try { detail = detailOf(await response.json()); } catch { /* Not JSON: fall back to the status. */ }
    throw new Error(detail ?? `API request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

// ---- Live alert feed (WS /live) ----
export type LiveAlert = CountryAlert & { country: string; day: string };
export type LiveState = 'connecting' | 'connected' | 'unavailable' | 'disabled';
export type LiveMessage = { type: 'status'; state: LiveState; message: string } | { type: 'alert'; alert: LiveAlert };
export const ALERT_LABELS: Record<string, string> = { drift: 'Demand drift', threat_burst: 'Threat burst' };
const LIVE_STATES = ['connecting', 'connected', 'unavailable', 'disabled'];

/** The feed's ws:// or wss:// address. A relative API base is resolved against the page. */
export function liveURL(page: string = window.location.href) {
  const url = new URL(`${API_BASE}/live`, page);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

/** One feed message, or null for anything that is not a status or a complete alert. */
export function parseLiveMessage(data: unknown): LiveMessage | null {
  let message: unknown;
  try { message = JSON.parse(String(data)); } catch { return null; }
  if (typeof message !== 'object' || message === null) return null;
  const { type, state, message: text, alert } = message as Record<string, unknown>;
  if (type === 'status' && typeof state === 'string' && LIVE_STATES.includes(state)) {
    return { type, state: state as LiveState, message: typeof text === 'string' ? text : '' };
  }
  if (type !== 'alert' || typeof alert !== 'object' || alert === null) return null;
  const { country, day, created_at, alert_type, subject, score, details } = alert as Record<string, unknown>;
  if ([country, day, created_at, alert_type, subject].some(value => typeof value !== 'string' || !value)) return null;
  if (typeof score !== 'number' || !Number.isFinite(score)) return null;
  return { type, alert: { country, day, created_at, alert_type, subject, score, details: typeof details === 'string' ? details : '' } as LiveAlert };
}

export function alertKey(alert: LiveAlert) {
  return [alert.country, alert.created_at, alert.alert_type, alert.subject].join('|');
}

/** Latest arrival first, without repeats, capped so a long-open page does not grow without bound. */
export function addAlert(existing: LiveAlert[], alert: LiveAlert, limit = 50) {
  const key = alertKey(alert);
  return [alert, ...existing.filter(item => alertKey(item) !== key)].slice(0, limit);
}
