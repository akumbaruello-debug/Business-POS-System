'use client'

import { useState, useEffect } from 'react'
import { X } from 'lucide-react'
import { api } from '@/lib/api-client'
import type { InventorySummary, StockAdjustmentRequest } from '@/lib/inventory-types'
import { Button } from '@/components/ui/button'

// Owner-only stock adjustment dialog (inventory.adjust).
// Sends POST /inventory/adjustments with Idempotency-Key + If-Match
// derived from the selected row's updated_at (Z-form ETag).

export default function StockAdjustmentDialog({
  open,
  onClose,
  onNotice,
  onDone,
  products,
}: {
  open: boolean
  onClose: () => void
  onNotice: (msg: string) => void
  onDone: () => void
  products: InventorySummary[]
}) {
  const [productId, setProductId] = useState<number | ''>('')
  const [quantity, setQuantity] = useState('')
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setProductId('')
      setQuantity('')
      setReason('')
      setError(null)
      setSubmitting(false)
    }
  }, [open])

  const selected = products.find((p) => p.product_id === productId) ?? null

  const handleSubmit = async () => {
    if (productId === '') {
      setError('Select a product.')
      return
    }
    if (selected?.updated_at == null) {
      setError('This product has no ETag; refresh the list and try again.')
      return
    }
    const qty = Number(quantity)
    if (!Number.isFinite(qty) || qty === 0) {
      setError('Quantity must be a non-zero number.')
      return
    }
    if (!reason.trim()) {
      setError('A reason is required.')
      return
    }
    setSubmitting(true)
    setError(null)
    const body: StockAdjustmentRequest = {
      product_id: Number(productId),
      quantity: qty,
      reason: reason.trim(),
    }
    const ifMatch = `"${selected.updated_at}"`
    try {
      await api.post('/inventory/adjustments', body, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          'If-Match': ifMatch,
        },
      })
      onNotice('Stock adjustment saved')
      onDone()
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Adjustment failed'
      // 409/412: stale ETag or idempotency conflict — surface clearly.
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  if (!open) return null

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        role="dialog"
        aria-label="Stock adjustment"
        style={{ width: '100%', maxWidth: 460, background: 'white', borderRadius: 14, border: '1px solid var(--border)', boxShadow: '0 20px 48px rgba(0,0,0,.12)', display: 'flex', flexDirection: 'column', maxHeight: '90vh' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--foreground)' }}>Stock adjustment</div>
            <div style={{ fontSize: 12, color: 'var(--muted)' }}>Signed quantity · reason is logged in audit</div>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, color: 'var(--muted)' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14, overflowY: 'auto' }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
            <span style={{ color: 'var(--muted)' }}>Product</span>
            <select
              value={String(productId)}
              onChange={(e) => setProductId(e.target.value === '' ? '' : Number(e.target.value))}
              style={{ height: 38, borderRadius: 6, border: '1px solid var(--border)', padding: '0 10px', fontSize: 13 }}
            >
              <option value="">Select a product…</option>
              {products.map((p) => (
                <option key={p.product_id} value={p.product_id}>
                  {p.product_name || `#${p.product_id}`}{p.product_code ? ` (${p.product_code})` : ''}
                </option>
              ))}
            </select>
          </label>

          {selected && (
            <div style={{ fontSize: 12, color: 'var(--muted)', background: 'var(--background)', borderRadius: 8, padding: '8px 10px' }}>
              On hand: <strong>{Number(selected.on_hand_quantity).toLocaleString('id-ID')}</strong> · Value: <strong>{selected.inventory_value != null ? selected.inventory_value.toLocaleString('id-ID') : '—'}</strong>
            </div>
          )}

          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
            <span style={{ color: 'var(--muted)' }}>Quantity (positive = add, negative = remove)</span>
            <input
              type="number"
              step="any"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="e.g. 5 or -10"
              style={{ height: 38, borderRadius: 6, border: '1px solid var(--border)', padding: '0 10px', fontSize: 13 }}
            />
          </label>

          <label style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 }}>
            <span style={{ color: 'var(--muted)' }}>Reason</span>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Stock count correction"
              style={{ height: 38, borderRadius: 6, border: '1px solid var(--border)', padding: '0 10px', fontSize: 13 }}
            />
          </label>

          {error && (
            <div style={{ fontSize: 13, color: '#dc2626', background: '#fef2f2', borderRadius: 8, padding: '8px 10px' }}>
              {error}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '14px 20px', borderTop: '1px solid var(--border)' }}>
          <Button variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Saving…' : 'Save adjustment'}
          </Button>
        </div>
      </div>
    </div>
  )
}
