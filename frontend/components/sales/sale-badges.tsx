'use client'

import { lifecycleLabel, lifecycleTone, paymentLabel, paymentTone } from '@/lib/sale-ui'

// -----------------------------------------------------------------------------
// Sales status badges.
//
// Rendered from the `sales-badge` class in globals.css with per-status tones so
// the three consumers (list table, detail header, detail summary) cannot drift.
// Labels are human-readable; the raw snake_case enum stays in the data layer.
// -----------------------------------------------------------------------------

export function LifecycleBadge({ status }: { status: string }) {
  const [bg, fg, bd] = lifecycleTone(status)
  return (
    <span
      className="sales-badge"
      style={{ background: bg, color: fg, borderColor: bd }}
      title={`Lifecycle: ${lifecycleLabel(status)}`}
    >
      <span className="dot" />
      {lifecycleLabel(status)}
    </span>
  )
}

export function PaymentBadge({ state }: { state: string }) {
  const [bg, fg, bd] = paymentTone(state)
  return (
    <span
      className="sales-badge"
      style={{ background: bg, color: fg, borderColor: bd }}
      title={`Payment: ${paymentLabel(state)}`}
    >
      {paymentLabel(state)}
    </span>
  )
}
