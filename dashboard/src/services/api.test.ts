import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_BASE, endpoint, getJSON } from './api';

afterEach(() => vi.unstubAllGlobals());
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
