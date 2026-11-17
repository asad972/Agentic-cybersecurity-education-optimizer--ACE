const DAY = 86_400_000;

/** Yesterday in UTC: the latest day whose daily aggregates are complete. */
export function yesterday(now = Date.now()) {
  return new Date(now - DAY).toISOString().slice(0, 10);
}

export function formatDate(day: string) {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${day}T00:00:00Z`));
}

export function formatTime(timestamp: string) {
  const time = new Date(timestamp);
  if (Number.isNaN(time.getTime())) return timestamp;
  return `${new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(time)} UTC`;
}

export function countLabel(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? 'No data' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 }).format(value);
}

/** A real calendar day (not 2026-02-31) inside the range the API accepts. */
export function isDay(value: string, min: string, max: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value < min || value > max) return false;
  const time = Date.parse(`${value}T00:00:00Z`);
  return !Number.isNaN(time) && new Date(time).toISOString().slice(0, 10) === value;
}
