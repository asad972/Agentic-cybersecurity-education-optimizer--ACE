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
