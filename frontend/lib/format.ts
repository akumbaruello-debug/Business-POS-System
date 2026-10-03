/**
 * Display formatters for IDR currency, percentages, and counts.
 *
 * Dashboard monetary values use Indonesian thousands separators with no
 * prefix: 12.514.000 (V0 baseline). Negative: -992.141.
 */

const DEFAULT_INTL = new Intl.NumberFormat('id-ID', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

function intlFor(locale: string): Intl.NumberFormat {
  return locale === 'id-ID' ? DEFAULT_INTL : new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })
}

/** Format a numeric amount in Rupiah (period thousands, no prefix). */
export function formatIDR(value: number | null | undefined, locale = 'id-ID'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return intlFor(locale).format(value)
}

/** Format a number as "1,248" (id-ID). */
export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return DEFAULT_INTL.format(value)
}

/** Format a stock quantity, keeping fractional decimals (NUMERIC(15,4)
 *  schema columns allow e.g. 2.5 kg). Sign prefix is the caller's job —
 *  negatives render as "-3" via id-ID, same as formatInt. */
const QTY_INTL = new Intl.NumberFormat('id-ID', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 4,
})

export function formatQty(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return QTY_INTL.format(value)
}

/** Format a decimal as a signed percent string ("+12.8%" / "-3.4%"). */
export function formatPct(value: number | null | undefined, signed = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const pct = value * 100
  const sign = signed && pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(1)}%`
}

/** "18 items" / "5 products" — count + unit label. */
export function formatCount(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${DEFAULT_INTL.format(value)} ${value === 1 ? unit.replace(/s$/, '') : unit}`
}

/** Format a date to "1 Jan 2024" style. */
export function formatDate(
  value: Date | string | number | null | undefined,
  locale = 'id-ID',
): string {
  if (value === null || value === undefined) return '—'
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(d)
}

/** Format a date-time to "1 Jan 2024, 14:30" style. */
export function formatDateTime(
  value: Date | string | number | null | undefined,
  locale = 'id-ID',
): string {
  if (value === null || value === undefined) return '—'
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d)
}
