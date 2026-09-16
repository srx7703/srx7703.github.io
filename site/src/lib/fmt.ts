/** Formatting helpers. Every number on a page comes from data/facts or data/marts; these only format. */

export const pct = (x: number | null | undefined, digits = 0): string =>
  x == null ? '—' : `${(x * 100).toFixed(digits)}%`;

export const pts = (x: number | null | undefined, digits = 1): string => {
  if (x == null) return '—';
  const v = x * 100;
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)} pt`;
};

export const num = (x: number | null | undefined): string =>
  x == null ? '—' : new Intl.NumberFormat('en-US').format(Math.round(x));

export const usd = (x: number | null | undefined): string => {
  if (x == null) return '—';
  const abs = Math.abs(x);
  if (abs >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(x / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(x / 1e3).toFixed(0)}K`;
  return `$${x.toFixed(0)}`;
};

export const dateUtc = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) + ' UTC';
};

export const dayUtc = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' });
};

export const daysUntil = (isoDay: string, fromIso?: string): number => {
  const from = fromIso ? new Date(fromIso) : new Date();
  const to = new Date(`${isoDay}T00:00:00Z`);
  return Math.max(0, Math.round((to.getTime() - from.getTime()) / 86400000));
};
