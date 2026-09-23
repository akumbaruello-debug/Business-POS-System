'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Plus,
  RefreshCw,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Product } from '@/lib/product-types'
import type {
  PaymentMethod,
  Sale,
  SaleLine,
  SalePayment,
  SalePaymentInput,
  SaleReturnRequest,
  SalesReturn,
  RefundRequest,
} from '@/lib/sale-types'
import {
  fmtDate,
  fmtDateTime,
  isCancellable,
  isPayable,
  isReturnable,
  num,
  sanitizeEtag,
} from '@/lib/sale-ui'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { LifecycleBadge, PaymentBadge } from '@/components/sales/sale-badges'
import { CancelSaleDialog, DeleteDraftDialog, PostSaleDialog, SalesDialog } from '@/components/sales/sale-dialogs'

// -----------------------------------------------------------------------------
// Backend wiring for /sales/{id} detail:
//
//   GET    /sales/{id}                    Sale                       (sale.view)
//   GET    /sales/{id}/lines              SaleLine[]    (bare array)  (sale.view)
//   GET    /sales/{id}/payments           SalePayment[] (bare array)  (sale.view)
//   GET    /sales/{id}/returns            SalesReturn[] (bare array)  (sale.view)
//
//   POST   /sales/{id}/post               draft → posted              (sale.post)
//   POST   /sales/{id}/cancel             non-draft → cancelled       (sale.cancel)
//   POST   /sales/{id}/payments           add payment                 (sale.create)
//   POST   /sales/{id}/returns            create partial/full return  (sale.return)
//
// Lifecycle guards mirror the backend service constants exactly
// (backend/app/services/sales.py):
//   _LIFECYCLE_CANCELLABLE = {posted, completed, partially_returned, returned}
//       → `draft` is deliberately absent: a draft is DELETED, not cancelled.
//   _LIFECYCLE_RETURNABLE  = {posted, completed, partially_returned}
//   payment                → any non-draft sale the backend accepts.
//
// The `payment_state` filter gap noted on the list page does not affect this
// page: `payment_state` / `paid_amount` / `outstanding` are all server-derived
// values on the sale envelope.
// -----------------------------------------------------------------------------

const RETURN_REASON_MAX = 1000

export default function SaleDetailPage() {
  const params = useParams<{ id: string }>()
  const rawId = params?.id ?? ''
  const saleId = Number(rawId)
  const router = useRouter()
  const user = useSession()

  const canView = user.capabilities.includes('sale.view')
  const canPost = user.capabilities.includes('sale.post')
  const canCancel = user.capabilities.includes('sale.cancel')
  const canPay = user.capabilities.includes('sale.create') // add-payment route uses sale.create
  const canReturn = user.capabilities.includes('sale.return')
  const canDeleteDraft = user.capabilities.includes('sale.create')
  const canPriceOverride = user.capabilities.includes('sale.price_override')
  const canDiscount = user.capabilities.includes('sale.discount')

  const [sale, setSale] = useState<Sale | null>(null)
  const [etag, setEtag] = useState<string | null>(null)
  const [lines, setLines] = useState<SaleLine[]>([])
  const [payments, setPayments] = useState<SalePayment[]>([])
  const [returns, setReturns] = useState<SalesReturn[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)

  // Lookup support data for display
  const [productMap, setProductMap] = useState<Map<number, string>>(new Map())
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])

  // Dialogs
  const [postOpen, setPostOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [payOpen, setPayOpen] = useState(false)
  const [returnOpen, setReturnOpen] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)

  // Add-payment form
  const [payMethod, setPayMethod] = useState<number | ''>('')
  const [payAmount, setPayAmount] = useState('')
  const [payReference, setPayReference] = useState('')

  // Create-return form
  const [returnReason, setReturnReason] = useState('')
  const [returnQuantities, setReturnQuantities] = useState<Record<number, number>>({})

  // Draft line editing
  const [products, setProducts] = useState<Product[]>([])
  const [editingLineId, setEditingLineId] = useState<number | null>(null)
  const [editQty, setEditQty] = useState('')
  const [editPrice, setEditPrice] = useState('')
  const [editDiscount, setEditDiscount] = useState('')
  const [addLineOpen, setAddLineOpen] = useState(false)
  const [addProductId, setAddProductId] = useState('')
  const [addQty, setAddQty] = useState('1')
  const [addPrice, setAddPrice] = useState('')
  const [addDiscount, setAddDiscount] = useState('')
  const [lineError, setLineError] = useState<string | null>(null)

  const toast = useCallback((text: string, isError = false) => {
    setNotice({ text, error: isError })
    window.setTimeout(() => setNotice(null), 3200)
  }, [])

  const deriveEtag = useCallback((data: Sale, headerEtag: string | null): string => {
    const fromHeader = sanitizeEtag(headerEtag)
    if (fromHeader) return fromHeader
    if (data.etag) return sanitizeEtag(data.etag)
    if (data.updated_at) return `"${data.updated_at}"`
    return ''
  }, [])

  const fetchSale = useCallback(async () => {
    if (!Number.isFinite(saleId) || saleId <= 0) {
      setLoading(false)
      setNotFound(true)
      return
    }
    if (!canView) {
      setLoading(false)
      setError('Missing capability: sale.view')
      return
    }
    setLoading(true)
    setError(null)
    setNotFound(false)
    try {
      const [saleRes, linesRes, payRes, retRes] = await Promise.all([
        api.headers.get<Sale>(`/sales/${saleId}`),
        api.get<SaleLine[]>(`/sales/${saleId}/lines`),
        api.get<SalePayment[]>(`/sales/${saleId}/payments`),
        api.get<SalesReturn[]>(`/sales/${saleId}/returns`),
      ])
      const data = saleRes.data
      setSale(data)
      setEtag(deriveEtag(data, saleRes.headers.get('ETag')))
      setLines(Array.isArray(linesRes) ? linesRes : [])
      setPayments(Array.isArray(payRes) ? payRes : [])
      setReturns(Array.isArray(retRes) ? retRes : [])
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load sale'
      const code = (err as Error & { code?: string }).code
      if (code === 'not_found' || /not found|404/i.test(msg)) {
        setNotFound(true)
        setSale(null)
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }, [saleId, canView, deriveEtag])

  useEffect(() => {
    fetchSale()
  }, [fetchSale])

  // Product names for line items (best-effort map, never blocks the page).
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.get<{ data: Product[] }>('/products', {
          params: { per_page: '500' },
        })
        if (!cancelled) {
          const list = Array.isArray(res.data) ? res.data : []
          setProducts(list)
          const m = new Map<number, string>()
          list.forEach((p) => m.set(p.id, p.name))
          setProductMap(m)
        }
      } catch {
        /* best-effort */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Payment method names for the payments table + record-payment form.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.get<{ data: PaymentMethod[] }>('/payment-methods', {
          params: { per_page: '500' },
        })
        if (!cancelled) setPaymentMethods(res.data ?? [])
      } catch {
        /* best-effort */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // Customer: prefer the server-enriched contact name from the sale envelope
  // (G4); fall back to the id, then to walk-in. No phone/city is invented.
  const customerDisplayName = useMemo(() => {
    if (!sale) return '—'
    if (sale.customer?.name) return sale.customer.name
    if (sale.customer_id) return `Customer #${sale.customer_id}`
    return 'Walk-in customer'
  }, [sale])

  const customerContact = sale?.customer?.phone ?? sale?.customer?.email ?? null

  // ---------------------------------------------------------------------------
  // Lifecycle guards — mirror the service constants
  // ---------------------------------------------------------------------------
  const status = sale?.lifecycle_status ?? ''
  const postable = canPost && status === 'draft'
  const deletable = canDeleteDraft && status === 'draft'
  const cancellable = canCancel && isCancellable(status)
  const payable = canPay && isPayable(status) && num(sale?.outstanding) > 0
  const returnable = canReturn && isReturnable(status)

  // ---------------------------------------------------------------------------
  // Mutations
  // ---------------------------------------------------------------------------

  const handleConflict = useCallback(async () => {
    setConflict('This sale changed on the server. Loading the latest version.')
    await fetchSale()
  }, [fetchSale])

  const runMutation = async (
    fn: () => Promise<unknown>,
    okMessage: string,
    onSuccess?: () => void,
  ) => {
    if (busy) return
    setBusy(true)
    setDialogError(null)
    setConflict(null)
    try {
      await fn()
      toast(okMessage)
      onSuccess?.()
      await fetchSale()
    } catch (e) {
      const code = (e as Error & { code?: string }).code
      const msg = e instanceof Error ? e.message : 'The operation failed'
      if (code === 'version_mismatch' || code === 'precondition_failed') {
        await handleConflict()
        setDialogError(null)
      } else {
        setDialogError(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  const mutationHeaders = () => ({
    'Idempotency-Key': crypto.randomUUID(),
    ...(etag ? { 'If-Match': etag } : {}),
  })

  const handlePost = () =>
    runMutation(
      () => api.headers.post(`/sales/${saleId}/post`, {}, { headers: mutationHeaders() }),
      'Sale posted',
      () => setPostOpen(false),
    )

  const handleCancel = (reason: string) =>
    runMutation(
      () => api.headers.post(`/sales/${saleId}/cancel`, { reason }, { headers: mutationHeaders() }),
      'Sale cancelled',
      () => setCancelOpen(false),
    )

  const handleDeleteDraft = () =>
    runMutation(
      () => api.headers.post(`/sales/${saleId}`, {}, { headers: mutationHeaders() }),
      'Draft deleted',
      () => {
        setDeleteOpen(false)
        router.push('/sales')
      },
    )

  const handlePayment = () => {
    if (!sale || payMethod === '' || !payAmount) return
    const body: SalePaymentInput = {
      payment_method_id: Number(payMethod),
      amount: Number(payAmount),
      reference: payReference.trim() || null,
    }
    return runMutation(
      () => api.headers.post(`/sales/${saleId}/payments`, body, { headers: mutationHeaders() }),
      'Payment recorded',
      () => {
        setPayOpen(false)
        setPayMethod('')
        setPayAmount('')
        setPayReference('')
      },
    )
  }

  const canRefund = user.capabilities.includes('sale.refund')

  const [refundOpen, setRefundOpen] = useState(false)
  const [refundMethod, setRefundMethod] = useState<number | ''>('')
  const [refundAmount, setRefundAmount] = useState('')
  const [refundReason, setRefundReason] = useState('')
  const [refundDate, setRefundDate] = useState('')

  const crl = num(sale?.crl)
  const refundable = canRefund && crl > 0

  const handleRefund = () => {
    if (!sale || refundMethod === '' || !refundAmount || Number(refundAmount) <= 0) return
    const body: RefundRequest = {
      sale_id: saleId,
      amount: Number(refundAmount),
      payment_method_id: Number(refundMethod),
      reason: refundReason.trim() || null,
      refund_date: refundDate ? `${refundDate}T00:00:00Z` : undefined,
    }
    return runMutation(
      () => api.headers.post('/refunds', body, { headers: mutationHeaders() }),
      'Refund recorded',
      () => {
        setRefundOpen(false)
        setRefundMethod('')
        setRefundAmount('')
        setRefundReason('')
        setRefundDate('')
      },
    )
  }

  const handleReturn = () => {
    const linesPayload = Object.entries(returnQuantities)
      .filter(([, qty]) => Number(qty) > 0)
      .map(([lineId, qty]) => ({ sale_line_id: Number(lineId), quantity: Number(qty) }))
    if (!returnReason.trim() || linesPayload.length === 0) {
      setDialogError('A reason and at least one line quantity are required.')
      return
    }
    const body: SaleReturnRequest = {
      reason: returnReason.trim(),
      lines: linesPayload,
    }
    return runMutation(
      () => api.headers.post(`/sales/${saleId}/returns`, body, { headers: mutationHeaders() }),
      'Return recorded',
      () => {
        setReturnOpen(false)
        setReturnReason('')
        setReturnQuantities({})
      },
    )
  }

  // ---------------------------------------------------------------------------
  // Draft line CRUD (only available while lifecycle_status === 'draft')
  // ---------------------------------------------------------------------------

  const startEditLine = (line: SaleLine) => {
    setEditingLineId(line.id)
    setEditQty(String(line.quantity))
    setEditPrice(String(line.unit_price))
    setEditDiscount(String(line.discount_amount))
    setLineError(null)
  }

  const cancelEditLine = () => {
    setEditingLineId(null)
    setEditQty('')
    setEditPrice('')
    setEditDiscount('')
    setLineError(null)
  }

  const saveEditLine = async () => {
    if (!sale || editingLineId == null) return
    const body: Record<string, number> = {}
    if (editQty.trim() !== '') body.quantity = Number(editQty)
    if (canPriceOverride && editPrice.trim() !== '') body.unit_price = Number(editPrice)
    if (canDiscount && editDiscount.trim() !== '') body.discount_amount = Number(editDiscount)
    if (Object.keys(body).length === 0) {
      cancelEditLine()
      return
    }
    setBusy(true)
    setLineError(null)
    try {
      await api.patch(`/sales/${saleId}/lines/${editingLineId}`, body, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          ...(etag ? { 'If-Match': etag } : {}),
        },
      })
      await fetchSale()
      cancelEditLine()
    } catch (e) {
      setLineError(e instanceof Error ? e.message : 'Failed to update line')
    } finally {
      setBusy(false)
    }
  }

  const deleteLine = async (lineId: number) => {
    if (!sale) return
    setBusy(true)
    setLineError(null)
    try {
      await api.delete(`/sales/${saleId}/lines/${lineId}`, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          ...(etag ? { 'If-Match': etag } : {}),
        },
      })
      await fetchSale()
    } catch (e) {
      setLineError(e instanceof Error ? e.message : 'Failed to delete line')
    } finally {
      setBusy(false)
    }
  }

  const selectedAddProduct = products.find((p) => String(p.id) === addProductId)
  const effectiveAddPrice =
    addPrice.trim() === '' ? selectedAddProduct?.selling_price ?? 0 : Number(addPrice)

  const submitAddLine = async () => {
    if (!sale || !addProductId || Number(addQty) <= 0 || effectiveAddPrice < 0) {
      setLineError('Select a product and enter a positive quantity.')
      return
    }
    setBusy(true)
    setLineError(null)
    try {
      await api.post(`/sales/${saleId}/lines`, {
        product_id: Number(addProductId),
        quantity: Number(addQty),
        unit_price: effectiveAddPrice,
        discount_amount: canDiscount && addDiscount ? Number(addDiscount) : 0,
      }, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          ...(etag ? { 'If-Match': etag } : {}),
        },
      })
      setAddLineOpen(false)
      setAddProductId('')
      setAddQty('1')
      setAddPrice('')
      setAddDiscount('')
      await fetchSale()
    } catch (e) {
      setLineError(e instanceof Error ? e.message : 'Failed to add line')
    } finally {
      setBusy(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Derived display values (all from real server fields)
  // ---------------------------------------------------------------------------
  const totalAmount = num(sale?.total_amount)
  const discountAmount = num(sale?.discount_amount)
  const paidAmount = num(sale?.paid_amount)
  const outstanding = num(sale?.outstanding)
  // Lines are gross of the sale-level discount; showing the sum lets the
  // totals stack add up honestly instead of implying a tax figure we lack.
  const lineSubtotal = useMemo(
    () => lines.reduce((n, l) => n + num(l.line_total), 0),
    [lines],
  )
  const paymentMethodName = (id: number) =>
    paymentMethods.find((m) => m.id === id)?.name ?? `Method #${id}`

  // ---------------------------------------------------------------------------
  // Render — loading / not-found / error
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div className="content" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div className="skeleton" style={{ height: 20, width: 140 }} />
        <div className="skeleton" style={{ height: 132, borderRadius: 12 }} />
        <div className="skeleton" style={{ height: 260, borderRadius: 12 }} />
        <div className="skeleton" style={{ height: 200, borderRadius: 12 }} />
      </div>
    )
  }

  if (notFound) {
    return (
      <div className="content">
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={28} />
          </div>
          <strong>Sale not found</strong>
          <p>We couldn&apos;t find a sale with id {rawId}.</p>
          <Button variant="outline" onClick={() => router.push('/sales')}>
            <ArrowLeft size={14} />
            Back to sales
          </Button>
        </div>
      </div>
    )
  }

  if (error || !sale) {
    return (
      <div className="content">
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={28} />
          </div>
          <strong>Failed to load sale</strong>
          <p>{error ?? 'Unknown error'}</p>
          <Button variant="outline" onClick={fetchSale}>
            <RefreshCw size={14} />
            Try again
          </Button>
        </div>
      </div>
    )
  }

  const actionsAvailable = postable || deletable || payable || returnable || cancellable

  return (
    <div className="content" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Toast */}
      {notice && (
        <div className={`sales-toast${notice.error ? ' error' : ''}`} role="status">
          {notice.error ? <AlertTriangle size={15} /> : <Check size={15} />}
          {notice.text}
        </div>
      )}

      <Link
        href="/sales"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          width: 'fit-content',
          color: '#718198',
          fontSize: 12,
          fontWeight: 600,
          textDecoration: 'none',
        }}
      >
        <ArrowLeft size={14} />
        Back to sales
      </Link>

      {/* Conflict banner */}
      {conflict && (
        <div className="sales-dialog-warn" style={{ marginBottom: 0 }}>
          <AlertTriangle size={16} />
          <span style={{ flex: 1 }}>{conflict}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setConflict(null)}
            style={{ background: 'none', border: 0, cursor: 'pointer', color: 'inherit' }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      {/* 1 — Header / context */}
      <section className="sales-card">
        <div className="sales-detail-head">
          <div>
            <div className="eyebrow">Sale detail</div>
            <div className="sales-detail-title">
              <h1>{sale.reference_no ?? `Sale #${sale.id}`}</h1>
              <LifecycleBadge status={sale.lifecycle_status} />
              <PaymentBadge state={sale.payment_state} />
            </div>
            <div className="sales-detail-sub">
              <span>{customerDisplayName}</span>
              {customerContact && (
                <>
                  <span aria-hidden="true">·</span>
                  <span>{customerContact}</span>
                </>
              )}
              <span aria-hidden="true">·</span>
              <span>Sale {fmtDate(sale.sale_date)}</span>
            </div>
          </div>
          <div className="sales-section-actions">
            <Button variant="outline" onClick={fetchSale} disabled={busy}>
              <RefreshCw size={14} />
              Refresh
            </Button>
          </div>
        </div>
      </section>

      {/* 2 — Sale information */}
      <section className="sales-card">
        <div className="panel-header">
          <div>
            <h2>Sale information</h2>
            <p>Header fields recorded on this transaction.</p>
          </div>
        </div>
        <div className="sales-detail-grid">
          <StatBlock label="Customer" value={customerDisplayName} />
          <StatBlock label="Sale date" value={fmtDate(sale.sale_date)} />
          <StatBlock
            label="Lifecycle"
            value={<LifecycleBadge status={sale.lifecycle_status} />}
          />
          <StatBlock label="Payment state" value={<PaymentBadge state={sale.payment_state} />} />
          <StatBlock label="Created" value={fmtDateTime(sale.created_at)} />
          <StatBlock label="Last updated" value={fmtDateTime(sale.updated_at)} />
          <StatBlock label="Posted at" value={fmtDateTime(sale.posted_at)} muted />
          <StatBlock label="Version" value={String(sale.version)} muted />
        </div>
        {sale.notes && (
          <div
            style={{
              padding: '14px 18px',
              borderTop: '1px solid var(--border)',
              fontSize: 13,
              color: '#4b5c72',
            }}
          >
            <span className="sales-field-label" style={{ display: 'block', marginBottom: 4 }}>
              Notes
            </span>
            {sale.notes}
          </div>
        )}
        {sale.cancellation_reason && (
          <div
            style={{
              padding: '14px 18px',
              borderTop: '1px solid var(--border)',
              fontSize: 13,
              color: '#b91c1c',
            }}
          >
            <span className="sales-field-label" style={{ display: 'block', marginBottom: 4 }}>
              Cancellation
            </span>
            {sale.cancellation_reason}
            {sale.cancellation_date && (
              <span className="sales-muted"> · {fmtDateTime(sale.cancellation_date)}</span>
            )}
          </div>
        )}
      </section>

      {/* 3 — Line items */}
      <section className="sales-card">
        <div className="panel-header">
          <div>
            <h2>Line items</h2>
            <p>
              {lines.length} line{lines.length === 1 ? '' : 's'} on this sale
            </p>
          </div>
          {status === 'draft' && (
            <Button
              variant="outline"
              onClick={() => {
                setLineError(null)
                setAddLineOpen(true)
              }}
              disabled={busy || addLineOpen}
            >
              <Plus size={14} />
              Add line
            </Button>
          )}
        </div>

        {lineError && (
          <div className="sales-dialog-warn" style={{ margin: '0 18px 12px' }}>
            <AlertTriangle size={16} />
            <span>{lineError}</span>
          </div>
        )}

        {addLineOpen && (
          <div
            style={{
              padding: '14px 18px',
              borderBottom: '1px solid var(--border)',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr auto', gap: 12 }}>
              <label className="sales-field">
                <span className="sales-field-label">Product</span>
                <select
                  className="sales-select"
                  value={addProductId}
                  onChange={(e) => {
                    setAddProductId(e.target.value)
                    const p = products.find((x) => String(x.id) === e.target.value)
                    if (p && addPrice === '') setAddPrice(String(p.selling_price))
                  }}
                  disabled={busy}
                >
                  <option value="">Select…</option>
                  {products
                    .filter((p) => p.is_sellable && p.is_active)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </label>
              <label className="sales-field">
                <span className="sales-field-label">Qty</span>
                <input
                  className="sales-input"
                  type="number"
                  inputMode="decimal"
                  step="any"
                  min={0}
                  value={addQty}
                  onChange={(e) => setAddQty(e.target.value)}
                  disabled={busy}
                />
              </label>
              <label className="sales-field">
                <span className="sales-field-label">Unit price</span>
                <input
                  className="sales-input"
                  type="number"
                  inputMode="decimal"
                  step="any"
                  min={0}
                  value={addPrice}
                  onChange={(e) => setAddPrice(e.target.value)}
                  disabled={busy || !canPriceOverride}
                />
              </label>
              {canDiscount && (
                <label className="sales-field">
                  <span className="sales-field-label">Discount</span>
                  <input
                    className="sales-input"
                    type="number"
                    inputMode="decimal"
                    step="any"
                    min={0}
                    value={addDiscount}
                    onChange={(e) => setAddDiscount(e.target.value)}
                    disabled={busy}
                  />
                </label>
              )}
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
                <Button size="sm" onClick={submitAddLine} disabled={busy || !addProductId}>
                  Save
                </Button>
                <Button size="sm" variant="outline" onClick={() => setAddLineOpen(false)} disabled={busy}>
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        )}

        {lines.length === 0 && !addLineOpen ? (
          <div className="empty-workspace" style={{ padding: '40px 24px' }}>
            <div className="empty-icon">
              <Plus size={22} />
            </div>
            <strong>No line items</strong>
            <p>This sale does not have any lines recorded.</p>
          </div>
        ) : (
          <div className="sales-table-wrap">
            <table className="sales-table" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th style={{ width: 44 }}>#</th>
                  <th>Product</th>
                  <th className="sales-th-right">Qty</th>
                  <th className="sales-th-right">Unit price</th>
                  <th className="sales-th-right">Discount</th>
                  <th className="sales-th-right">Line total</th>
                  {status === 'draft' && <th className="sales-th-actions"><span className="sr-only">Edit</span></th>}
                </tr>
              </thead>
              <tbody>
                {lines.map((l) => {
                  const editing = editingLineId === l.id
                  return (
                    <tr key={l.id}>
                      <td className="sales-muted">{l.line_number}</td>
                      <td>
                        <span style={{ fontWeight: 600 }}>
                          {productMap.get(l.product_id) ?? `Product #${l.product_id}`}
                        </span>
                        {l.is_negative_stock_fallback && (
                          <span className="sales-inline-badge">negative-stock fallback</span>
                        )}
                      </td>
                      {editing ? (
                        <>
                          <td className="sales-td-right">
                            <input
                              className="sales-input"
                              type="number"
                              inputMode="decimal"
                              step="any"
                              min={0}
                              value={editQty}
                              onChange={(e) => setEditQty(e.target.value)}
                              disabled={busy}
                              style={{ width: 90 }}
                            />
                          </td>
                          <td className="sales-td-right">
                            <input
                              className="sales-input"
                              type="number"
                              inputMode="decimal"
                              step="any"
                              min={0}
                              value={editPrice}
                              onChange={(e) => setEditPrice(e.target.value)}
                              disabled={busy || !canPriceOverride}
                              style={{ width: 120 }}
                            />
                          </td>
                          <td className="sales-td-right">
                            {canDiscount ? (
                              <input
                                className="sales-input"
                                type="number"
                                inputMode="decimal"
                                step="any"
                                min={0}
                                value={editDiscount}
                                onChange={(e) => setEditDiscount(e.target.value)}
                                disabled={busy}
                                style={{ width: 110 }}
                              />
                            ) : (
                              formatIDR(num(l.discount_amount))
                            )}
                          </td>
                          <td className="sales-td-right sales-num">
                            {formatIDR(num(l.line_total))}
                          </td>
                          <td className="sales-td-actions">
                            <div style={{ display: 'flex', gap: 8 }}>
                              <Button size="sm" onClick={saveEditLine} disabled={busy}>
                                Save
                              </Button>
                              <Button size="sm" variant="outline" onClick={cancelEditLine} disabled={busy}>
                                Cancel
                              </Button>
                            </div>
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="sales-td-right" style={{ fontVariantNumeric: 'tabular-nums' }}>
                            {l.quantity}
                          </td>
                          <td className="sales-td-right sales-num">{formatIDR(num(l.unit_price))}</td>
                          <td className="sales-td-right sales-num">{formatIDR(num(l.discount_amount))}</td>
                          <td className="sales-td-right sales-num">{formatIDR(num(l.line_total))}</td>
                          {status === 'draft' && (
                            <td className="sales-td-actions">
                              <div style={{ display: 'flex', gap: 8 }}>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => startEditLine(l)}
                                  disabled={busy}
                                >
                                  Edit
                                </Button>
                                <Button
                                  size="sm"
                                  variant="destructive"
                                  onClick={() => deleteLine(l.id)}
                                  disabled={busy}
                                >
                                  Delete
                                </Button>
                              </div>
                            </td>
                          )}
                        </>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* 4 — Totals (built only from real contract fields) */}
        {lines.length > 0 && (
          <div
            style={{ borderTop: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}
          >
            <div className="sales-totals">
              <div className="sales-total-row">
                <span>Line subtotal</span>
                <span>{formatIDR(lineSubtotal)}</span>
              </div>
              {discountAmount > 0 && (
                <div className="sales-total-row">
                  <span>Sale discount</span>
                  <span>−{formatIDR(discountAmount)}</span>
                </div>
              )}
              <div className="sales-total-row grand">
                <span>Total</span>
                <span>{formatIDR(totalAmount)}</span>
              </div>
              <div className="sales-total-row">
                <span>Paid</span>
                <span>{formatIDR(paidAmount)}</span>
              </div>
              <div
                className={`sales-total-row ${outstanding > 0 ? 'due' : 'settled'}`}
              >
                <span>Outstanding</span>
                <span>{formatIDR(outstanding)}</span>
              </div>
            </div>
          </div>
        )}
      </section>

      {/* 5 — Payments */}
      <section className="sales-card">
        <div className="panel-header">
          <div>
            <h2>Payments</h2>
            <p>
              {payments.length} payment{payments.length === 1 ? '' : 's'} recorded
            </p>
          </div>
          {payable && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setDialogError(null)
                setPayOpen(true)
              }}
              disabled={busy}
            >
              <Plus size={14} />
              Record payment
            </Button>
          )}
        </div>
        {refundable && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 14px',
              borderTop: '1px solid var(--border)',
              fontSize: 13,
            }}
          >
            <span>Refundable balance: <strong>{formatIDR(crl)}</strong></span>
            <Button
              size="sm"
              onClick={() => {
                setDialogError(null)
                setRefundOpen(true)
              }}
              disabled={busy}
            >
              Issue refund
            </Button>
          </div>
        )}
        {payments.length === 0 ? (
          <div className="empty-workspace" style={{ padding: '40px 24px' }}>
            <strong>No payments yet</strong>
            <p>
              {payable
                ? 'Record a payment to settle this sale.'
                : 'This sale has no recorded payments.'}
            </p>
          </div>
        ) : (
          <div className="sales-table-wrap">
            <table className="sales-table" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Method</th>
                  <th>Reference</th>
                  <th className="sales-th-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p) => (
                  <tr key={p.id}>
                    <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(p.payment_date)}</td>
                    <td>{paymentMethodName(p.payment_method_id)}</td>
                    <td className="sales-muted">{p.reference ?? '—'}</td>
                    <td className="sales-td-right sales-num">{formatIDR(num(p.amount))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 6 — Returns */}
      <section className="sales-card">
        <div className="panel-header">
          <div>
            <h2>Returns</h2>
            <p>
              {returns.length} return{returns.length === 1 ? '' : 's'} recorded
            </p>
          </div>
          {returnable && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setDialogError(null)
                setReturnOpen(true)
              }}
              disabled={busy}
            >
              <Plus size={14} />
              Create return
            </Button>
          )}
        </div>
        {returns.length === 0 ? (
          <div className="empty-workspace" style={{ padding: '40px 24px' }}>
            <strong>No returns</strong>
            <p>
              {returnable
                ? 'Nothing from this sale has been returned.'
                : `Returns are not available while the sale is ${sale.lifecycle_status.replace('_', ' ')}.`}
            </p>
          </div>
        ) : (
          <div>
            {returns.map((r) => (
              <div
                key={r.id}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                  padding: '14px 18px',
                  borderTop: '1px solid #f0f4f9',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <strong style={{ fontSize: 13 }}>
                    Return #{r.id}
                    {r.lifecycle_status === 'cancelled' && (
                      <span className="sales-inline-badge">cancelled</span>
                    )}
                  </strong>
                  <span className="sales-muted" style={{ fontSize: 12 }}>
                    {fmtDate(r.return_date)}
                  </span>
                </div>
                {r.reason && (
                  <div style={{ fontSize: 12, color: '#4b5c72' }}>Reason: {r.reason}</div>
                )}
                <div
                  style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 18,
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  <span>Value returned: {formatIDR(num(r.total_selling_price_returned))}</span>
                  <span className="sales-muted" style={{ fontWeight: 500 }}>
                    Cost restored: {formatIDR(num(r.total_cost_returned))}
                  </span>
                  <span className="sales-muted" style={{ fontWeight: 500 }}>
                    {r.lines?.length ?? 0} line{(r.lines?.length ?? 0) === 1 ? '' : 's'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 7 — Primary actions (deliberate footer strip, not crammed into the title row) */}
      {actionsAvailable && (
        <section className="sales-card">
          <div className="sales-detail-actions">
            {postable && (
              <Button
                onClick={() => {
                  setDialogError(null)
                  setPostOpen(true)
                }}
                disabled={busy}
              >
                Post sale
              </Button>
            )}
            {returnable && (
              <Button
                variant="outline"
                onClick={() => {
                  setDialogError(null)
                  setReturnOpen(true)
                }}
                disabled={busy}
              >
                Create return
              </Button>
            )}
            {payable && (
              <Button
                variant="outline"
                onClick={() => {
                  setDialogError(null)
                  setPayOpen(true)
                }}
                disabled={busy}
              >
                <Plus size={14} />
                Record payment
              </Button>
            )}
            {deletable && (
              <Button
                variant="destructive"
                onClick={() => {
                  setDialogError(null)
                  setDeleteOpen(true)
                }}
                disabled={busy}
              >
                Delete draft
              </Button>
            )}
            {cancellable && (
              <Button
                variant="destructive"
                onClick={() => {
                  setDialogError(null)
                  setCancelOpen(true)
                }}
                disabled={busy}
              >
                Cancel sale
              </Button>
            )}
          </div>
        </section>
      )}

      {/* ---- Dialogs ---- */}
      {postOpen && (
        <PostSaleDialog
          reference={sale.reference_no ?? `#${sale.id}`}
          busy={busy}
          error={dialogError}
          onClose={() => !busy && setPostOpen(false)}
          onConfirm={handlePost}
        />
      )}

      {cancelOpen && (
        <CancelSaleDialog
          reference={sale.reference_no ?? `#${sale.id}`}
          busy={busy}
          error={dialogError}
          onClose={() => !busy && setCancelOpen(false)}
          onConfirm={handleCancel}
        />
      )}

      {deleteOpen && (
        <DeleteDraftDialog
          reference={sale.reference_no ?? `#${sale.id}`}
          busy={busy}
          error={dialogError}
          onClose={() => !busy && setDeleteOpen(false)}
          onConfirm={handleDeleteDraft}
        />
      )}

      {payOpen && (
        <SalesDialog
          title="Record payment"
          busy={busy}
          onClose={() => !busy && setPayOpen(false)}
        >
          <p className="sales-dialog-note">
            Outstanding on this sale is <strong>{formatIDR(outstanding)}</strong>. The backend
            rejects a payment that would exceed the sale total.
          </p>
          <div className="sales-dialog-body">
            <label className="sales-field">
              <span className="sales-field-label">Payment method</span>
              <div className="sales-select">
                <select
                  value={payMethod}
                  onChange={(e) => setPayMethod(e.target.value ? Number(e.target.value) : '')}
                  disabled={busy}
                >
                  <option value="">Choose…</option>
                  {paymentMethods.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </div>
            </label>
            <label className="sales-field">
              <span className="sales-field-label">Amount</span>
              <input
                className="sales-input"
                type="number"
                inputMode="decimal"
                step="any"
                min={0}
                value={payAmount}
                onChange={(e) => setPayAmount(e.target.value)}
                placeholder={formatIDR(outstanding)}
                disabled={busy}
              />
            </label>
            <label className="sales-field">
              <span className="sales-field-label">Reference (optional)</span>
              <input
                className="sales-input"
                value={payReference}
                onChange={(e) => setPayReference(e.target.value)}
                placeholder="Receipt / voucher no."
                maxLength={100}
                disabled={busy}
              />
            </label>
            {dialogError && <span className="sales-field-error">{dialogError}</span>}
          </div>
          <div className="sales-dialog-foot">
            <Button variant="outline" onClick={() => setPayOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={handlePayment}
              disabled={busy || payMethod === '' || !payAmount}
            >
              {busy ? 'Saving…' : 'Save payment'}
            </Button>
          </div>
        </SalesDialog>
      )}

      {returnOpen && (
        <SalesDialog
          title="Create return"
          busy={busy}
          onClose={() => !busy && setReturnOpen(false)}
        >
          <p className="sales-dialog-note">
            Returned units are restored to stock and reduce this sale&apos;s receivable. A
            quantity cannot exceed the amount originally sold on that line.
          </p>
          <div className="sales-dialog-body">
            <label className="sales-field">
              <span className="sales-field-label">Reason</span>
              <textarea
                className="sales-textarea"
                value={returnReason}
                onChange={(e) => setReturnReason(e.target.value)}
                placeholder="Why is this being returned?"
                maxLength={RETURN_REASON_MAX}
                disabled={busy}
              />
              <span className="sales-field-foot">
                <span />
                <span className="sales-char-count">
                  {returnReason.length}/{RETURN_REASON_MAX}
                </span>
              </span>
            </label>
            <div>
              <span className="sales-field-label" style={{ display: 'block', marginBottom: 6 }}>
                Lines to return
              </span>
              <div className="sales-return-lines">
                {lines.map((l) => {
                  const qty = returnQuantities[l.id] ?? 0
                  return (
                    <div key={l.id} className="sales-return-line">
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 13 }}>
                          {productMap.get(l.product_id) ?? `Product #${l.product_id}`}
                        </div>
                        <div className="sales-muted" style={{ fontSize: 11 }}>
                          Sold {l.quantity} @ {formatIDR(num(l.unit_price))}
                        </div>
                      </div>
                      <input
                        className="sales-input"
                        type="number"
                        inputMode="decimal"
                        step="any"
                        min={0}
                        max={l.quantity}
                        value={qty || ''}
                        onChange={(e) => {
                          const v = Number(e.target.value) || 0
                          setReturnQuantities((prev) => ({ ...prev, [l.id]: v }))
                        }}
                        placeholder="0"
                        disabled={busy}
                        aria-label={`Quantity to return for line ${l.line_number}`}
                      />
                    </div>
                  )
                })}
              </div>
            </div>
            {dialogError && <span className="sales-field-error">{dialogError}</span>}
          </div>
          <div className="sales-dialog-foot">
            <Button variant="outline" onClick={() => setReturnOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={handleReturn} disabled={busy}>
              {busy ? 'Saving…' : 'Save return'}
            </Button>
          </div>
        </SalesDialog>
      )}

      {refundOpen && (
        <SalesDialog
          title="Issue refund"
          busy={busy}
          onClose={() => !busy && setRefundOpen(false)}
        >
          <p className="sales-dialog-note">
            Refundable balance on this sale is <strong>{formatIDR(crl)}</strong>.
            The backend rejects a refund that exceeds CRL or available cash.
          </p>
          <div className="sales-dialog-body">
            <label className="sales-field">
              <span className="sales-field-label">Payment method</span>
              <div className="sales-select">
                <select
                  value={refundMethod}
                  onChange={(e) => setRefundMethod(e.target.value ? Number(e.target.value) : '')}
                  disabled={busy}
                >
                  <option value="">Choose…</option>
                  {paymentMethods.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </div>
            </label>
            <label className="sales-field">
              <span className="sales-field-label">Amount</span>
              <input
                className="sales-input"
                type="number"
                inputMode="decimal"
                step="any"
                min={0}
                max={crl}
                value={refundAmount}
                onChange={(e) => setRefundAmount(e.target.value)}
                placeholder={formatIDR(crl)}
                disabled={busy}
              />
              <span className="sales-field-foot">
                <span />
                <span className="sales-char-count">Max {formatIDR(crl)}</span>
              </span>
            </label>
            <label className="sales-field">
              <span className="sales-field-label">Date (optional)</span>
              <input
                className="sales-input"
                type="date"
                value={refundDate}
                onChange={(e) => setRefundDate(e.target.value)}
                disabled={busy}
              />
            </label>
            <label className="sales-field">
              <span className="sales-field-label">Reason (optional)</span>
              <input
                className="sales-input"
                value={refundReason}
                onChange={(e) => setRefundReason(e.target.value)}
                placeholder="Why is this refund being issued?"
                maxLength={1000}
                disabled={busy}
              />
            </label>
            {dialogError && <span className="sales-field-error">{dialogError}</span>}
          </div>
          <div className="sales-dialog-foot">
            <Button variant="outline" onClick={() => setRefundOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              onClick={handleRefund}
              disabled={busy || refundMethod === '' || !refundAmount || Number(refundAmount) <= 0 || Number(refundAmount) > crl}
            >
              {busy ? 'Saving…' : 'Issue refund'}
            </Button>
          </div>
        </SalesDialog>
      )}
    </div>
  )
}

function StatBlock({
  label,
  value,
  muted,
}: {
  label: string
  value: React.ReactNode
  muted?: boolean
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span className="sales-field-label">{label}</span>
      <span
        style={{
          fontSize: 14,
          fontWeight: muted ? 600 : 700,
          color: muted ? '#4b5c72' : 'var(--foreground)',
        }}
      >
        {value}
      </span>
    </div>
  )
}
