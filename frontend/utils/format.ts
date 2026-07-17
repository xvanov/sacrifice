// Shared, user-facing formatting helpers. Keep every money/date string in the
// app flowing through here so nothing renders raw cents or ISO timestamps.

const MONEY = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Cents → "$1,234.56". Always two decimals. */
export function formatMoney(cents: number | null | undefined, currency = 'usd'): string {
  const value = ((cents ?? 0) as number) / 100;
  if (currency && currency.toLowerCase() !== 'usd') {
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency.toUpperCase(),
        minimumFractionDigits: 2,
      }).format(value);
    } catch {
      // Fall through to USD formatting for unknown currency codes.
    }
  }
  return MONEY.format(value);
}

/** ISO → "Jul 18, 2026" (date only). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/** ISO → "Jul 18, 2026, 10:45 PM" (date + time). */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/** ISO → "3m ago" / "5h ago" / "Jul 18" — for dense feeds like notifications. */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const diffMs = Date.now() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays}d ago`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

/** IANA timezone id → human label: "America/New_York" → "New York (ET)".
 * Falls back to the de-underscored city name when the offset can't be
 * resolved; "UTC" stays "UTC". */
export function formatTimezone(tz: string | null | undefined): string {
  if (!tz) return '—';
  if (tz.toUpperCase() === 'UTC') return 'UTC';
  const city = (tz.split('/').pop() || tz).replace(/_/g, ' ');
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      timeZoneName: 'short',
    }).formatToParts(new Date());
    const abbr = parts.find((p) => p.type === 'timeZoneName')?.value;
    // Numeric fallbacks like "GMT-4" are less friendly than the bare city.
    if (abbr && !abbr.startsWith('GMT')) return `${city} (${abbr})`;
  } catch {
    // Unknown zone id — just show the cleaned city.
  }
  return city;
}
