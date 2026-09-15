'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import {
  AlertTriangle,
  ArrowLeft,
  FileText,
  Package,
  Pencil,
  Plus,
  Trash2,
  Truck,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Purchase, PurchaseLine } from '@/lib/purchase-types'
import type { Contact } from '@/lib/contact-types'
import type { Product } from '@/lib/product-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'

// ---------------------------------------------------------------------------
// Detail: GET /purchases/{id}?include=lines,shipping,payments,returns
// Captures the server ETag into If-Match for mutations.
// Phase B: draft mutations. Phase C: POST /purchases/{id}/post (draft → posted)
// via a confirmation dialog (capability purchase.post). Supplier names via
// GET /contacts?filter[type]=supplier (best-effort). Product names via
// GET /products?per_page=500 (best-effort). No ReceiveModal, no timeline,
// no fabricated invoice/terms/discount.
// ---------------------------------------------------------------------------

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('id-ID', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}
function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('id-ID', { year: 'numeric', month: 'short', day: '2-digit' })
  } catch {
    return iso
  }
}

function Badge({ children, tone, value }: { children: React.ReactNode; tone: 'lifecycle' | 'payment'; value: string }) {
  const lifecycle: Record<string, string> = {
    draft: '#f1f5f9,#475569,#e2e8f0',
    posted: '#eff6ff,#2563eb,#dbeafe',
    completed: '#ecfdf5,#059669,#d1fae5',
    partially_returned: '#fffbeb,#d97706,#fde68a',
    returned: '#faf5ff,#7c3aed,#e9d5ff',
    cancelled: '#fef2f2,#dc2626,#fecaca',
  }
  const payment: Record<string, string> = {
    unpaid: '#fef2f2,#dc2626,#fecaca',
    partial: '#eff6ff,#2563eb,#dbeafe',
    paid: '#ecfdf5,#059669,#d1fae5',
  }
  const map = tone === 'lifecycle' ? lifecycle : payment
  const raw = map[value] ?? (tone === 'lifecycle' ? lifecycle.draft : payment.unpaid)
  const [bg, fg, bd] = raw.split(',')
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 10px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        textTransform: 'capitalize',
        background: bg,
        color: fg,
        border: `1px solid ${bd}`,
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor', flex: 'none' }} />
      {children}
    </span>
  )
}

function Card({
  title,
  description,
  action,
  children,
}: {
  title: string
  description?: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', borderBottom: '1px solid var(--border)', gap: 12 }}>
        <div>
          <h2 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>{title}</h2>
          {description && <p style={{ margin: '4px 0 0', fontSize: 12, color: '#718198' }}>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

export default function PurchaseDetailPage() {
  const params = useParams<{ id: string }>()
  const rawId = params?.id ?? ''
  const purchaseId = Number(rawId)
  const user = useSession()
  const canView = user.capabilities.includes('purchase.view')

  const [purchase, setPurchase] = useState<Purchase | null>(null)
  const [etag, setEtag] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)

  const [supplierContact, setSupplierContact] = useState<Contact | null>(null)
  const [productMap, setProductMap] = useState<Map<number, string>>(new Map())
  const [productOptions, setProductOptions] = useState<Product[]>([])
  const [suppliers, setSuppliers] = useState<Contact[]>([])

  // Phase B — draft editing (lifecycle_status === 'draft' + capability).
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [conflict, setConflict] = useState<string | null>(null)
  const [headerOpen, setHeaderOpen] = useState(false)
  const [lineDialog, setLineDialog] = useState<{ mode: 'add' | 'edit'; line?: PurchaseLine } | null>(null)
  const [shippingOpen, setShippingOpen] = useState(false)
  const router = useRouter()

  const fetchPurchase = useCallback(async () => {
    if (!Number.isFinite(purchaseId) || purchaseId <= 0) {
      setLoading(false)
      setNotFound(true)
      return
    }
    if (!canView) {
      setLoading(false)
      setError('Missing capability: purchase.view')
      return
    }
    setLoading(true)
    setError(null)
    setNotFound(false)
    try {
      // api.headers.get exposes the response ETag (canonical quoted ISO-8601
      // with the Z suffix) so Phase-B mutations can send it back verbatim in
      // If-Match without a divergent format.
      const res = await api.headers.get<Purchase>(`/purchases/${purchaseId}`, {
        params: { include: 'lines,shipping,payments,returns' },
      })
      const data = res.data
      const derived =
        res.headers.get('ETag') ??
        (data as unknown as { etag?: string })?.etag ??
        (data.updated_at ? `"${data.updated_at}"` : null)
      setEtag(derived)
      setPurchase(data)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load purchase'
      const code = (err as Error & { code?: string }).code
      if (code === 'not_found' || /not found|404/i.test(msg)) {
        setNotFound(true)
        setPurchase(null)
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }, [purchaseId, canView])

  useEffect(() => {
    fetchPurchase()
  }, [fetchPurchase])

  // Supplier lookup best-effort
  useEffect(() => {
    if (!purchase?.supplier_id) {
      setSupplierContact(null)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const all = await api.get<{ data: Contact[] }>('/contacts', { params: { 'filter[type]': 'supplier', per_page: '500' } })
        if (cancelled) return
        const found = all.data.find((c) => c.id === purchase.supplier_id) ?? null
        setSupplierContact(found)
      } catch {
        if (!cancelled) setSupplierContact(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [purchase?.supplier_id])

  // Product names best-effort (also the option list for the line editor)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.get<{ data: Product[] }>('/products', { params: { per_page: '500' } })
        if (cancelled) return
        const m = new Map<number, string>()
        res.data.forEach((p) => m.set(p.id, p.name))
        setProductMap(m)
        setProductOptions(res.data)
      } catch {
        if (!cancelled) {
          setProductMap(new Map())
          setProductOptions([])
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const supplierDisplayName = useMemo(() => {
    if (!purchase?.supplier_id) return '—'
    if (supplierContact) return supplierContact.name
    return `Supplier #${purchase.supplier_id}`
  }, [purchase?.supplier_id, supplierContact])

  // -------------------------------------------------------------------------
  // Phase B — draft mutations. Capability purchase.edit_own_draft + draft only.
  // Every mutation sends the server's canonical ETag in If-Match; POST/DELETE
  // also send a fresh Idempotency-Key. 412 -> conflict banner + refetch (no
  // blind retry with a stale ETag). 409/400 -> surfaced as a readable toast.
  // -------------------------------------------------------------------------
  const canEditDraft = user.capabilities.includes('purchase.edit_own_draft')
  const canPost = user.capabilities.includes('purchase.post')
  const isDraft = purchase?.lifecycle_status === 'draft'
  const editable = canEditDraft && isDraft
  const postable = canPost && isDraft
  const [postConfirmOpen, setPostConfirmOpen] = useState(false)

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const refetch = useCallback(async () => {
    await fetchPurchase()
  }, [fetchPurchase])

  // Supplier lookup for the header-edit dialog (best-effort).
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.get<{ data: Contact[] }>('/contacts', {
          params: { 'filter[type]': 'supplier', per_page: '500' },
        })
        if (!cancelled) setSuppliers(res.data)
      } catch {
        if (!cancelled) setSuppliers([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  /**
   * Runs a Phase-B mutation with the shared error contract:
   *  412 version_mismatch -> conflict banner + refetch (never auto-retry),
   *  409/400/other       -> readable toast message,
   *  success             -> refresh the authoritative purchase from the server.
   */
  const runMutation = async (
    label: string,
    fn: () => Promise<void>,
    opts: { refresh?: boolean } = { refresh: true },
  ) => {
    setBusy(true)
    setConflict(null)
    try {
      await fn()
      toast(label)
      if (opts.refresh !== false) await refetch()
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      const msg = err instanceof Error ? err.message : 'Request failed'
      if (code === 'version_mismatch' || /stale|412|precondition/i.test(msg)) {
        setConflict('This purchase changed elsewhere since you loaded it. Refresh and try again.')
        await refetch()
      } else if (code === 'lifecycle_state_invalid' || (typeof code === 'string' && code.startsWith('lifecycle'))) {
        toast('This purchase is no longer a draft.')
        await refetch()
      } else if (code === 'idempotency_violation') {
        toast('Duplicate request detected — nothing was changed twice.')
        await refetch()
      } else {
        toast(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  const patchHeader = (supplier_id: number | null, purchase_date: string, notes: string) =>
    runMutation(
      'Purchase updated',
      async () => {
        await api.patch(
          `/purchases/${purchaseId}`,
          {
            supplier_id,
            purchase_date: purchase_date ? new Date(`${purchase_date}T00:00:00`).toISOString() : null,
            notes: notes.trim() ? notes.trim() : null,
          },
          { headers: { 'If-Match': etag ?? '' } },
        )
        setHeaderOpen(false)
      },
    )

  const addLine = (product_id: number, quantity: number, unit_price: number) =>
    runMutation(
      'Line added',
      async () => {
        await api.post(
          `/purchases/${purchaseId}/lines`,
          { product_id, quantity, unit_price },
          { headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() } },
        )
        setLineDialog(null)
      },
    )

  const updateLine = (lineId: number, quantity: number, unit_price: number) =>
    runMutation(
      'Line updated',
      async () => {
        await api.patch(
          `/purchases/${purchaseId}/lines/${lineId}`,
          { quantity, unit_price },
          { headers: { 'If-Match': etag ?? '' } },
        )
        setLineDialog(null)
      },
    )

  const deleteLine = (lineId: number) =>
    runMutation('Line removed', async () => {
      await api.delete(`/purchases/${purchaseId}/lines/${lineId}`, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
    })

  const saveShipping = (amount: number, paid_in_cash: boolean, supplier_id: number | null, description: string) =>
    runMutation(
      'Shipping saved',
      async () => {
        await api.post(
          `/purchases/${purchaseId}/shipping`,
          { amount, paid_in_cash, supplier_id, description: description.trim() ? description.trim() : null },
          { headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() } },
        )
        setShippingOpen(false)
      },
    )

  // Phase C — post a draft (POST /purchases/{id}/post).
  // Lifecycle draft → posted only. Backend is authoritative for inventory /
  // stock movements / moving-average / shipping landed cost. Body is {}
  // per the backend contract (request_body is fingerprinted for idempotency).
  const postPurchase = () =>
    runMutation('Purchase posted', async () => {
      await api.post(`/purchases/${purchaseId}/post`, {}, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
      setPostConfirmOpen(false)
    })

  if (loading) {
    return (
      <div className="content">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 1100 }}>
          <div className="skeleton" style={{ height: 28, width: 220, borderRadius: 6 }} />
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 16 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="skeleton" style={{ height: 320, borderRadius: 10 }} />
              <div className="skeleton" style={{ height: 220, borderRadius: 10 }} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="skeleton" style={{ height: 180, borderRadius: 10 }} />
              <div className="skeleton" style={{ height: 200, borderRadius: 10 }} />
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (notFound) {
    return (
      <div className="content">
        <div style={{ maxWidth: 520, margin: '40px auto', background: 'white', border: '1px solid var(--border)', borderRadius: 14, padding: 28, textAlign: 'center' }}>
          <div style={{ width: 48, height: 48, borderRadius: 999, background: '#eff6ff', color: 'var(--primary)', display: 'grid', placeItems: 'center', margin: '0 auto 12px' }}>
            <FileText size={22} />
          </div>
          <h1 style={{ fontSize: 18, fontWeight: 800, margin: 0 }}>Purchase not found</h1>
          <p style={{ margin: '8px 0 0', fontSize: 13, color: '#718198', lineHeight: 1.6 }}>
            The purchase you&apos;re looking for doesn&apos;t exist or may no longer be available.
          </p>
          <div style={{ marginTop: 18, display: 'flex', justifyContent: 'center', gap: 8 }}>
            <Link href="/purchases" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 14px', borderRadius: 8, border: '1px solid var(--border)', background: 'white', fontSize: 13, fontWeight: 600, textDecoration: 'none', color: '#334155' }}>
              <ArrowLeft size={14} /> Back to purchases
            </Link>
            <Button variant="outline" onClick={fetchPurchase}>
              Try again
            </Button>
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Purchasing / Purchase detail</div>
            <h1>Purchase detail</h1>
          </div>
          <Link href="/purchases" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 14px', borderRadius: 8, border: '1px solid var(--border)', background: 'white', fontSize: 13, fontWeight: 600, textDecoration: 'none', color: '#334155' }}>
            <ArrowLeft size={14} /> Back to purchases
          </Link>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>Failed to load purchase</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchPurchase}>
            Retry
          </Button>
        </div>
      </div>
    )
  }

  if (!purchase) return null

  const ref = purchase.reference_no ?? `#${purchase.id}`

  return (
    <div className="content">
      {/* Header */}
      <div className="page-heading" style={{ alignItems: 'flex-start' }}>
        <div>
          <div className="eyebrow">
            <Link href="/purchases" style={{ color: 'inherit', textDecoration: 'none' }}>
              Purchasing
            </Link>{' '}
            <span style={{ opacity: 0.5, margin: '0 4px' }}>/</span> Purchase detail
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginTop: 6 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>{ref}</h1>
            <Badge tone="lifecycle" value={purchase.lifecycle_status}>
              {purchase.lifecycle_status}
            </Badge>
            <Badge tone="payment" value={purchase.payment_state}>
              {purchase.payment_state}
            </Badge>
          </div>
          <p style={{ margin: '8px 0 0', fontSize: 12, color: '#718198' }}>
            #{purchase.id} · Purchased {fmtDateShort(purchase.purchase_date)} · Version {purchase.version}
            {etag && <span style={{ marginLeft: 8, fontFamily: 'ui-monospace, monospace', fontSize: 11, color: '#94a3b8' }}>ETag {etag}</span>}
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {postable && (
            <Button onClick={() => setPostConfirmOpen(true)} disabled={busy}>
              <Package size={14} className="mr-1" /> Post purchase
            </Button>
          )}
          {editable && (
            <Button variant="outline" onClick={() => setHeaderOpen(true)} disabled={busy}>
              <Pencil size={14} className="mr-1" /> Edit purchase
            </Button>
          )}
          <Link
            href="/purchases"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 14px',
              borderRadius: 8,
              border: '1px solid var(--border)',
              background: 'white',
              fontSize: 13,
              fontWeight: 600,
              textDecoration: 'none',
              color: '#334155',
            }}
          >
            <ArrowLeft size={14} /> Back to purchases
          </Link>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 340px', gap: 16, alignItems: 'start' }}>
        {/* Left */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
          <Card
            title="Purchase items"
            description={`${purchase.lines?.length ?? 0} line(s) · quantities and landed costs`}
            action={
              editable ? (
                <Button variant="outline" size="sm" onClick={() => setLineDialog({ mode: 'add' })} disabled={busy}>
                  <Plus size={13} className="mr-1" /> Add line
                </Button>
              ) : undefined
            }
          >
            {purchase.lines && purchase.lines.length > 0 ? (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: '#f8fafc', textAlign: 'left', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Product</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Quantity</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Unit price</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Allocated shipping</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Line total</th>
                      {editable && <th style={{ padding: '10px 14px', width: 90 }} />}
                    </tr>
                  </thead>
                  <tbody>
                    {purchase.lines.map((ln) => {
                      const name = productMap.get(ln.product_id) ?? `Product #${ln.product_id}`
                      return (
                        <tr key={ln.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                          <td style={{ padding: '11px 14px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span style={{ width: 28, height: 28, borderRadius: 6, background: '#f1f5f9', display: 'grid', placeItems: 'center', flex: 'none' }}>
                                <Package size={14} color="#64748b" />
                              </span>
                              <div>
                                <div style={{ fontWeight: 600, color: '#0f172a' }}>{name}</div>
                                <div style={{ fontSize: 11, color: '#94a3b8' }}>Line #{ln.line_number} · Product {ln.product_id}</div>
                              </div>
                            </div>
                          </td>
                          <td style={{ padding: '11px 14px', textAlign: 'right', fontWeight: 600 }}>{ln.quantity}</td>
                          <td style={{ padding: '11px 14px', textAlign: 'right' }}>{formatIDR(ln.unit_price)}</td>
                          <td style={{ padding: '11px 14px', textAlign: 'right', color: '#475569' }}>{formatIDR(ln.allocated_shipping)}</td>
                          <td style={{ padding: '11px 14px', textAlign: 'right', fontWeight: 700 }}>{formatIDR(ln.line_total)}</td>
                          {editable && (
                            <td style={{ padding: '11px 14px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                              <button
                                onClick={() => setLineDialog({ mode: 'edit', line: ln })}
                                disabled={busy}
                                aria-label="Edit line"
                                style={{ border: 0, background: 'none', color: '#2563eb', marginRight: 8 }}
                              >
                                <Pencil size={14} />
                              </button>
                              {purchase.lines && purchase.lines.length > 1 && (
                                <button
                                  onClick={() => deleteLine(ln.id)}
                                  disabled={busy}
                                  aria-label="Delete line"
                                  style={{ border: 0, background: 'none', color: '#dc2626' }}
                                >
                                  <Trash2 size={14} />
                                </button>
                              )}
                            </td>
                          )}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ padding: 20, fontSize: 13, color: '#718198', textAlign: 'center' }}>No lines on this purchase.</div>
            )}
          </Card>

          <Card
            title="Shipping"
            description="Landed-cost component (capitalized into inventory value)"
            action={
              editable ? (
                <Button variant="outline" size="sm" onClick={() => setShippingOpen(true)} disabled={busy}>
                  <Pencil size={13} className="mr-1" />
                  {purchase.shipping ? 'Edit shipping' : 'Set shipping'}
                </Button>
              ) : undefined
            }
          >
            {purchase.shipping ? (
              <div style={{ padding: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontSize: 13 }}>
                <div>
                  <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Amount</div>
                  <div style={{ marginTop: 4, fontWeight: 700 }}>{formatIDR(purchase.shipping.amount)}</div>
                </div>
                <div>
                  <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Paid in cash</div>
                  <div style={{ marginTop: 4, fontWeight: 600 }}>{purchase.shipping.paid_in_cash ? 'Yes' : 'No'}</div>
                </div>
                {purchase.shipping.supplier_id && (
                  <div>
                    <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Shipping supplier</div>
                    <div style={{ marginTop: 4 }}>{purchase.shipping.supplier_id}</div>
                  </div>
                )}
                <div style={{ gridColumn: '1 / -1' }}>
                  <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Description</div>
                  <div style={{ marginTop: 4, color: '#334155', whiteSpace: 'pre-wrap' }}>{purchase.shipping.description ?? '—'}</div>
                </div>
              </div>
            ) : (
              <div style={{ padding: 20, fontSize: 13, color: '#718198', textAlign: 'center' }}>No shipping recorded.</div>
            )}
          </Card>

          <Card title="Payment history" description="Recorded allocations (read-only in Phase A)">
            {purchase.payments && purchase.payments.length > 0 ? (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: '#f8fafc', textAlign: 'left', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Date</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Method</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Reference</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {purchase.payments.map((pay) => (
                      <tr key={pay.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                        <td style={{ padding: '10px 14px', color: '#475569' }}>{fmtDateShort(pay.payment_date)}</td>
                        <td style={{ padding: '10px 14px' }}>{pay.payment_method_id}</td>
                        <td style={{ padding: '10px 14px', color: '#718198', fontSize: 12 }}>{pay.reference ?? '—'}</td>
                        <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 700 }}>{formatIDR(pay.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ padding: 20, fontSize: 13, color: '#718198', textAlign: 'center' }}>No payments yet.</div>
            )}
          </Card>

          <Card title="Returns" description="Goods returned to supplier (read-only)">
            {purchase.returns && purchase.returns.length > 0 ? (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: '#f8fafc', textAlign: 'left', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Return</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Date</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Value</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {purchase.returns.map((ret) => (
                      <tr key={ret.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                        <td style={{ padding: '10px 14px', fontWeight: 600 }}>#{ret.id}</td>
                        <td style={{ padding: '10px 14px', color: '#475569' }}>{fmtDateShort(ret.return_date)}</td>
                        <td style={{ padding: '10px 14px', textAlign: 'right', fontWeight: 700 }}>{formatIDR(ret.total_value_returned)}</td>
                        <td style={{ padding: '10px 14px' }}>
                          <Badge tone="lifecycle" value={ret.lifecycle_status}>
                            {ret.lifecycle_status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ padding: 20, fontSize: 13, color: '#718198', textAlign: 'center' }}>No returns.</div>
            )}
          </Card>
        </div>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Card title="Supplier information">
            <div style={{ padding: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ width: 36, height: 36, borderRadius: 8, background: '#eff6ff', color: 'var(--primary)', display: 'grid', placeItems: 'center' }}>
                  <Truck size={16} />
                </span>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 13 }}>{supplierDisplayName}</div>
                  <div style={{ fontSize: 11, color: '#8a98ab' }}>{purchase.supplier_id ? `Supplier #${purchase.supplier_id}` : 'No supplier (counter purchase)'}</div>
                </div>
              </div>
              {supplierContact && (
                <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13, color: '#334155', borderTop: '1px solid #f0f4f9', paddingTop: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: '#8a98ab', fontSize: 11, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase' }}>Phone</span>
                    <span>{supplierContact.phone ?? '—'}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: '#8a98ab', fontSize: 11, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase' }}>Email</span>
                    <span style={{ maxWidth: 170, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{supplierContact.email ?? '—'}</span>
                  </div>
                  <div>
                    <div style={{ color: '#8a98ab', fontSize: 11, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase' }}>Address</div>
                    <div style={{ marginTop: 4, color: '#334155', fontSize: 12, lineHeight: 1.5 }}>{supplierContact.address ?? '—'}</div>
                  </div>
                </div>
              )}
            </div>
          </Card>

          <Card title="Purchase information">
            <div style={{ padding: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontSize: 13 }}>
              <div>
                <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Reference no</div>
                <div style={{ marginTop: 4, fontWeight: 600 }}>{purchase.reference_no ?? '—'}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Purchase date</div>
                <div style={{ marginTop: 4 }}>{fmtDateShort(purchase.purchase_date)}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Created by</div>
                <div style={{ marginTop: 4 }}>{purchase.created_by}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Created at</div>
                <div style={{ marginTop: 4, fontSize: 12 }}>{fmtDate(purchase.created_at)}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Posted</div>
                <div style={{ marginTop: 4, fontSize: 12 }}>{purchase.posted_at ? `${fmtDate(purchase.posted_at)} · by #${purchase.posted_by ?? '—'}` : '—'}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Cancelled</div>
                <div style={{ marginTop: 4, fontSize: 12 }}>{purchase.cancellation_date ? `${fmtDate(purchase.cancellation_date)} · by #${purchase.cancelled_by ?? '—'}` : '—'}</div>
              </div>
              {purchase.cancellation_reason && (
                <div style={{ gridColumn: '1 / -1' }}>
                  <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>Cancellation reason</div>
                  <div style={{ marginTop: 4, fontSize: 12, color: '#334155', whiteSpace: 'pre-wrap' }}>{purchase.cancellation_reason}</div>
                </div>
              )}
            </div>
          </Card>

          <Card title="Payment summary">
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#718198' }}>Purchase total</span>
                <strong>{formatIDR(purchase.total_amount)}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: '#718198' }}>Paid</span>
                <strong>{formatIDR(purchase.paid_amount)}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid #f0f4f9', paddingTop: 10, marginTop: 2 }}>
                <span style={{ fontWeight: 700 }}>Outstanding</span>
                <strong style={{ color: 'var(--primary)' }}>{formatIDR(purchase.outstanding)}</strong>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
                <span style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase' }}>Payment state</span>
                <Badge tone="payment" value={purchase.payment_state}>
                  {purchase.payment_state}
                </Badge>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#718198' }}>
                <span>AP</span>
                <span style={{ fontWeight: 600, color: '#334155' }}>{formatIDR(purchase.ap)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#718198' }}>
                <span>Supplier receivable</span>
                <span style={{ fontWeight: 600, color: '#334155' }}>{formatIDR(purchase.supplier_receivable)}</span>
              </div>
            </div>
          </Card>

          <Card title="Notes">
            <div style={{ padding: 16, fontSize: 13, color: purchase.notes ? '#334155' : '#8a98ab', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
              {purchase.notes ?? 'No notes.'}
            </div>
          </Card>
        </div>
      </div>

      {/* Conflict banner (412) — never auto-retried with a stale ETag */}
      {conflict && (
        <div
          role="alert"
          style={{
            position: 'fixed',
            top: 20,
            right: 20,
            zIndex: 60,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            maxWidth: 420,
            padding: '12px 14px',
            borderRadius: 10,
            background: '#fffbeb',
            color: '#92400e',
            border: '1px solid #fde68a',
            fontSize: 13,
          }}
        >
          <AlertTriangle size={16} />
          <span style={{ flex: 1 }}>{conflict}</span>
          <button onClick={() => setConflict(null)} aria-label="Dismiss" style={{ border: 0, background: 'none', color: 'inherit' }}>
            <X size={15} />
          </button>
        </div>
      )}

      {/* Toast */}
      {notice && (
        <div
          role="status"
          style={{
            position: 'fixed',
            bottom: 20,
            right: 20,
            zIndex: 60,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 16px',
            borderRadius: 10,
            background: 'var(--foreground)',
            color: 'white',
            fontSize: 13,
            boxShadow: '0 8px 24px rgba(0,0,0,.15)',
          }}
        >
          {notice}
        </div>
      )}

      {/* Header edit dialog (supplier / purchase_date / notes only) */}
      {headerOpen && purchase && (
        <HeaderEditDialog
          purchase={purchase}
          suppliers={suppliers}
          busy={busy}
          onCancel={() => setHeaderOpen(false)}
          onSave={patchHeader}
        />
      )}

      {/* Post confirmation dialog (Phase C) */}
      {postConfirmOpen && purchase && (
        <PostConfirmDialog
          reference={purchase.reference_no ?? `#${purchase.id}`}
          total={purchase.total_amount}
          lineCount={purchase.lines?.length ?? 0}
          busy={busy}
          onCancel={() => { if (!busy) setPostConfirmOpen(false) }}
          onConfirm={postPurchase}
        />
      )}

      {/* Line add/edit dialog */}
      {lineDialog && (
        <LineDialog
          mode={lineDialog.mode}
          line={lineDialog.line}
          products={productOptions}
          productName={productMap}
          busy={busy}
          onCancel={() => setLineDialog(null)}
          onSave={(productId, quantity, unitPrice) =>
            lineDialog.mode === 'add'
              ? addLine(productId, quantity, unitPrice)
              : updateLine(lineDialog.line!.id, quantity, unitPrice)
          }
        />
      )}

      {/* Shipping dialog */}
      {shippingOpen && (
        <ShippingDialog
          shipping={purchase?.shipping ?? null}
          suppliers={suppliers}
          busy={busy}
          onCancel={() => setShippingOpen(false)}
          onSave={saveShipping}
        />
      )}

      <style>{`@media(max-width: 900px){ div[style*="grid-template-columns: minmax(0, 1fr) 340px"]{grid-template-columns:1fr!important} }`}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Phase B dialogs — field styles shared with the create dialog on the list page
// ---------------------------------------------------------------------------

const FIELD_LABEL: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: 0.8,
  textTransform: 'uppercase',
  color: '#8a98ab',
}
const FIELD_INPUT: React.CSSProperties = {
  height: 34,
  borderRadius: 8,
  border: '1px solid var(--border)',
  background: 'white',
  padding: '0 10px',
  fontSize: 13,
  width: '100%',
}

// ---------------------------------------------------------------------------
// Phase C — post confirmation. Server total shown verbatim (no client math).
// Posting is irreversible: finalizes the purchase, updates inventory via a
// stock movement (trigger purchase_receipt), applies landed shipping cost,
// and makes the draft non-editable.
// ---------------------------------------------------------------------------
function PostConfirmDialog({
  reference,
  total,
  lineCount,
  busy,
  onCancel,
  onConfirm,
}: {
  reference: string
  total: string | number | null | undefined
  lineCount: number
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <DialogShell
      title="Post purchase"
      description="Posting finalizes this purchase. This cannot be undone from here."
      onCancel={onCancel}
      busy={busy}
      onSave={onConfirm}
      saveLabel={busy ? 'Posting…' : 'Post purchase'}
      width={480}
    >
      <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14, fontSize: 13, color: '#334155' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: '12px 14px' }}>
          <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1, color: '#b45309' }} />
          <div style={{ lineHeight: 1.6 }}>
            Posting will finalize the purchase, update inventory, record the
            purchase receipt (stock movement), apply landed shipping cost
            where applicable, and make the draft no longer editable.
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
          <div>
            <div style={FIELD_LABEL}>Reference</div>
            <div style={{ marginTop: 4, fontWeight: 700 }}>{reference}</div>
          </div>
          <div>
            <div style={FIELD_LABEL}>Lines</div>
            <div style={{ marginTop: 4, fontWeight: 700 }}>{lineCount}</div>
          </div>
          <div>
            <div style={FIELD_LABEL}>Total (server)</div>
            <div style={{ marginTop: 4, fontWeight: 700 }}>{total != null ? formatIDR(Number(total)) : '—'}</div>
          </div>
        </div>
      </div>
    </DialogShell>
  )
}

function DialogShell({
  title,
  description,
  onCancel,
  busy,
  onSave,
  saveLabel,
  children,
  width = 560,
}: {
  title: string
  description?: string
  onCancel: () => void
  busy: boolean
  onSave: () => void
  saveLabel: string
  children: React.ReactNode
  width?: number
}) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 50,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        background: 'rgba(15,23,42,.35)',
        padding: 24,
        overflowY: 'auto',
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: width,
          background: 'white',
          borderRadius: 14,
          border: '1px solid var(--border)',
          boxShadow: '0 20px 48px rgba(0,0,0,.12)',
          margin: '40px 0',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>{title}</h2>
            {description && <p style={{ margin: '4px 0 0', fontSize: 12, color: '#718198' }}>{description}</p>}
          </div>
          <button onClick={onCancel} aria-label="Close" style={{ border: 0, background: 'none', color: '#8a98ab' }}>
            <X size={18} />
          </button>
        </div>
        <div style={{ padding: 20 }}>{children}</div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            padding: '14px 20px',
            borderTop: '1px solid var(--border)',
          }}
        >
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={onSave} disabled={busy}>
            {busy ? 'Saving…' : saveLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

function HeaderEditDialog({
  purchase,
  suppliers,
  busy,
  onCancel,
  onSave,
}: {
  purchase: Purchase
  suppliers: Contact[]
  busy: boolean
  onCancel: () => void
  onSave: (supplierId: number | null, purchaseDate: string, notes: string) => void
}) {
  const [supplierId, setSupplierId] = useState(purchase.supplier_id ? String(purchase.supplier_id) : '')
  const [purchaseDate, setPurchaseDate] = useState(() => (purchase.purchase_date ? purchase.purchase_date.slice(0, 10) : ''))
  const [notes, setNotes] = useState(purchase.notes ?? '')

  return (
    <DialogShell
      title="Edit purchase"
      description="Reference number is immutable once the draft is created."
      onCancel={onCancel}
      busy={busy}
      onSave={() => onSave(supplierId ? Number(supplierId) : null, purchaseDate, notes)}
      saveLabel="Save changes"
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>Supplier (optional)</label>
          <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} style={FIELD_INPUT}>
            <option value="">No supplier (counter / cash buy)</option>
            {suppliers.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>Purchase date</label>
          <input type="date" value={purchaseDate} onChange={(e) => setPurchaseDate(e.target.value)} style={FIELD_INPUT} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>Notes</label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            maxLength={2000}
            rows={3}
            style={{ ...FIELD_INPUT, height: 'auto', padding: 10, resize: 'vertical' }}
          />
        </div>
        <div style={{ fontSize: 12, color: '#94a3b8' }}>
          Reference no: <strong style={{ color: '#334155' }}>{purchase.reference_no ?? '—'}</strong> (read-only)
        </div>
      </div>
    </DialogShell>
  )
}

function LineDialog({
  mode,
  line,
  products,
  productName,
  busy,
  onCancel,
  onSave,
}: {
  mode: 'add' | 'edit'
  line?: PurchaseLine
  products: Product[]
  productName: Map<number, string>
  busy: boolean
  onCancel: () => void
  onSave: (productId: number, quantity: number, unitPrice: number) => void
}) {
  const [productId, setProductId] = useState(line ? String(line.product_id) : '')
  const [quantity, setQuantity] = useState(line ? String(line.quantity) : '1')
  const [unitPrice, setUnitPrice] = useState(line ? String(line.unit_price) : '')
  const [localError, setLocalError] = useState<string | null>(null)

  const selectedProduct = products.find((p) => String(p.id) === productId)
  const subtotal = (Number(quantity) || 0) * (Number(unitPrice) || 0)

  const submit = () => {
    setLocalError(null)
    if (!productId) {
      setLocalError('Select a product.')
      return
    }
    const q = Number(quantity)
    const u = Number(unitPrice)
    if (!Number.isFinite(q) || q <= 0) {
      setLocalError('Quantity must be greater than 0.')
      return
    }
    if (!Number.isFinite(u) || u < 0) {
      setLocalError('Unit price must be 0 or greater.')
      return
    }
    onSave(Number(productId), q, u)
  }

  return (
    <DialogShell
      title={mode === 'add' ? 'Add line' : 'Edit line'}
      description="Subtotal is a preview — the server computes the authoritative allocation."
      onCancel={onCancel}
      busy={busy}
      onSave={submit}
      saveLabel={mode === 'add' ? 'Add line' : 'Save line'}
      width={520}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>Product</label>
          <select
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
            style={FIELD_INPUT}
            disabled={mode === 'edit'}
          >
            <option value="">Select product…</option>
            {products.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
            {mode === 'edit' && line && !products.some((p) => p.id === line.product_id) && (
              <option value={String(line.product_id)}>{productName.get(line.product_id) ?? `Product #${line.product_id}`}</option>
            )}
          </select>
          {mode === 'edit' && (
            <span style={{ fontSize: 11, color: '#94a3b8' }}>Product cannot be changed — delete and re-add instead.</span>
          )}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={FIELD_LABEL}>Quantity</label>
            <input
              type="number"
              min="0"
              step="0.0001"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              style={FIELD_INPUT}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={FIELD_LABEL}>Unit price</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={unitPrice}
              onChange={(e) => setUnitPrice(e.target.value)}
              style={FIELD_INPUT}
            />
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, color: '#475569' }}>
          <span>Unit</span>
          <span>{selectedProduct?.unit_id ? `unit #${selectedProduct.unit_id}` : '—'}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid #f0f4f9', paddingTop: 10, fontSize: 13, fontWeight: 700 }}>
          <span>Subtotal (preview)</span>
          <span>{formatIDR(subtotal)}</span>
        </div>
        {localError && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 13, border: '1px solid #fecaca' }}>
            <AlertTriangle size={15} /> {localError}
          </div>
        )}
      </div>
    </DialogShell>
  )
}

function ShippingDialog({
  shipping,
  suppliers,
  busy,
  onCancel,
  onSave,
}: {
  shipping: { amount: number; paid_in_cash: boolean; supplier_id: number | null; description: string | null } | null
  suppliers: Contact[]
  busy: boolean
  onCancel: () => void
  onSave: (amount: number, paidInCash: boolean, supplierId: number | null, description: string) => void
}) {
  const [amount, setAmount] = useState(shipping ? String(shipping.amount) : '')
  const [paidInCash, setPaidInCash] = useState(shipping?.paid_in_cash ?? false)
  const [supplierId, setSupplierId] = useState(shipping?.supplier_id ? String(shipping.supplier_id) : '')
  const [description, setDescription] = useState(shipping?.description ?? '')
  const [localError, setLocalError] = useState<string | null>(null)

  const submit = () => {
    setLocalError(null)
    const a = Number(amount)
    if (!Number.isFinite(a) || a < 0) {
      setLocalError('Shipping amount must be 0 or greater.')
      return
    }
    onSave(a, paidInCash, supplierId ? Number(supplierId) : null, description)
  }

  return (
    <DialogShell
      title={shipping ? 'Edit shipping' : 'Set shipping'}
      description="Shipping is a landed cost — it capitalizes into inventory value, not an operating expense."
      onCancel={onCancel}
      busy={busy}
      onSave={submit}
      saveLabel="Save shipping"
      width={520}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>Shipping amount</label>
          <input
            type="number"
            min="0"
            step="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="0"
            style={FIELD_INPUT}
          />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>Shipping supplier (optional)</label>
          <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} style={FIELD_INPUT}>
            <option value="">None</option>
            {suppliers.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>Description</label>
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} style={FIELD_INPUT} />
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#334155' }}>
          <input type="checkbox" checked={paidInCash} onChange={(e) => setPaidInCash(e.target.checked)} />
          Shipping paid in cash now
        </label>
        <p style={{ margin: 0, fontSize: 11, color: '#94a3b8' }}>
          The server re-allocates the shipping amount pro-rata across the lines and recomputes each line total.
        </p>
        {localError && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 13, border: '1px solid #fecaca' }}>
            <AlertTriangle size={15} /> {localError}
          </div>
        )}
      </div>
    </DialogShell>
  )
}
