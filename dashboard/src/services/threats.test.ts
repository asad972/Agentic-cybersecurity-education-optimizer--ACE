import { describe, expect, it } from 'vitest';
import type { HourlyThreats } from './api';
import { addDays, burstSlots, daysBetween, hourSlots, sliderEnd, withCodes, worldCountries } from './threats';

const answer = (overrides: Partial<HourlyThreats> = {}): HourlyThreats => ({
  country: 'HU', from: '2026-11-02', to: '2026-11-02', total: 0, by_type: {}, hours: [], bursts: [], bursts_truncated: false, note: '', ...overrides,
});

describe('threat landscape helpers', () => {
  it('counts days in UTC, across month ends', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(daysBetween('2026-10-25', '2026-11-02')).toBe(8);
  });
  it('gives every hour of the days asked for, with gaps where nothing is stored', () => {
    const slots = hourSlots(answer({ hours: [{ hour: '2026-11-02T09:00:00Z', total: 6, counts: { phishing: 6 } }] }));
    expect(slots).toHaveLength(24);
    expect(slots[0]).toEqual({ hour: '2026-11-02T00:00:00Z', total: null });
    expect(slots[9]).toEqual({ hour: '2026-11-02T09:00:00Z', total: 6 });
    expect(slots.filter(slot => slot.total != null)).toHaveLength(1);
    expect(hourSlots(answer({ from: '2026-10-27', to: '2026-11-02' }))).toHaveLength(7 * 24);
    expect(hourSlots(answer({ from: '2026-11-03', to: '2026-11-02' }))).toEqual([]);
  });
  it('marks the hour of each burst, but not an hour without stored observations', () => {
    const data = answer({
      hours: [{ hour: '2026-11-02T13:00:00Z', total: 30, counts: {} }],
      bursts: [
        { created_at: '2026-11-02T13:00:00Z', threat_type: 'phishing', score: 0.9, details: '' },
        { created_at: '2026-11-02T13:00:00Z', threat_type: 'malware', score: 0.7, details: '' },
        { created_at: '2026-11-02T15:00:00Z', threat_type: 'botnet', score: 0.6, details: '' },
      ],
    });
    expect(burstSlots(data, hourSlots(data))).toEqual([13]);
  });
  it('keeps the slider still while the day is inside it, and centres a day outside it', () => {
    const today = '2026-11-19';
    expect(sliderEnd(today, '2026-11-01', today)).toBe(today);           // inside the last 30 days
    expect(sliderEnd(today, '2026-10-21', today)).toBe(today);           // first day of the window
    expect(sliderEnd(today, '2026-10-20', today)).toBe('2026-11-04');    // just before: it moves
    expect(sliderEnd('2026-11-04', '2026-11-12', today)).toBe(today);    // a later day moves it, never past today
    expect(sliderEnd('2026-06-15', '2026-06-01', today)).toBe('2026-06-15');
  });
  it('names the countries of the outline and keeps codes it does not name', () => {
    const outline = { features: [
      { properties: { code: 'HU', name: 'Hungary' } }, { properties: { code: 'AF', name: 'Afghanistan' } },
      { properties: { code: 'HU', name: 'Hungary (duplicate)' } }, { properties: { code: 'SOL', name: 'Somaliland' } }, { properties: {} },
    ] };
    const countries = worldCountries(outline);
    expect(countries).toEqual([{ code: 'AF', name: 'Afghanistan' }, { code: 'HU', name: 'Hungary' }]);
    expect(worldCountries(null)).toEqual([]);
    expect(withCodes(countries, ['HU', 'XK'])).toEqual([...countries, { code: 'XK', name: 'XK' }]);
    expect(withCodes(countries, ['HU'])).toBe(countries);
  });
});
