// -----------------------------------------------------------------------------
// Shared Sales presentation primitives.
//
// Both `/sales` and `/sales/[id]` render lifecycle + payment state, so the
// badge definitions and the human-readable labels live here — one source of
// truth instead of two drifting copies.
//
// Labels are display-only: the raw `lifecycle_status` / `payment_state`
// values are never mutated, and nothing here invents a status the backend
// does not emit.
// -----------------------------------------------------------------------------

/** Lifecycle → human label. Keys are exactly the backend enum values. */
export const LIFECYCLE_LABELS: Record<string, string> = {
  draft: 'Draft',
  posted: 'Posted',
  completed: 'Completed',
  partially_returned: 'Partially returned',
  returned: 'Returned',
  cancelled: 'Cancelled',
}

/** Payment state → human label. Keys are exactly the backend enum values. */
export const PAYMENT_LABELS: Record<string, string> = {
  unpaid: 'Unpaid',
  partial: 'Partially paid',
  paid: 'Paid',
}

/** Lifecycle statuses the backend treats as cancellable.
 *  Mirrors `_LIFECYCLE_CANCELLABLE` in backend/app/services/sales.py —
 *  notably EXCLUDES `draft`, which is deleted, not cancelled. */
export const CANCELLABLE_STATUSES = [
  'posted',
  'completed',
  'partially_returned',
  'returned',
] as const

/** Lifecycle statuses the backend accepts a return against.
 *  Mirrors `_LIFECYCLE_RETURNABLE` in backend/app/services/sales.py. */
export const RETURNABLE_STATUSES = [
  'posted',
  'completed',
  'partially_returned',
] as const

/** Payment is allowed on everything except draft (and, per the documented
 *  lifecycle, terminal `cancelled` / fully `returned` sales). The backend
 *  enforces the same set (`_PAYABLE_STATES` in backend/app/services/sales.py). */
export const PAYABLE_STATUSES = [
  'posted',
  'completed',
  'partially_returned',
] as const

export function isPayable(status: string): boolean {
  return (PAYABLE_STATUSES as readonly string[]).includes(status)
}

export function lifecycleLabel(status: string): string {
  return LIFECYCLE_LABELS[status] ?? status
}

export function paymentLabel(state: string): string {
  return PAYMENT_LABELS[state] ?? state
}

export function isCancellable(status: string): boolean {
  return (CANCELLABLE_STATUSES as readonly string[]).includes(status)
}

export function isReturnable(status: string): boolean {
  return (RETURNABLE_STATUSES as readonly string[]).includes(status)
}

/** Tone per lifecycle status. Tailwind-free class tokens defined in globals.css. */
const LIFECYCLE_TONE: Record<string, [string, string, string]> = {
  draft: ['#f1f5f9', '#475569', '#e2e8f0'],
  posted: ['#eff6ff', '#2563eb', '#dbeafe'],
  completed: ['#ecfdf5', '#059669', '#d1fae5'],
  partially_returned: ['#fffbeb', '#d97706', '#fde68a'],
  returned: ['#faf5ff', '#7c3aed', '#e9d5ff'],
  cancelled: ['#fef2f2', '#dc2626', '#fecaca'],
}

const PAYMENT_TONE: Record<string, [string, string, string]> = {
  unpaid: ['#fef2f2', '#dc2626', '#fecaca'],
  partial: ['#eff6ff', '#2563eb', '#dbeafe'],
  paid: ['#ecfdf5', '#059669', '#d1fae5'],
}

export function lifecycleTone(status: string): [string, string, string] {
  return LIFECYCLE_TONE[status] ?? LIFECYCLE_TONE.draft
}

export function paymentTone(state: string): [string, string, string] {
  return PAYMENT_TONE[state] ?? PAYMENT_TONE.unpaid
}

// -----------------------------------------------------------------------------
// Numeric + date formatting
// -----------------------------------------------------------------------------

/** NUMERIC columns arrive as strings on list rows ("485000.00") and as numbers
 *  on detail. Coerce before any arithmetic or comparison. */
export const num = (v: unknown): number => {
  if (v == null) return 0
  if (typeof v === 'number') return v
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('id-ID', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  })
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('id-ID', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Normalise a backend ETag for `If-Match`.
 *
 * The response shim double-wraps quotes (\"\"<inner>\"\"), so collapse to a
 * single RFC 7232 quoting layer. Must be applied to both header- and
 * body-sourced tags before they go on the wire.
 */
export function sanitizeEtag(raw: string | null | undefined): string {
  if (!raw) return ''
  let v = raw.trim()
  while (v.length >= 2 && v.startsWith('"') && v.endsWith('"')) {
    v = v.slice(1, -1)
  }
  return v ? `"${v}"` : ''
}
