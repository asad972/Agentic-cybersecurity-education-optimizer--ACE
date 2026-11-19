// Helpers for the threat landscape page (#40). Days are ISO dates (YYYY-MM-DD) in UTC.
import type { Country, HourlyThreats } from './api';

const DAY = 86_400_000;
const HOUR = 3_600_000;

export function addDays(day: string, days: number) {
  return new Date(Date.parse(`${day}T00:00:00Z`) + days * DAY).toISOString().slice(0, 10);
}

export function daysBetween(first: string, last: string) {
  return Math.round((Date.parse(`${last}T00:00:00Z`) - Date.parse(`${first}T00:00:00Z`)) / DAY);
}

/** Every UTC hour of the answer's days, with null where nothing is stored: a gap, not zero. */
export function hourSlots(answer: HourlyThreats): { hour: string; total: number | null }[] {
  const start = Date.parse(`${answer.from}T00:00:00Z`);
  const end = Date.parse(`${answer.to}T00:00:00Z`) + DAY;
  if (!(end > start) || end - start > 31 * DAY) return [];
  const stored = new Map(answer.hours.map(hour => [Date.parse(hour.hour), hour.total]));
  const slots = [];
  for (let time = start; time < end; time += HOUR) {
    slots.push({ hour: new Date(time).toISOString().replace('.000Z', 'Z'), total: stored.get(time) ?? null });
  }
  return slots;
}

/** Index of the hour each burst marks, for bursts that fall on an hour with stored observations. */
export function burstSlots(answer: HourlyThreats, slots: { hour: string; total: number | null }[]) {
  const index = new Map(slots.map((slot, position) => [Date.parse(slot.hour), position]));
  const marked = new Set<number>();
  for (const burst of answer.bursts) {
    const position = index.get(Math.floor(Date.parse(burst.created_at) / HOUR) * HOUR);
    if (position !== undefined && slots[position].total != null) marked.add(position);
  }
  return [...marked].sort((a, b) => a - b);
}

/**
 * The last day of the time slider's window of `length` days. It stays where it is while the
 * chosen day is inside; a day outside (from the date field or the arrows) moves the window so
 * that day sits in its middle, never past today.
 */
export function sliderEnd(end: string, chosen: string, today: string, length = 30) {
  const start = addDays(end, -(length - 1));
  if (chosen >= start && chosen <= end) return end;
  const middle = addDays(chosen, Math.floor(length / 2));
  return middle > today ? today : middle;
}

/** Country names from the map outline (Natural Earth): threats are measured worldwide. */
export function worldCountries(outline: unknown): Country[] {
  const features = (outline as { features?: { properties?: { code?: unknown; name?: unknown } }[] } | null)?.features ?? [];
  const names = new Map<string, string>();
  for (const { properties } of features) {
    const { code, name } = properties ?? {};
    if (typeof code === 'string' && /^[A-Z]{2}$/.test(code) && typeof name === 'string' && !names.has(code)) names.set(code, name);
  }
  return [...names].map(([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name));
}

/** The known countries plus any code the API reported that the outline does not name. */
export function withCodes(countries: Country[], codes: string[]) {
  const known = new Set(countries.map(country => country.code));
  const extra = [...new Set(codes)].filter(code => !known.has(code)).map(code => ({ code, name: code }));
  return extra.length ? [...countries, ...extra].sort((a, b) => a.name.localeCompare(b.name)) : countries;
}
