/**
 * Display formatters for IDR currency, percentages, and counts.
 *
 * V0 baseline used "Rp 128.450.000" (period thousands separator,
 * no decimals, "Rp" prefix with non-breaking space). Preserve it.
 */

const IDR = new Intl.NumberFormat('id-ID', {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})

/** Format a numeric amount as Indonesian Rupiah. */
export function formatIDR(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `Rp ${IDR.format(value)}`
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
