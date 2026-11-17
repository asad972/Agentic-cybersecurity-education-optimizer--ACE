import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_BASE, addAlert, endpoint, getJSON, liveURL, openLiveFeed, parseLiveMessage } from './api';
import type { FeedStatus, LiveAlert } from './api';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const signal = () => new AbortController().signal;

describe('API requests', () => {
  it('encodes skill names safely in query parameters', () => {
    expect(endpoint('/map', { metric: 'demand', item: 'Cloud security & IAM' })).toBe(`${API_BASE}/map?metric=demand&item=Cloud+security+%26+IAM`);
    expect(endpoint('/map/meta')).toBe(`${API_BASE}/map/meta`);
  });
  it('shows the message the API sent and forwards the cancellation signal', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Map storage is unavailable' }), { status: 503 }));
    vi.stubGlobal('fetch', fetch);
    const cancel = signal();
    await expect(getJSON('/map', {}, cancel)).rejects.toThrow('Map storage is unavailable');
    expect(fetch.mock.calls[0][1].signal).toBe(cancel);
  });
  it('reads FastAPI validation errors, which arrive as a list', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ loc: ['query', 'date'], msg: 'Field required' }] }), { status: 422 })));
    await expect(getJSON('/map', {}, signal())).rejects.toThrow('Field required');
  });
  it('falls back to the status when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>Bad gateway</html>', { status: 502 })));
    await expect(getJSON('/map', {}, signal())).rejects.toThrow('API request failed (502)');
  });
  it('explains an unreachable API instead of reporting "Failed to fetch"', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(getJSON('/map/meta', {}, signal())).rejects.toThrow(`could not be reached at ${API_BASE}`);
  });
});

describe('live alert feed', () => {
  const alert: LiveAlert = { country: 'HU', day: '2026-11-02', created_at: '2026-11-02T12:00:00Z', alert_type: 'threat_burst', subject: 'phishing', score: 0.91, details: '' };

  it('derives the WebSocket address from the API address', () => {
    expect(liveURL('http://localhost:5173/#europe')).toBe(`${API_BASE.replace(/^http/, 'ws')}/live`);
    expect(liveURL('https://ace.example/')).toMatch(/^wss?:\/\/.+\/live$/);
  });
  it('accepts status and complete alert messages only', () => {
    expect(parseLiveMessage(JSON.stringify({ type: 'status', state: 'connected', message: 'ok' }))).toEqual({ type: 'status', state: 'connected', message: 'ok' });
    expect(parseLiveMessage(JSON.stringify({ type: 'alert', alert: { ...alert, details: undefined } }))).toEqual({ type: 'alert', alert });
    expect(parseLiveMessage(JSON.stringify({ type: 'alert', alert: { ...alert, subject: '' } }))).toEqual({ type: 'alert', alert: { ...alert, subject: '' } });
    expect(parseLiveMessage(JSON.stringify({ type: 'skipped', message: 'fell behind' }))).toEqual({ type: 'skipped', message: 'fell behind' });
    for (const bad of ['not json', 'null', '[]', '{"type":"status","state":"exploded"}', '{"type":"alert"}',
      JSON.stringify({ type: 'alert', alert: { ...alert, score: 'high' } }), JSON.stringify({ type: 'alert', alert: { ...alert, country: '' } })]) {
      expect(parseLiveMessage(bad)).toBeNull();
    }
  });
  it('keeps the latest arrival first, without repeats and within the cap', () => {
    const drift = { ...alert, alert_type: 'drift', subject: 'SIEM' };
    expect(addAlert([alert], drift)).toEqual([drift, alert]);
    expect(addAlert([drift, alert], { ...alert, score: 0.95 })).toEqual([{ ...alert, score: 0.95 }, drift]);
    expect(addAlert([alert], { ...alert, country: 'DE' })).toHaveLength(2);
    expect(addAlert([drift, alert], { ...alert, subject: 'malware' }, 2)).toEqual([{ ...alert, subject: 'malware' }, drift]);
  });
});

describe('live feed connection', () => {
  // Stands in for the browser's WebSocket; the test plays the API's side.
  class Socket {
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: ((event: { code: number }) => void) | null = null;
    closed = false;
    constructor(public url: string) {}
    close() { this.closed = true; }
    send(message: object) { this.onmessage?.({ data: JSON.stringify(message) }); }
  }
  function open() {
    vi.useFakeTimers();
    const sockets: Socket[] = [];
    const seen = { statuses: [] as FeedStatus[], alerts: [] as LiveAlert[], skipped: 0 };
    const stop = openLiveFeed('ws://api.test/live', 'https://ace.example', {
      alert: alert => seen.alerts.push(alert), status: status => seen.statuses.push(status), skipped: () => seen.skipped++,
    }, url => { sockets.push(new Socket(url)); return sockets.at(-1) as unknown as WebSocket; });
    return { sockets, seen, stop };
  }

  it('stops and says the page is not allowed when the API refuses its origin', () => {
    const { sockets, seen } = open();
    sockets[0].onclose?.({ code: 1008 });
    vi.advanceTimersByTime(120_000);
    expect(sockets).toHaveLength(1);
    expect(seen.statuses.at(-1)?.state).toBe('refused');
    expect(seen.statuses.at(-1)?.message).toContain('https://ace.example');
    expect(seen.statuses.at(-1)?.message).toContain('API_CORS_ORIGINS');
  });
  it('reconnects with a growing delay after a lost connection', () => {
    const { sockets, seen } = open();
    sockets[0].onclose?.({ code: 1006 });
    expect(seen.statuses.at(-1)?.state).toBe('offline');
    vi.advanceTimersByTime(999);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);
    sockets[1].onclose?.({ code: 1006 });
    vi.advanceTimersByTime(1999);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(3);
  });
  it('does not reconnect to a switched-off feed, and closes when the page leaves', () => {
    const { sockets, seen, stop } = open();
    sockets[0].send({ type: 'status', state: 'disabled', message: 'off' });
    sockets[0].onclose?.({ code: 1000 });
    vi.advanceTimersByTime(120_000);
    expect(sockets).toHaveLength(1);
    expect(seen.statuses).toEqual([{ state: 'disabled', message: 'off' }]);
    stop();
    expect(sockets[0].closed).toBe(true);
  });
  it('reports skipped alerts apart from the state, so a heartbeat does not hide them', () => {
    const { sockets, seen } = open();
    sockets[0].send({ type: 'status', state: 'connected', message: 'ok' });
    sockets[0].send({ type: 'skipped', message: 'fell behind' });
    sockets[0].send({ type: 'status', state: 'connected', message: 'ok' });
    sockets[0].send({ type: 'alert', alert: { country: 'HU', day: '2026-11-02', created_at: '2026-11-02T12:00:00Z', alert_type: 'drift', subject: 'SIEM', score: 2 } });
    expect(seen.skipped).toBe(1);
    expect(seen.statuses.map(status => status.state)).toEqual(['connected', 'connected']);
    expect(seen.alerts.map(alert => alert.subject)).toEqual(['SIEM']);
  });
});
