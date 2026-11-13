import { describe, expect, it } from 'vitest';
import { countLabel, formatDate, formatTime, isDay, yesterday } from './dates';

describe('dates and values', () => {
  it('accepts only real calendar days inside the range', () => {
    expect(isDay('2026-11-02', '2020-01-01', '2026-11-11')).toBe(true);
    expect(isDay('2026-02-31', '2020-01-01', '2026-11-11')).toBe(false);
    expect(isDay('2019-12-31', '2020-01-01', '2026-11-11')).toBe(false);
    expect(isDay('2026-11-12', '2020-01-01', '2026-11-11')).toBe(false);
    expect(isDay('', '2020-01-01', '2026-11-11')).toBe(false);
  });
  it('uses UTC for yesterday and for display', () => {
    expect(yesterday(Date.parse('2026-11-11T00:30:00Z'))).toBe('2026-11-10');
    expect(formatDate('2026-11-02')).toBe('2 Nov 2026');
    expect(formatTime('2026-11-02T09:30:00+00:00')).toBe('09:30 UTC');
    expect(formatTime('not a time')).toBe('not a time');
  });
  it('never shows a missing value as zero', () => {
    expect(countLabel(null)).toBe('No data');
    expect(countLabel(undefined)).toBe('No data');
    expect(countLabel(0)).toBe('0');
    expect(countLabel(12345)).toBe('12,345');
    expect(countLabel(2.456)).toBe('2.46');
  });
});
