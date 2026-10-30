/**
 * POS payment double-submit guard + tender-history dedupe.
 *
 * Pure atoms shared by the production page (app/(protected)/pos/page.tsx
 * handlePayment) and the verification script (scripts/verify-pos-guard.mjs)
 * so the test exercises the exact logic the page runs — no mirror copy.
 *
 * In-flight guard: the React `busy` state cannot close the same-tick
 * double-click window (two handlers both read `false` before the re-render
 * lands). A synchronous flag set BEFORE the first await closes it.
 * `enter()` returns false when a payment is already being recorded, and the
 * caller must then return without submitting.
 */

/** @returns {{ enter: () => boolean, exit: () => void }} */
export function createInFlightGuard() {
  let inFlight = false
  return {
    /** Acquire the guard. False when a call is already in flight. */
    enter() {
      if (inFlight) return false
      inFlight = true
      return true
    },
    /** Release the guard. Must run on every path (finally). */
    exit() {
      inFlight = false
    },
  }
}

/**
 * Merge `next` into `payments` keyed by payment id, so an idempotent replay
 * of the same payment (same id) never duplicates a tender-history row while
 * a genuinely new payment is appended. Last-write-wins per id.
 *
 * @param {Array<{id: number | string}>} payments
 * @param {{id: number | string}} next
 * @returns {Array<{id: number | string}>}
 */
export function dedupeRecordedPayments(payments, next) {
  const map = new Map(payments.map((p) => [p.id, p]))
  map.set(next.id, next)
  return Array.from(map.values())
}