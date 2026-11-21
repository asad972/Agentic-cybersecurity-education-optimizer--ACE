// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, useApi, type Load } from './api';

afterEach(() => { vi.unstubAllGlobals(); });

const signal = () => new AbortController().signal;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const text = (body: string, status: number) =>
  new Response(body, { status, headers: { 'content-type': 'text/plain' } });

async function errorKind(response: Response) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
  const error = await api.latestDay(signal()).catch(caught => caught);
  expect(error).toBeInstanceOf(ApiError);
  return (error as ApiError).kind;
}

describe('Demand API requests', () => {
  it('calls the API under /api with the query parameters', async () => {
    const fetch = vi.fn().mockResolvedValue(json({ country: 'DE', kind: 'skill', items: [] }));
    vi.stubGlobal('fetch', fetch);
    await api.top10('DE', 'skill', '2026-11-19', signal());
    expect(fetch.mock.calls[0][0]).toBe('/api/top10?country=DE&kind=skill&date=2026-11-19');
  });

  it('returns the JSON body of a successful answer', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ date: '2026-11-19' })));
    await expect(api.latestDay(signal())).resolves.toEqual({ date: '2026-11-19' });
  });

  it('reports a 503 as the database being unavailable', async () => {
    expect(await errorKind(json({ detail: 'Cassandra is unavailable' }, 503))).toBe('database');
  });

  it('reports a 404 as no data found', async () => {
    expect(await errorKind(json({ detail: 'No Demand data found' }, 404))).toBe('not-found');
  });

  it('reports 502 and 504 from nginx as the API not being reachable', async () => {
    expect(await errorKind(text('Bad gateway', 502))).toBe('unreachable');
    expect(await errorKind(text('Gateway timeout', 504))).toBe('unreachable');
  });

  it("reports the Vite proxy's plain-text 500 as the API not being reachable", async () => {
    expect(await errorKind(text('connect ECONNREFUSED', 500))).toBe('unreachable');
  });

  it('reports a JSON 500 from the API itself as another error', async () => {
    expect(await errorKind(json({ detail: 'Internal Server Error' }, 500))).toBe('other');
  });

  it('reports a successful answer that is not JSON, such as an HTML page, as not reachable', async () => {
    expect(await errorKind(new Response('<!doctype html>', { status: 200, headers: { 'content-type': 'text/html' } }))).toBe('unreachable');
  });

  it('reports a failed request as not reachable, but passes a cancellation through', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(api.latestDay(signal())).rejects.toMatchObject({ kind: 'unreachable' });

    const controller = new AbortController();
    const abort = new DOMException('The operation was aborted.', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => { controller.abort(); return Promise.reject(abort); }));
    await expect(api.latestDay(controller.signal)).rejects.toBe(abort);
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

type Props = { load: ((signal: AbortSignal) => Promise<string>) | null; key: string };

describe('useApi', () => {
  it('is idle while there is nothing to load', () => {
    const { result } = renderHook(({ load, key }: Props) => useApi(load, key), { initialProps: { load: null, key: 'none' } });
    expect(result.current).toEqual({ status: 'idle' });
  });

  it('never returns data loaded for an old key', async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const { result, rerender } = renderHook(({ load, key }: Props) => useApi(load, key),
      { initialProps: { load: () => first.promise, key: 'DE' } as Props });
    expect(result.current).toEqual({ status: 'loading' });

    rerender({ load: () => second.promise, key: 'FR' });
    // The answer for DE arrives after FR was chosen: it must not be shown for FR.
    await act(async () => { first.resolve('data for DE'); });
    expect(result.current).toEqual({ status: 'loading' });

    await act(async () => { second.resolve('data for FR'); });
    expect(result.current).toEqual<Load<string>>({ status: 'ready', data: 'data for FR' });
  });

  it('cancels the request for the old key', () => {
    let seen: AbortSignal | undefined;
    const { rerender } = renderHook(({ load, key }: Props) => useApi(load, key),
      { initialProps: { load: (s: AbortSignal) => { seen = s; return new Promise<string>(() => {}); }, key: 'DE' } as Props });
    rerender({ load: () => new Promise<string>(() => {}), key: 'FR' });
    expect(seen?.aborted).toBe(true);
  });

  it('turns an unexpected error into an ApiError', async () => {
    const { result } = renderHook(({ load, key }: Props) => useApi(load, key),
      { initialProps: { load: () => Promise.reject(new Error('boom')), key: 'DE' } as Props });
    await waitFor(() => expect(result.current.status).toBe('error'));
    const state = result.current as Extract<Load<string>, { status: 'error' }>;
    expect(state.error).toBeInstanceOf(ApiError);
    expect(state.error.kind).toBe('other');
  });
});
