'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  ClipboardList,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Pagination, Purchase, PurchaseListResponse } from '@/lib/purchase-types'
import type { Contact } from '@/lib/contact-types'
import type { Product } from '@/lib/product-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'

// ============================================================================
// PurchasesDataTable — client island with URL-driven state.
// Mirrors /contacts pattern: server fetches (page.tsx) + this island renders
// interactivity, filters drive URL → RSC re-fetches.
// ============================================================================

interface Props {
  initialPurchases: Purchase[]
  initialPagination: Pagination
  initialSuppliers: Contact[]
  initialProducts: Product[]
}

const DEFAULT_PAGINATION: Pagination = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('id-ID', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    })
  } catch {
    return iso
  }
}

function lifecycleLabel(s: string): string {
  const map: Record<string, string> = {
    draft: 'status.draft',
    posted: 'status.posted',
    completed: 'status.completed',
    partially_returned: 'status.partiallyReturned',
    returned: 'status.returned',
    cancelled: 'status.cancelled',
  }
  return map[s] ?? s
}

function paymentLabel(s: string): string {
  const map: Record<string, string> = {
    unpaid: 'status.unpaid',
    partial: 'status.partial',
    paid: 'status.paid',
  }
  return map[s] ?? s
}

function parseStyle(s: string): Record<string, string> {
  const out: Record<string, string> = {}
  s.split(';').forEach((part) => {
    const [k, v] = part.split(':').map((x) => x?.trim())
    if (k && v) out[k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v
  })
  return out
}

function LifecycleBadge({ status, t }: { status: string; t: (key: string, params?: Record<string, string | number>) => string }) {
  const tone: Record<string, string> = {
    draft: 'background:#f1f5f9;color:#475569;border:1px solid #e2e8f0',
    posted: 'background:#eff6ff;color:#2563eb;border:1px solid #dbeafe',
    completed: 'background:#ecfdf5;color:#059669;border:1px solid #d1fae5',
    partially_returned: 'background:#fffbeb;color:#d97706;border:1px solid #fde68a',
    returned: 'background:#faf5ff;color:#7c3aed;border:1px solid #e9d5ff',
    cancelled: 'background:#fef2f2;color:#dc2626;border:1px solid #fecaca',
  }
  const style = tone[status] ?? tone.draft
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 8px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        textTransform: 'capitalize',
        whiteSpace: 'nowrap',
        ...parseStyle(style),
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: 999,
          background: 'currentColor',
          flex: 'none',
        }}
      />
      {t(lifecycleLabel(status), { defaultValue: status })}
    </span>
  )
}

function PaymentBadge({ state, t }: { state: string; t: (key: string, params?: Record<string, string | number>) => string }) {
  const tone: Record<string, string> = {
    unpaid: 'background:#fef2f2;color:#dc2626;border:1px solid #fecaca',
    partial: 'background:#eff6ff;color:#2563eb;border:1px solid #dbeafe',
    paid: 'background:#ecfdf5;color:#059669;border:1px solid #d1fae5',
  }
  const style = tone[state] ?? tone.unpaid
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '3px 8px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        textTransform: 'capitalize',
        whiteSpace: 'nowrap',
        ...parseStyle(style),
      }}
    >
      {t(paymentLabel(state), { defaultValue: state })}
    </span>
  )
}

function CalendarIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Create-draft dialog (Phase B) — keeps its own local form state; on success
// the parent navigates to /purchases/<id>. This is a self-contained island —
// no cross-component state to sync with URL filters.
// ---------------------------------------------------------------------------

interface CreateLineDraft {
  key: string
  product_id: string
  quantity: string
  unit_price: string
}

function emptyLine(): CreateLineDraft {
  return { key: crypto.randomUUID(), product_id: '', quantity: '1', unit_price: '' }
}

function CreatePurchaseDialog({
  suppliers,
  products,
  onClose,
  onCreated,
  onError,
  t,
}: {
  suppliers: Contact[]
  products: Product[]
  onClose: () => void
  onCreated: (id: number) => void
  onError: (msg: string) => void
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const [supplierId, setSupplierId] = useState('')
  const [purchaseDate, setPurchaseDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [referenceNo, setReferenceNo] = useState('')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<CreateLineDraft[]>([emptyLine()])
  const [shipAmount, setShipAmount] = useState('')
  const [shipPaidInCash, setShipPaidInCash] = useState(false)
  const [shipSupplierId, setShipSupplierId] = useState('')
  const [shipDescription, setShipDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const updateLine = (key: string, patch: Partial<CreateLineDraft>) =>
    setLines((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))

  const addLine = () => setLines((rows) => [...rows, emptyLine()])
  const removeLine = (key: string) =>
    setLines((rows) => (rows.length > 1 ? rows.filter((r) => r.key !== key) : rows))

  const productName = (id: string) => {
    const found = products.find((p) => String(p.id) === id)
    return found ? found.name : id ? `${t('purchases.product')} #${id}` : '—'
  }

  const previewSubtotal = lines.reduce((sum, l) => {
    const q = Number(l.quantity)
    const u = Number(l.unit_price)
    return sum + (Number.isFinite(q) && Number.isFinite(u) ? q * u : 0)
  }, 0)
  const previewShipping = Number(shipAmount) || 0
  const previewTotal = previewSubtotal + previewShipping

  const submit = async () => {
    setError(null)
    if (lines.length === 0) {
      setError(t('purchases.lineRequired'))
      return
    }
    const payloadLines: { product_id: number; quantity: number; unit_price: number }[] = []
    for (const l of lines) {
      if (!l.product_id) {
        setError(t('purchases.lineNeedsProduct'))
        return
      }
      const q = Number(l.quantity)
      const u = Number(l.unit_price)
      if (!Number.isFinite(q) || q <= 0) {
        setError(t('purchases.quantityPositive'))
        return
      }
      if (!Number.isFinite(u) || u < 0) {
        setError(t('purchases.unitPricePositive'))
        return
      }
      payloadLines.push({ product_id: Number(l.product_id), quantity: q, unit_price: u })
    }

    const body: Record<string, unknown> = { lines: payloadLines }
    if (supplierId) body.supplier_id = Number(supplierId)
    if (purchaseDate) body.purchase_date = new Date(`${purchaseDate}T00:00:00`).toISOString()
    if (referenceNo.trim()) body.reference_no = referenceNo.trim()
    if (notes.trim()) body.notes = notes.trim()
    if (shipAmount !== '' || shipDescription.trim() || shipSupplierId) {
      const shipping: Record<string, unknown> = {
        amount: Number(shipAmount) || 0,
        paid_in_cash: shipPaidInCash,
      }
      if (shipSupplierId) shipping.supplier_id = Number(shipSupplierId)
      if (shipDescription.trim()) shipping.description = shipDescription.trim()
      body.shipping = shipping
    }

    setBusy(true)
    try {
      const res = await api.headers.post<Purchase>('/purchases', body, {
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      const id = Number(res.data?.id)
      if (!Number.isFinite(id)) throw new Error(t('purchases.serverNoId'))
      onCreated(id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('purchases.createFailed')
      const code = (err as Error & { code?: string }).code
      if (code === 'conflict' || /reference|duplicate|unique/i.test(msg)) {
        setError(t('purchases.duplicateReference'))
      } else {
        setError(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  const labelStyle: React.CSSProperties = {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    color: '#8a98ab',
  }
  const fieldStyle: React.CSSProperties = {
    height: 34,
    borderRadius: 8,
    border: '1px solid var(--border)',
    background: 'white',
    padding: '0 10px',
    fontSize: 13,
    width: '100%',
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 40,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        background: 'rgba(15,23,42,.35)',
        padding: 24,
        overflowY: 'auto',
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 860,
          background: 'white',
          borderRadius: 14,
          border: '1px solid var(--border)',
          boxShadow: '0 20px 48px rgba(0,0,0,.12)',
          margin: '24px 0',
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
            <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>{t('purchases.newPurchaseDialog')}</h2>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: '#718198' }}>
              {t('purchases.creatingDraftHelp')}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ border: 0, background: 'none', color: '#8a98ab' }}>
            <X size={18} />
          </button>
        </div>
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Header fields */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('purchases.supplierOptional')}</label>
              <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} style={fieldStyle}>
                <option value="">{t('purchases.noSupplier')}</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('purchases.purchaseDate')}</label>
              <input
                type="date"
                value={purchaseDate}
                onChange={(e) => setPurchaseDate(e.target.value)}
                style={fieldStyle}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('purchases.referenceNoOptional')}</label>
              <input
                value={referenceNo}
                onChange={(e) => setReferenceNo(e.target.value)}
                maxLength={50}
                placeholder={t('purchases.referenceNoPlaceholder')}
                style={fieldStyle}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('purchases.notesOptional')}</label>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} style={fieldStyle} />
            </div>
          </div>

          {/* Items */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <h3 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>{t('purchases.purchaseItems')}</h3>
              <Button variant="outline" size="sm" onClick={addLine}>
                <Plus size={13} className="mr-1" /> {t('purchases.addLine')}
              </Button>
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>{t('purchases.product')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>{t('purchases.quantity')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>{t('purchases.unit')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>{t('purchases.unitPrice')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>{t('purchases.subtotal')}</th>
                    <th style={{ padding: '9px 12px', width: 40 }} />
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l) => {
                    const product = products.find((pr) => String(pr.id) === l.product_id)
                    const subtotal = (Number(l.quantity) || 0) * (Number(l.unit_price) || 0)
                    return (
                      <tr key={l.key} style={{ borderTop: '1px solid #f0f4f9' }}>
                        <td style={{ padding: '8px 12px', minWidth: 200 }}>
                          <select
                            value={l.product_id}
                            onChange={(e) => updateLine(l.key, { product_id: e.target.value })}
                            style={{ ...fieldStyle, height: 32 }}
                          >
                            <option value="">{t('purchases.selectProduct')}</option>
                            {products.map((pr) => (
                              <option key={pr.id} value={String(pr.id)}>
                                {pr.name}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td style={{ padding: '8px 12px' }}>
                          <input
                            type="number"
                            min="0"
                            step="0.0001"
                            value={l.quantity}
                            onChange={(e) => updateLine(l.key, { quantity: e.target.value })}
                            style={{ ...fieldStyle, height: 32, textAlign: 'right' }}
                          />
                        </td>
                        <td style={{ padding: '8px 12px', color: '#718198' }}>
                          {product?.unit_id ? `${t('purchases.unit')} #${product.unit_id}` : '—'}
                        </td>
                        <td style={{ padding: '8px 12px' }}>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={l.unit_price}
                            onChange={(e) => updateLine(l.key, { unit_price: e.target.value })}
                            style={{ ...fieldStyle, height: 32, textAlign: 'right' }}
                          />
                        </td>
                        <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, whiteSpace: 'nowrap' }}>
                          {formatIDR(subtotal)}
                        </td>
                        <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                          <button
                            onClick={() => removeLine(l.key)}
                            disabled={lines.length <= 1}
                            aria-label={t('purchases.removeLine')}
                            style={{ border: 0, background: 'none', color: lines.length <= 1 ? '#cbd5e1' : '#dc2626' }}
                          >
                            <Trash2 size={15} />
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p style={{ margin: '6px 0 0', fontSize: 11, color: '#94a3b8' }}>
              {t('purchases.previewNote')}
            </p>
          </div>

          {/* Shipping */}
          <div>
            <h3 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 8px' }}>
              {t('purchases.shipping')} <span style={{ fontWeight: 400, color: '#8a98ab' }}>({t('purchases.shippingHelp')})</span>
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={labelStyle}>{t('purchases.shippingAmount')}</label>
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={shipAmount}
                  onChange={(e) => setShipAmount(e.target.value)}
                  placeholder="0"
                  style={fieldStyle}
                />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={labelStyle}>{t('purchases.shippingSupplier')}</label>
                <select
                  value={shipSupplierId}
                  onChange={(e) => setShipSupplierId(e.target.value)}
                  style={fieldStyle}
                >
                  <option value="">{t('common.none')}</option>
                  {suppliers.map((s) => (
                    <option key={s.id} value={String(s.id)}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={labelStyle}>{t('purchases.description')}</label>
                <input
                  value={shipDescription}
                  onChange={(e) => setShipDescription(e.target.value)}
                  maxLength={500}
                  placeholder="e.g. DHL"
                  style={fieldStyle}
                />
              </div>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, fontSize: 13, color: '#334155' }}>
              <input
                type="checkbox"
                checked={shipPaidInCash}
                onChange={(e) => setShipPaidInCash(e.target.checked)}
              />
              {t('purchases.shippingPaidCash')}
            </label>
          </div>

          {/* Summary */}
          <div style={{ background: '#f8fafc', border: '1px solid var(--border)', borderRadius: 10, padding: 14, fontSize: 13 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ color: '#718198' }}>{t('purchases.lineSubtotal')}</span>
              <span>{formatIDR(previewSubtotal)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ color: '#718198' }}>{t('purchases.shipping')}</span>
              <span>{formatIDR(previewShipping)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 8, fontWeight: 700 }}>
              <span>{t('purchases.totalPreview')}</span>
              <span>{formatIDR(previewTotal)}</span>
            </div>
          </div>

          {error && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '10px 12px',
                borderRadius: 8,
                background: '#fef2f2',
                color: '#dc2626',
                fontSize: 13,
                border: '1px solid #fecaca',
              }}
            >
              <AlertTriangle size={15} /> {error}
            </div>
          )}
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            padding: '14px 20px',
            borderTop: '1px solid var(--border)',
          }}
        >
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? t('purchases.creating') : t('purchases.createDraft')}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main table island
// ---------------------------------------------------------------------------

export default function PurchasesDataTable({
  initialPurchases,
  initialPagination,
  initialSuppliers,
  initialProducts,
}: Props) {
  const { t } = useLanguage()
  const router = useRouter()
  const searchParams = useSearchParams()
  const user = useSession()
  const canView = user.capabilities.includes('purchase.view')
  const canCreate = user.capabilities.includes('purchase.create')

  // Sync local state with server-provided initial data (RSC re-render on URL change)
  const [purchases, setPurchases] = useState<Purchase[]>(initialPurchases)
  const [pagination, setPagination] = useState<Pagination>(initialPagination)
  const [suppliers, setSuppliers] = useState<Contact[]>(initialSuppliers)
  const [products, setProducts] = useState<Product[]>(initialProducts)
  const [createOpen, setCreateOpen] = useState(false)
  const [notice, setNotice] = useState('')

  // URL-driven filters (read from searchParams on every RSC re-render)
  const query = searchParams.get('q') || ''
  const [queryInput, setQueryInput] = useState(query)
  const lifecycleFilter = searchParams.get('filter[lifecycle_status]') || ''
  const paymentFilter = searchParams.get('filter[payment_state]') || ''
  const supplierFilter = searchParams.get('filter[supplier_id]') || ''
  const fromDate = searchParams.get('from') || ''
  const toDate = searchParams.get('to') || ''
  const page = parseInt(searchParams.get('page') || '1')
  const perPage = parseInt(searchParams.get('per_page') || '25')
  const sortKey = (searchParams.get('sort') as string) || 'purchase_date'
  const sortDir = (searchParams.get('dir') as string) || 'desc'

  // Sync queryInput when URL changes externally (back/forward, clear, mutation reset)
  useEffect(() => {
    setQueryInput(query)
  }, [query])

  // Debounce: local input → URL update
  const debounceTimer = useRef<number | null>(null)
  const handleQueryChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value
    setQueryInput(val)
    if (debounceTimer.current) window.clearTimeout(debounceTimer.current)
    debounceTimer.current = window.setTimeout(() => {
      if (val.trim()) {
        updateUrl({ q: val.trim(), page: '1' })
      } else {
        updateUrl({ q: '', page: '1' })
      }
    }, 300) as unknown as number
  }
  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (debounceTimer.current) window.clearTimeout(debounceTimer.current)
    }
  }, [])

  // Sync from server initial data when RSC re-renders (URL change)
  const syncFromServer = () => {
    setPurchases(initialPurchases)
    setPagination(initialPagination)
    setSuppliers(initialSuppliers)
    setProducts(initialProducts)
  }
  // Effect runs when initial props change (RSC re-fetch)
  useEffect(() => {
    syncFromServer()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialPurchases, initialPagination])

  const updateUrl = (newParams: Record<string, string>) => {
    const params = new URLSearchParams(searchParams)
    Object.entries(newParams).forEach(([key, value]) => {
      if (value) params.set(key, value)
      else params.delete(key)
    })
    router.replace(`/purchases?${params.toString()}`, { scroll: false })
  }

  const resetUrl = () => {
    router.replace('/purchases', { scroll: false })
  }

  const clearFilters = () => {
    resetUrl()
  }

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const supplierName = useMemo(() => {
    const m = new Map<number, string>()
    suppliers.forEach((s) => m.set(s.id, s.name))
    return m
  }, [suppliers])

  const handleSort = (key: string) => {
    const newDir = sortKey === key && sortDir === 'asc' ? 'desc' : 'asc'
    updateUrl({ sort: key, dir: newDir, page: '1' })
  }

  const handleCreateDone = (id: number) => {
    setCreateOpen(false)
    toast(t('purchases.createdToast'))
    router.push(`/purchases/${id}`)
  }

  const activeFilterCount = [
    query.trim(),
    lifecycleFilter,
    paymentFilter,
    supplierFilter,
    fromDate || toDate,
    sortKey !== 'purchase_date' ? sortKey : '',
    sortDir !== 'desc' ? sortDir : '',
  ].filter(Boolean).length

  if (!canView) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">purchases.eyebrow</div>
            <h1>purchases.title</h1>
            <p>purchases.subtitle</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>{t('errors.forbidden')}</strong>
          <p>{t('errors.lacksCapability', { capability: 'purchase.view' })}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Toast */}
      {notice && (
        <div
          role="status"
          style={{
            position: 'fixed',
            bottom: 20,
            right: 20,
            zIndex: 50,
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

      {/* Heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('purchases.eyebrow')}</div>
          <h1>{t('purchases.title')}</h1>
          <p>{t('purchases.subtitle')}</p>
        </div>
        {canCreate && (
          <div className="heading-actions">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus size={14} className="mr-1" /> {t('purchases.newPurchase')}
            </Button>
          </div>
        )}
      </div>

      {/* Filters */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 10,
          alignItems: 'flex-end',
          marginBottom: 16,
          background: 'white',
          border: '1px solid var(--border)',
          borderRadius: 10,
          padding: 14,
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: '1 1 200px', minWidth: 180 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('common.search')}
          </label>
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
            <Search size={14} style={{ position: 'absolute', left: 10, color: '#9aa7b8' }} />
            <input
              value={queryInput}
              onChange={handleQueryChange}
              placeholder={t('purchases.searchPlaceholder')}
              style={{
                width: '100%',
                height: 34,
                paddingLeft: 30,
                paddingRight: 10,
                borderRadius: 8,
                border: '1px solid var(--border)',
                fontSize: 13,
                outline: 'none',
                background: 'white',
              }}
            />
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('purchases.lifecycle')}
          </label>
          <select
            value={lifecycleFilter}
            onChange={(e) => updateUrl({ 'filter[lifecycle_status]': e.target.value, page: '1' })}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          >
            <option value="">{t('purchases.allStatuses')}</option>
            <option value="draft">{t('status.draft')}</option>
            <option value="posted">{t('status.posted')}</option>
            <option value="completed">{t('status.completed')}</option>
            <option value="partially_returned">{t('status.partiallyReturned')}</option>
            <option value="returned">{t('status.returned')}</option>
            <option value="cancelled">{t('status.cancelled')}</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('purchases.payment')}
          </label>
          <select
            value={paymentFilter}
            onChange={(e) => updateUrl({ 'filter[payment_state]': e.target.value, page: '1' })}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          >
            <option value="">{t('purchases.all')}</option>
            <option value="unpaid">{t('status.unpaid')}</option>
            <option value="partial">{t('status.partial')}</option>
            <option value="paid">{t('status.paid')}</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('purchases.supplier')}
          </label>
          <select
            value={supplierFilter}
            onChange={(e) => updateUrl({ 'filter[supplier_id]': e.target.value, page: '1' })}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13, minWidth: 150 }}
          >
            <option value="">{t('purchases.allSuppliers')}</option>
            {suppliers.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('purchases.from')}
          </label>
          <input
            type="date"
            value={fromDate ? new Date(fromDate).toISOString().slice(0, 10) : ''}
            onChange={(e) => {
              const d = e.target.value
              updateUrl({ from: d ? new Date(`${d}T00:00:00`).toISOString() : '', page: '1' })
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('purchases.to')}
          </label>
          <input
            type="date"
            value={toDate ? new Date(toDate).toISOString().slice(0, 10) : ''}
            onChange={(e) => {
              const d = e.target.value
              updateUrl({ to: d ? new Date(`${d}T23:59:59`).toISOString() : '', page: '1' })
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          />
        </div>

        <Button variant="outline" size="sm" onClick={clearFilters} style={{ height: 34 }}>
          {t('common.clearFilters')}
        </Button>
      </div>

      {/* Table */}
      <section
        style={{
          background: 'white',
          border: '1px solid var(--border)',
          borderRadius: 10,
          overflow: 'hidden',
        }}
      >
        {purchases.length === 0 ? (
          <div className="error-state" style={{ padding: 48 }}>
            <div className="error-icon" style={{ background: '#eff6ff', color: 'var(--primary)' }}>
              <ClipboardList size={28} />
            </div>
            <strong>{t('purchases.noPurchasesFound')}</strong>
            <p style={{ maxWidth: 420, color: '#718198', fontSize: 13, textAlign: 'center' }}>
              {activeFilterCount > 0
                ? t('purchases.noPurchasesMatchFilters')
                : t('purchases.purchasesAppearHere')}
            </p>
            {activeFilterCount > 0 && (
              <Button variant="outline" onClick={clearFilters}>
                {t('common.clearFilters')}
              </Button>
            )}
          </div>
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', textAlign: 'left', fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', color: '#64748b' }}>
                    <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      <button
                        onClick={() => handleSort('id')}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'none', border: 0, color: 'inherit', font: 'inherit', cursor: 'pointer' }}
                      >
                        {t('purchases.purchase')} <ChevronsUpDown size={12} style={{ opacity: sortKey === 'id' ? 1 : 0.35 }} />
                      </button>
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>{t('purchases.supplier')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      <button
                        onClick={() => handleSort('purchase_date')}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'none', border: 0, color: 'inherit', font: 'inherit', cursor: 'pointer' }}
                      >
                        {t('purchases.purchaseDate')} <ChevronsUpDown size={12} style={{ opacity: sortKey === 'purchase_date' ? 1 : 0.35 }} />
                      </button>
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>{t('purchases.total')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>{t('purchases.payment')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>{t('purchases.lifecycle')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {purchases.map((p) => {
                    const ref = p.reference_no ? p.reference_no : `#${p.id}`
                    const supplier = p.supplier_id ? (supplierName.get(p.supplier_id) ?? `${t('purchases.supplier')} #${p.supplier_id}`) : '—'
                    return (
                      <tr key={p.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                        <td style={{ padding: '12px 14px' }}>
                          <Link href={`/purchases/${p.id}`} style={{ fontWeight: 600, color: 'var(--primary)', textDecoration: 'none' }}>
                            {ref}
                          </Link>
                          <div style={{ fontSize: 11, color: '#8a98ab', marginTop: 2 }}>#{p.id}</div>
                        </td>
                        <td style={{ padding: '12px 14px', color: '#334155', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{supplier}</td>
                        <td style={{ padding: '12px 14px', color: '#475569', whiteSpace: 'nowrap' }}>{fmtDate(p.purchase_date)}</td>
                        <td style={{ padding: '12px 14px', textAlign: 'right', fontWeight: 700, whiteSpace: 'nowrap' }}>{formatIDR(p.total_amount)}</td>
                        <td style={{ padding: '12px 14px' }}>
                          <PaymentBadge state={p.payment_state} t={t} />
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <LifecycleBadge status={p.lifecycle_status} t={t} />
                        </td>
                        <td style={{ padding: '12px 14px', textAlign: 'right' }}>
                          <Link
                            href={`/purchases/${p.id}`}
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 6,
                              padding: '6px 10px',
                              borderRadius: 6,
                              border: '1px solid var(--border)',
                              background: 'white',
                              color: '#334155',
                              fontSize: 12,
                              fontWeight: 600,
                              textDecoration: 'none',
                            }}
                          >
                            {t('purchases.view')}
                          </Link>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '12px 14px',
                borderTop: '1px solid var(--border)',
                fontSize: 13,
                color: '#718198',
                flexWrap: 'wrap',
                gap: 8,
              }}
            >
              <span>
                {t('common.showing')} {purchases.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0}–
                {Math.min(pagination.page * pagination.per_page, pagination.total)} {t('common.of')} {pagination.total}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <select
                  value={perPage}
                  onChange={(e) => {
                    updateUrl({ per_page: e.target.value, page: '1' })
                  }}
                  style={{ height: 32, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 8px', fontSize: 12 }}
                  aria-label="Rows per page"
                >
                  <option value={10}>10 / {t('common.page')}</option>
                  <option value={25}>25 / {t('common.page')}</option>
                  <option value={50}>50 / {t('common.page')}</option>
                </select>
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => updateUrl({ page: String(pagination.page - 1) })}>
                  <ChevronLeft size={14} />
                </Button>
                <span style={{ fontSize: 12, padding: '0 6px' }}>
                  {t('common.page')} {pagination.page} {t('common.of')} {pagination.total_pages}
                </span>
                <Button variant="outline" size="sm" disabled={page >= pagination.total_pages} onClick={() => updateUrl({ page: String(pagination.page + 1) })}>
                  <ChevronRight size={14} />
                </Button>
              </div>
            </div>
          </>
        )}
      </section>

      {/* Create draft dialog */}
      {createOpen && (
        <CreatePurchaseDialog
          suppliers={suppliers}
          products={products}
          onClose={() => setCreateOpen(false)}
          onCreated={handleCreateDone}
          onError={(msg) => toast(msg)}
          t={t}
        />
      )}
    </div>
  )
}
