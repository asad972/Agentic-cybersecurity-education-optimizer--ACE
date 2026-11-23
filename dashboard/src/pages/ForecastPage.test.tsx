// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CountryAlert, ForecastItem, GapItem } from '../services/api';

const chartRender = vi.hoisted(() => vi.fn());

vi.mock('../components/Chart', () => ({
  Chart: ({ label, option }: { label: string; option: unknown }) => {
    chartRender(label, option);
    return <div role="img" aria-label={label} />;
  },
}));
vi.mock('../components/CountryMap', () => ({
  CountryMap: ({ onSelect }: { onSelect: (code: string) => void }) => <div role="img" aria-label="Supply map"><button onClick={() => onSelect('FR')}>Choose France on map</button></div>,
}));

import { ForecastPage, forecastBands, initialSkill, newestDriftAlerts, rankGap } from './ForecastPage';

const gapItems: GapItem[] = [
  { skill: 'Terraform', demand: 20, supply: 10, gap_ratio: null },
  { skill: 'AWS Security', demand: 40, supply: 25, gap_ratio: 1.6 },
  { skill: 'Cloud Security', demand: 40, supply: 30, gap_ratio: null },
  ...Array.from({ length: 9 }, (_, index) => ({ skill: `Skill ${index}`, demand: 10 - index, supply: index, gap_ratio: 1 })),
];

const forecastItems: ForecastItem[] = [
  { target_month: '2026-11', predicted: 12, lower: 8, upper: 16, model: 'sarima', trained_at: '2026-11-09T02:00:00Z' },
  { target_month: '2026-12', predicted: 15, lower: 10, upper: 21, model: 'sarima', trained_at: '2026-11-09T02:00:00Z' },
];

function alert(index: number, type = 'drift'): CountryAlert {
  return { created_at: `2026-11-${String(index + 1).padStart(2, '0')}T10:00:00Z`, alert_type: type, subject: `${type}-${index}`, score: index, details: `details ${index}` };
}

let mapFailures = 0;
let emptyGapForGermany = false;
let calls: URL[];

beforeEach(() => {
  calls = [];
  mapFailures = 0;
  emptyGapForGermany = false;
  chartRender.mockClear();
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input));
    calls.push(url);
    if (url.pathname === '/map/meta') return Response.json({ countries: [{ code: 'DE', name: 'Germany' }, { code: 'FR', name: 'France' }], metrics: [], date_min: '2020-01-01', date_max: '2026-11-15' });
    if (url.pathname === '/gap') {
      const country = url.searchParams.get('country');
      const items = emptyGapForGermany && country === 'DE' ? [] : gapItems;
      return Response.json({ country, month: url.searchParams.get('month'), items });
    }
    if (url.pathname === '/map') {
      if (mapFailures-- > 0) return Response.json({ detail: 'Supply storage unavailable' }, { status: 503 });
      return Response.json({ metric: 'supply', date: url.searchParams.get('date'), item: url.searchParams.get('item'), unit: 'profiles', period: '365-day stock snapshot', note: 'event day', data: [{ country: 'DE', value: 30 }, { country: 'FR', value: null }] });
    }
    if (url.pathname === '/forecast') return Response.json({ region: url.searchParams.get('region'), skill: url.searchParams.get('skill'), items: forecastItems });
    if (url.pathname === '/map/country') return Response.json({ country: url.searchParams.get('country'), date: url.searchParams.get('date'), top_skills: [], top_certifications: [], workforce: null, alerts: [...Array.from({ length: 12 }, (_, index) => alert(index)), alert(20, 'threat_burst')], alerts_truncated: false });
    return Response.json({ detail: 'Unexpected request' }, { status: 500 });
  }));
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('M3 dashboard transformations', () => {
  it('ranks deterministically, limits bars and chooses a positive default', () => {
    const ranked = rankGap(gapItems);
    expect(ranked).toHaveLength(10);
    expect(ranked.slice(0, 2).map(item => item.skill)).toEqual(['AWS Security', 'Cloud Security']);
    expect(initialSkill(gapItems)).toBe('AWS Security');
    expect(initialSkill([{ skill: 'Linux', demand: 0, supply: 20, gap_ratio: 0 }])).toBe('');
  });

  it('creates coherent confidence-band series', () => {
    expect(forecastBands(forecastItems)).toEqual({ months: ['2026-11', '2026-12'], predicted: [12, 15], lower: [8, 10], range: [8, 11] });
  });

  it('keeps only the ten newest drift alerts', () => {
    const filtered = newestDriftAlerts([...Array.from({ length: 12 }, (_, index) => alert(index)), alert(20, 'threat_burst')]);
    expect(filtered).toHaveLength(10);
    expect(filtered[0].subject).toBe('drift-11');
    expect(filtered.some(item => item.alert_type === 'threat_burst')).toBe(false);
  });
});

describe('M3 dashboard requests and states', () => {
  it('selects the highest-Demand Skill, renders all panels and preserves null Gap', async () => {
    const { container } = render(<ForecastPage />);
    expect(await screen.findByDisplayValue('AWS Security')).toBeTruthy();
    expect(await screen.findByRole('img', { name: 'Supply map' })).toBeTruthy();
    expect(screen.getByRole('img', { name: /Demand and Supply/ })).toBeTruthy();
    expect(screen.getByRole('img', { name: /Twelve-month AWS Security/ })).toBeTruthy();
    expect(screen.getAllByText('Not enough Supply data').length).toBeGreaterThan(0);
    await waitFor(() => expect(calls.some(url => url.pathname === '/forecast' && url.searchParams.get('region') === 'EU' && url.searchParams.get('skill') === 'AWS Security')).toBe(true));
    expect(container.querySelectorAll('.forecast-alerts li')).toHaveLength(10);
    expect(screen.queryByText('threat_burst-20')).toBeNull();
  });

  it('keeps a manually applied Skill when the country and scope change', async () => {
    const user = userEvent.setup();
    render(<ForecastPage />);
    const skill = await screen.findByDisplayValue('AWS Security');
    await user.clear(skill);
    await user.type(skill, 'Terraform{Enter}');
    await user.selectOptions(screen.getByRole('combobox', { name: /Country/ }), 'FR');
    await user.click(screen.getByRole('radio', { name: 'FR' }));

    await waitFor(() => expect(calls.some(url => url.pathname === '/forecast' && url.searchParams.get('region') === 'FR' && url.searchParams.get('skill') === 'Terraform')).toBe(true));
    expect(screen.getByDisplayValue('Terraform')).toBeTruthy();
  });

  it('keeps chart options stable while editing an unapplied Skill draft', async () => {
    const user = userEvent.setup();
    render(<ForecastPage />);
    const skill = await screen.findByDisplayValue('AWS Security');
    await screen.findByRole('img', { name: /Twelve-month AWS Security/ });

    const initialGapOption = chartRender.mock.calls.find(([label]) => String(label).startsWith('Demand and Supply'))?.[1];
    const initialForecastOption = chartRender.mock.calls.find(([label]) => String(label).startsWith('Twelve-month'))?.[1];
    expect(initialGapOption).toBeTruthy();
    expect(initialForecastOption).toBeTruthy();
    chartRender.mockClear();

    await user.type(skill, ' draft');

    const gapOptions = chartRender.mock.calls
      .filter(([label]) => String(label).startsWith('Demand and Supply'))
      .map(([, option]) => option);
    const forecastOptions = chartRender.mock.calls
      .filter(([label]) => String(label).startsWith('Twelve-month'))
      .map(([, option]) => option);
    expect(gapOptions.length).toBeGreaterThan(0);
    expect(forecastOptions.length).toBeGreaterThan(0);
    expect(gapOptions.every(option => option === initialGapOption)).toBe(true);
    expect(forecastOptions.every(option => option === initialForecastOption)).toBe(true);
  });

  it('chooses an initial Skill after an earlier Gap response was empty', async () => {
    emptyGapForGermany = true;
    const user = userEvent.setup();
    render(<ForecastPage />);

    expect(await screen.findByText('No Skills Gap rows are stored for this country and month.')).toBeTruthy();
    expect((screen.getByPlaceholderText('Exact Skill name') as HTMLInputElement).value).toBe('');

    await user.selectOptions(screen.getByRole('combobox', { name: /Country/ }), 'FR');

    expect(await screen.findByDisplayValue('AWS Security')).toBeTruthy();
    await waitFor(() => expect(calls.some(url => url.pathname === '/forecast'
      && url.searchParams.get('skill') === 'AWS Security')).toBe(true));
  });

  it('keeps successful panels visible when Supply fails and retries only that panel', async () => {
    mapFailures = 1;
    const user = userEvent.setup();
    render(<ForecastPage />);
    expect(await screen.findByText('Supply storage unavailable')).toBeTruthy();
    expect(screen.getByRole('img', { name: /Demand and Supply/ })).toBeTruthy();
    const before = calls.filter(url => url.pathname === '/map').length;
    await user.click(screen.getByRole('button', { name: /Try again/ }));
    expect(await screen.findByRole('img', { name: 'Supply map' })).toBeTruthy();
    expect(calls.filter(url => url.pathname === '/map').length).toBe(before + 1);
  });
});
