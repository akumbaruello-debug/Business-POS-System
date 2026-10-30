/**
 * Verification for the POS payment in-flight guard + tender-history dedupe.
 * Imports the REAL production atoms from lib/pos-payment-guard.js — the same
 * module app/(protected)/pos/page.tsx handlePayment uses. No mirror copy.
 * Run: node scripts/verify-pos-guard.mjs   (assert + exit code)
 */
import assert from 'node:assert/strict'
import {
  createInFlightGuard,
  dedupeRecordedPayments,
} from '../lib/pos-payment-guard.js'

// 1) Same-tick double click: both handlers run before React re-renders; the
//    guard's synchronous flag rejects the second acquisition in the same tick
//    -> only one submission can proceed.
{
  const g = createInFlightGuard()
  const first = g.enter()
  const second = g.enter()
  assert.equal(first, true)
  assert.equal(second, false)
  console.log('PASS double-click: single acquisition (second enter() rejected)')
}

// 2) Guard releases after completion -> next payment acquires.
{
  const g = createInFlightGuard()
  assert.equal(g.enter(), true)
  g.exit()
  assert.equal(g.enter(), true)
  console.log('PASS guard releases after completion')
}

// 3) Failed request still releases: the page's finally{} calls exit() on
//    every path, so a rejected POST must leave the guard acquirable so the
//    user can retry.
{
  const g = createInFlightGuard()
  assert.equal(g.enter(), true)
  async function failingPost() {
    try {
      await Promise.reject(new Error('simulated network failure'))
    } finally {
      g.exit()
    }
  }
  let caught = null
  try {
    await failingPost()
  } catch (err) {
    caught = err
  }
  assert.equal(caught?.message, 'simulated network failure')
  assert.equal(g.enter(), true)
  console.log('PASS guard releases after failure; retry allowed')
}

// 4) Tender history dedupes by payment id: an idempotent replay of the same
//    payment (same id) never adds a duplicate row; a genuinely new payment
//    is appended.
{
  const p1 = { id: 1, amount: 50, change_amount: 0 }
  const p1Replay = { id: 1, amount: 50, change_amount: 0 }
  const p2 = { id: 2, amount: 30, change_amount: 0 }
  const afterReplay = dedupeRecordedPayments([p1], p1Replay)
  assert.deepEqual(afterReplay.map((p) => p.id), [1])
  assert.equal(afterReplay.length, 1)
  const afterNew = dedupeRecordedPayments(afterReplay, p2)
  assert.deepEqual(afterNew.map((p) => p.id), [1, 2])
  assert.equal(afterNew.length, 2)
  console.log('PASS dedupe by payment id (replay no-duplicate, new appended)')
}

// 5) Last-write-wins on replay with a changed amount (same id, new value).
{
  const out = dedupeRecordedPayments(
    [{ id: 1, amount: 50, change_amount: 0 }],
    { id: 1, amount: 60, change_amount: 0 },
  )
  assert.deepEqual(out, [{ id: 1, amount: 60, change_amount: 0 }])
  console.log('PASS dedupe last-write-wins on same id')
}

console.log('ALL POS GUARD CHECKS PASSED')