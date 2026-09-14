'use client'

import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Package,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Pagination, StockMovement, StockMovementListResponse } from '@/lib/inventory-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'

// ---------------------------------------------------------------------------
// Per-product stock history dialog.
// Calls GET /products/{id}/stock-movements (operationId getProductStockMovements).
// Capability: inventory.view (Owner + Staff).
// Mirrors the V0 "Stock history" dialog from build-inventory-management.zip:
//   history-list → paginated movement rows (date, trigger, qty, cost, ref).
//   history-row → 3-column grid (desktop) / 2-column (mobile).
//   history-pagination → centered prev/next.
// ---------------------------------------------------------------------------

const TRIGGER_LABELS: Record<string, string> = {
  purchase_receipt: 'Purchase / Receiving',
  sale: 'Sale',
  sales_return: 'Sales Return',
  purchase_return: 'Purchase Return',
  production_input: 'Production Input',
  production_output: 'Production Output',
  stock_adjustment: 'Stock Adjustment',
  opening_balance: 'Opening Balance',
  sale_reversal: 'Sale Reversal',
  purchase_reversal: 'Purchase Reversal',
  sales_return_reversal: 'Sales Return Reversal',
  purchase_return_reversal: 'Purchase Return Reversal',
  production_reversal: 'Production Reversal',
  adjustment_reversal: 'Adjustment Reversal',
  value_adjustment: 'Value Adjustment',
  production_input_reversal: 'Production Input Reversal',
  production_output_reversal: 'Production Output Reversal',
}

function triggerLabel(t: string): string {
  return TRIGGER_LABELS[t] ?? t.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function fmtDate(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('id-ID', { day: '2-digit', month: 'short', year: 'numeric' })
    + ', ' + d.toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
}

function refLabel(m: StockMovement): string {
  if (!m.reference_type && !m.reference_id) return '—'
  const type = m.reference_type
    ? m.reference_type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
    : ''
  // reference_id alone (no type) is a valid backend combo — no leading space.
  return m.reference_id ? `${type}${type ? ' ' : ''}#${m.reference_id}` : type
}

const DEFAULT_PAGINATION: Pagination = {
  page: 1, per_page: 10, total: 0, total_pages: 1, has_next: false, has_prev: false,
}

export default function StockHistoryDialog({
  open,
  onClose,
  productId,
  productName,
}: {
  open: boolean
  onClose: () => void
  productId: number | null
  productName: string
}) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [movements, setMovements] = useState<StockMovement[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)
  const [page, setPage] = useState(1)
  const perPage = 8 // V0 history dialog page size

  const fetch_ = useCallback(async () => {
    if (!productId) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<StockMovementListResponse>(
        `/products/${productId}/stock-movements`,
        { params: { page: String(page), per_page: String(perPage) } },
      )
      setMovements(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history')
    } finally {
      setLoading(false)
    }
  }, [productId, page])

  // Parent remounts this dialog per product (key=product_id), so `page`
  // always starts at 1 — no reset effect needed here.
  useEffect(() => {
    if (open && productId) fetch_()
  }, [open, productId, fetch_])

  if (!open || !productId) return null

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'grid', placeItems: 'center', background: 'rgba(28,47,70,.32)', padding: 18 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        role="dialog"
        aria-label="Stock history"
        style={{ width: 'min(680px, 100%)', maxHeight: '90vh', overflow: 'auto', background: '#fff', border: '1px solid var(--border)', borderRadius: 9, boxShadow: '0 20px 60px rgba(31,58,90,.2)' }}
      >
        {/* Header — V0 dialog-head */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '19px 22px', borderBottom: '1px solid var(--border)' }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700 }}>Stock history</h2>
            <div style={{ color: '#8997a7', fontSize: 12, marginTop: 4 }}>{productName}</div>
          </div>
          <button onClick={onClose} style={{ border: 0, background: 'transparent', color: '#789', cursor: 'pointer', padding: 4 }} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        {/* Body — V0 history-list */}
        <div style={{ padding: '20px 22px', maxHeight: 'min(58vh, 520px)', overflowY: 'auto', overscrollBehavior: 'contain' }}>
          {loading ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {[1, 2, 3, 4].map((i) => (
                <div key={i} style={{ height: 48, borderRadius: 6, background: '#f0f4f9', animation: 'pulse 1.5s infinite' }} />
              ))}
            </div>
          ) : error ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '32px 0', color: '#9aa7b8' }}>
              <AlertTriangle size={28} />
              <strong style={{ color: 'var(--foreground)' }}>Failed to load history</strong>
              <p style={{ margin: 0, fontSize: 13 }}>{error}</p>
              <Button variant="outline" size="sm" onClick={fetch_}>Retry</Button>
            </div>
          ) : movements.length === 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '32px 0', color: '#9aa7b8' }}>
              <Package size={28} />
              <strong style={{ color: 'var(--foreground)' }}>No movements recorded</strong>
              <p style={{ margin: 0, fontSize: 13 }}>This item has no stock history yet.</p>
            </div>
          ) : (
            <>
              <p style={{ margin: '0 0 8px', color: '#8997a7', fontSize: 11 }}>
                {pagination.total} movement{pagination.total !== 1 ? 's' : ''} · Showing {(page - 1) * perPage + 1}–{Math.min(page * perPage, pagination.total)}
              </p>

              {/* Desktop table */}
              <div style={{ overflowX: 'auto' }} className="history-desktop">
                <table style={{ width: '100%', minWidth: 580, textAlign: 'left', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead style={{ color: '#8997a7', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    <tr>
                      <th style={{ padding: '8px 0' }}>Date</th>
                      <th style={{ padding: '8px 0' }}>Type</th>
                      <th style={{ padding: '8px 0', textAlign: 'right' }}>Qty</th>
                      <th style={{ padding: '8px 0', textAlign: 'right' }}>Unit cost</th>
                      <th style={{ padding: '8px 0' }}>Reference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {movements.map((m) => (
                      <tr key={m.id} style={{ borderBottom: '1px solid #edf1f5' }}>
                        <td style={{ padding: '12px 0' }}>
                          <div>{fmtDate(m.movement_date)}</div>
                        </td>
                        <td style={{ padding: '12px 0' }}>
                          <strong style={{ fontWeight: 600 }}>{triggerLabel(m.trigger)}</strong>
                        </td>
                        <td style={{ padding: '12px 0', textAlign: 'right', fontWeight: 600, color: m.quantity > 0 ? '#059669' : m.quantity < 0 ? '#dc2626' : '#64748b' }}>
                          {m.quantity > 0 ? '+' : ''}{Number(m.quantity).toLocaleString('id-ID')}
                        </td>
                        <td style={{ padding: '12px 0', textAlign: 'right' }}>
                          {m.unit_cost_at_movement != null ? formatIDR(m.unit_cost_at_movement) : '—'}
                        </td>
                        <td style={{ padding: '12px 0', color: '#8b99a9', fontSize: 11 }}>
                          {refLabel(m)}
                          {m.reason && <div style={{ marginTop: 2, fontStyle: 'italic' }}>{m.reason}</div>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile cards */}
              <div className="history-mobile" style={{ display: 'none', flexDirection: 'column', gap: 8 }}>
                {movements.map((m) => (
                  <div key={m.id} style={{ border: '1px solid var(--border)', borderRadius: 7, padding: 12, background: '#fff' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <strong style={{ fontSize: 12 }}>{triggerLabel(m.trigger)}</strong>
                      <span style={{ fontWeight: 600, fontSize: 13, color: m.quantity > 0 ? '#059669' : m.quantity < 0 ? '#dc2626' : '#64748b' }}>
                        {m.quantity > 0 ? '+' : ''}{Number(m.quantity).toLocaleString('id-ID')}
                      </span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 8, fontSize: 11, color: '#8b99a9' }}>
                      <span>{fmtDate(m.movement_date)}</span>
                      <span>{refLabel(m)}</span>
                      <span>Cost: {m.unit_cost_at_movement != null ? formatIDR(m.unit_cost_at_movement) : '—'}</span>
                      {m.reason && <span style={{ fontStyle: 'italic' }}>{m.reason}</span>}
                    </div>
                  </div>
                ))}
              </div>

              {/* Pagination — V0 history-pagination */}
              {pagination.total_pages > 1 && (
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 14, paddingTop: 16, color: '#8290a3', fontSize: 11 }}>
                  <button
                    disabled={page <= 1}
                    onClick={() => setPage((p) => p - 1)}
                    aria-label="Previous movement page"
                    style={{ display: 'grid', placeItems: 'center', width: 28, height: 28, border: '1px solid var(--border)', borderRadius: 5, background: '#fff', color: '#5e7692', cursor: page <= 1 ? 'not-allowed' : 'pointer', opacity: page <= 1 ? 0.5 : 1 }}
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <span>Page {page} of {pagination.total_pages}</span>
                  <button
                    disabled={page >= pagination.total_pages}
                    onClick={() => setPage((p) => p + 1)}
                    aria-label="Next movement page"
                    style={{ display: 'grid', placeItems: 'center', width: 28, height: 28, border: '1px solid var(--border)', borderRadius: 5, background: '#fff', color: '#5e7692', cursor: page >= pagination.total_pages ? 'not-allowed' : 'pointer', opacity: page >= pagination.total_pages ? 0.5 : 1 }}
                  >
                    <ChevronRight size={14} />
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
        @media(max-width:620px) {
          .history-desktop { display:none!important; }
          .history-mobile { display:flex!important; }
        }
      `}</style>
    </div>
  )
}
