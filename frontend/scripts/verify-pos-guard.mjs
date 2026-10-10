/**
 * Self-running verification of the POS payment in-flight guard semantics.
 * Mirrors the exact logic in app/(protected)/pos/page.tsx handlePayment:
 *   - a second call while the first is in flight is rejected,
 *   - the guard resets after a failed (rejected) request so a retry works,
 *   - recorded payments are deduped by payment id.
 * No framework: assert + exit code.
 */
import assert from 'node:assert/strict'

// Minimal stand-in for the page state (busy + ref guard).
function makePaymentController({ failFirst = false } = {}) {
  let busy = false
  let paymentInFlight = false
  let failed = false
  const submitted = []

  async function handlePayment(amount) {
    // Same early-out as the page: reject while busy OR while a payment is
    // already being recorded. The ref closes the same-tick double-click gap
    // that the `busy` state alone cannot.
    if (busy || paymentInFlight) return { ok: false, reason: 'in-flight' }
    paymentInFlight = true
    busy = true
    try {
      // Simulated network request; throws on the first call when failFirst.
      const doFail = failFirst && !failed
      if (doFail) failed = true
      await new Promise((r) => setTimeout(r, 5))
      if (doFail) throw new Error('simulated network failure')
      submitted.push(amount)
      return { ok: true }
    } finally {
      busy = false
      paymentInFlight = false // guard resets even when the request fails
    }
  }

  return { handlePayment, submitted, getGuard: () => paymentInFlight }
}

function dedupeRecorded(payments, next) {
  const map = new Map(payments.map((p) => [p.id, p]))
  map.set(next.id, next)
  return Array.from(map.values())
}

// 1) Double-click in the same tick: both handlers see guard=false, but the
//    ref set synchronously before the first await means the second call is
//    rejected -> only one payment submitted.
{
  const c = makePaymentController()
  const [a, b] = await Promise.all([c.handlePayment(50), c.handlePayment(50)])
  assert.equal(a.ok, true)
  assert.equal(b.ok, false) // second click rejected
  assert.deepEqual(c.submitted, [50])
  console.log('PASS double-click: single submission')
}

// 2) Guard reset after success -> next payment goes through.
{
  const c = makePaymentController()
  await c.handlePayment(50)
  await c.handlePayment(30)
  assert.deepEqual(c.submitted, [50, 30])
  assert.equal(c.getGuard(), false)
  console.log('PASS guard resets after success')
}

// 3) Failed request: guard must reset so a retry is allowed (the page's
//    finally{} resets paymentInFlight regardless of outcome).
{
  const c = makePaymentController({ failFirst: true })
  const first = await c.handlePayment(50).catch(() => ({ ok: false, failed: true }))
  assert.equal(first.failed, true)
  assert.deepEqual(c.submitted, []) // nothing recorded on failure
  assert.equal(c.getGuard(), false) // finally reset the guard
  const retry = await c.handlePayment(50).catch(() => ({ ok: false, failed: true }))
  assert.equal(retry.ok, true) // guard reset -> retry succeeds
  assert.deepEqual(c.submitted, [50])
  console.log('PASS guard resets after failure; retry allowed')
}

// 4) Registered payments are deduped by payment id (replay of the same
//    payment returns the same id -> no duplicate tender-history row).
{
  const p1 = { id: 1, amount: 50, change_amount: 0 }
  const p1again = { id: 1, amount: 50, change_amount: 0 } // idempotent replay
  const p2 = { id: 2, amount: 30, change_amount: 0 }
  const out = dedupeRecorded(dedupeRecorded([p1], p1again), p2)
  assert.deepEqual(out.map((p) => p.id), [1, 2])
  assert.equal(out.length, 2)
  console.log('PASS dedupe by payment id')
}

console.log('ALL POS GUARD CHECKS PASSED')
