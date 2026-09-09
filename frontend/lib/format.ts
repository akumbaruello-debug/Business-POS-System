/**
 * Display formatters for IDR currency, percentages, and counts.
 *
 * Dashboard monetary values use Indonesian thousands separators with no
 * prefix: 12.514.000 (V0 baseline). Negative: -992.141.
 */

const IDR = new Intl.NumberFormat('id-ID', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

/** Format a numeric amount in Indonesian Rupiah (period thousands, no prefix). */
export function formatIDR(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return IDR.format(value)
}

/** Format a number as "1,248" (id-ID). */
export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return IDR.format(value)
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
  return `${IDR.format(value)} ${value === 1 ? unit.replace(/s$/, '') : unit}`
}
