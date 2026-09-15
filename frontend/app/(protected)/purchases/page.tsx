'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
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
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api-client'
import type { Pagination, Purchase } from '@/lib/purchase-types'
import type { Contact, ContactListResponse } from '@/lib/contact-types'
import type { Product } from '@/lib/product-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'

// ---------------------------------------------------------------------------
// Backend contract (from openapi.yaml §13 + backend/app/api/v1/purchases.py):
//   GET /purchases?page=&per_page=&q=&sort=
//       &filter[lifecycle_status]=draft|posted|completed|partially_returned|returned|cancelled
//       &filter[supplier_id]=int&filter[payment_state]=unpaid|partial|paid
//       &from=ISO &to=ISO
//     → { data: Purchase[], pagination }  (capability purchase.view)
//   Purchase = { id, reference_no, supplier_id, purchase_date, lifecycle_status,
//                payment_state, total_amount, paid_amount, outstanding, ap,
//                supplier_receivable, created_at, updated_at, version, etag }
//   total_amount is server-derived (Σ line_total + shipping) — never calc client.
//   Filter bracket aliases fixed in backend (alias="filter[...]") + bare fallback.
//   Supplier names resolved via GET /contacts?filter[type]=supplier (contact.view).
// ---------------------------------------------------------------------------

interface PurchaseListResponse {
  data: Purchase[]
  pagination: Pagination
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
  return s
}

function paymentLabel(s: string): string {
  return s
}

function LifecycleBadge({ status }: { status: string }) {
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
      {lifecycleLabel(status)}
    </span>
  )
}

function PaymentBadge({ state }: { state: string }) {
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
      {paymentLabel(state)}
    </span>
  )
}

function parseStyle(s: string): Record<string, string> {
  const out: Record<string, string> = {}
  s.split(';').forEach((part) => {
    const [k, v] = part.split(':').map((x) => x?.trim())
    if (k && v) out[k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v
  })
  return out
}

export default function PurchasesPage() {
  const user = useSession()
  const router = useRouter()
  const canView = user.capabilities.includes('purchase.view')
  const canCreate = user.capabilities.includes('purchase.create')

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [purchases, setPurchases] = useState<Purchase[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)
  const [suppliers, setSuppliers] = useState<Contact[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [createOpen, setCreateOpen] = useState(false)

  // Filters
  const [query, setQuery] = useState('')
  const [lifecycleFilter, setLifecycleFilter] = useState<string>('all')
  const [paymentFilter, setPaymentFilter] = useState<string>('all')
  const [supplierFilter, setSupplierFilter] = useState<string>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [sortKey, setSortKey] = useState('purchase_date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const [notice, setNotice] = useState('')

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const supplierName = useMemo(() => {
    const m = new Map<number, string>()
    suppliers.forEach((s) => m.set(s.id, s.name))
    return m
  }, [suppliers])

  const fetchSuppliers = useCallback(async () => {
    try {
      const res = await api.get<ContactListResponse>('/contacts', {
        params: { 'filter[type]': 'supplier', per_page: '500' },
      })
      setSuppliers(res.data)
    } catch {
      // enrichment only
    }
  }, [])

  const fetchProducts = useCallback(async () => {
    try {
      const res = await api.get<{ data: Product[] }>('/products', {
        params: { 'filter[is_purchasable]': 'true', per_page: '500' },
      })
      setProducts(res.data)
    } catch {
      // enrichment only — create dialog can still accept a product id
    }
  }, [])

  useEffect(() => {
    fetchSuppliers()
    fetchProducts()
  }, [fetchSuppliers, fetchProducts])

  const fetchPurchases = useCallback(async () => {
    if (!canView) {
      setLoading(false)
      setError('Missing capability: purchase.view')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        per_page: String(perPage),
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
      }
      if (query.trim()) params.q = query.trim()
      if (lifecycleFilter !== 'all') params['filter[lifecycle_status]'] = lifecycleFilter
      if (paymentFilter !== 'all') params['filter[payment_state]'] = paymentFilter
      if (supplierFilter !== 'all') params['filter[supplier_id]'] = supplierFilter
      if (fromDate) params.from = new Date(fromDate).toISOString()
      if (toDate) params.to = new Date(toDate).toISOString()

      const res = await api.get<PurchaseListResponse>('/purchases', { params })
      setPurchases(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load purchases')
    } finally {
      setLoading(false)
    }
  }, [canView, page, perPage, query, lifecycleFilter, paymentFilter, supplierFilter, fromDate, toDate, sortKey, sortDir])

  useEffect(() => {
    fetchPurchases()
  }, [fetchPurchases])

  const handleSort = (key: string) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir('asc')
    }
    setPage(1)
  }

  const clearFilters = () => {
    setLifecycleFilter('all')
    setPaymentFilter('all')
    setSupplierFilter('all')
    setFromDate('')
    setToDate('')
    setQuery('')
    setPage(1)
  }

  if (!canView && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Purchasing / Purchases</div>
            <h1>Purchases</h1>
            <p>Manage purchase orders and supplier invoices.</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>Forbidden</strong>
          <p>Your account lacks the purchase.view capability.</p>
        </div>
      </div>
    )
  }

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Purchasing / Purchases</div>
            <h1>Purchases</h1>
            <p>Manage purchase orders and supplier invoices.</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>Failed to load purchases</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchPurchases}>
            Retry
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">Purchasing / Purchases</div>
          <h1>Purchases</h1>
          <p>Manage purchase orders and supplier invoices.</p>
        </div>
        {canCreate && (
          <div className="heading-actions">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus size={14} className="mr-1" /> New purchase
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
            Search
          </label>
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
            <Search size={14} style={{ position: 'absolute', left: 10, color: '#9aa7b8' }} />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setPage(1)
              }}
              placeholder="Reference no… (q is reference search only)"
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
            Lifecycle
          </label>
          <select
            value={lifecycleFilter}
            onChange={(e) => {
              setLifecycleFilter(e.target.value)
              setPage(1)
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          >
            <option value="all">All statuses</option>
            <option value="draft">draft</option>
            <option value="posted">posted</option>
            <option value="completed">completed</option>
            <option value="partially_returned">partially_returned</option>
            <option value="returned">returned</option>
            <option value="cancelled">cancelled</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            Payment
          </label>
          <select
            value={paymentFilter}
            onChange={(e) => {
              setPaymentFilter(e.target.value)
              setPage(1)
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          >
            <option value="all">All</option>
            <option value="unpaid">unpaid</option>
            <option value="partial">partial</option>
            <option value="paid">paid</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            Supplier
          </label>
          <select
            value={supplierFilter}
            onChange={(e) => {
              setSupplierFilter(e.target.value)
              setPage(1)
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13, minWidth: 150 }}
          >
            <option value="all">All suppliers</option>
            {suppliers.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            From
          </label>
          <input
            type="date"
            value={fromDate}
            onChange={(e) => {
              setFromDate(e.target.value)
              setPage(1)
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            To
          </label>
          <input
            type="date"
            value={toDate}
            onChange={(e) => {
              setToDate(e.target.value)
              setPage(1)
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          />
        </div>

        <Button variant="outline" size="sm" onClick={clearFilters} style={{ height: 34 }}>
          Clear
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
        {loading ? (
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="skeleton" style={{ height: 46, borderRadius: 8 }} />
            ))}
          </div>
        ) : purchases.length === 0 ? (
          <div className="error-state" style={{ padding: 48 }}>
            <div className="error-icon" style={{ background: '#eff6ff', color: 'var(--primary)' }}>
              <ClipboardList size={28} />
            </div>
            <strong>No purchases found</strong>
            <p style={{ maxWidth: 420, color: '#718198', fontSize: 13, textAlign: 'center' }}>
              {query || lifecycleFilter !== 'all' || paymentFilter !== 'all' || supplierFilter !== 'all' || fromDate || toDate
                ? 'No purchases match the current filters. Try clearing filters.'
                : 'No purchase records yet. Purchases will appear here once created.'}
            </p>
            {(query || lifecycleFilter !== 'all' || paymentFilter !== 'all' || supplierFilter !== 'all' || fromDate || toDate) && (
              <Button variant="outline" onClick={clearFilters}>
                Clear filters
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
                        Purchase <ChevronsUpDown size={12} style={{ opacity: sortKey === 'id' ? 1 : 0.35 }} />
                      </button>
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>Supplier</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      <button
                        onClick={() => handleSort('purchase_date')}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'none', border: 0, color: 'inherit', font: 'inherit', cursor: 'pointer' }}
                      >
                        Purchase date <ChevronsUpDown size={12} style={{ opacity: sortKey === 'purchase_date' ? 1 : 0.35 }} />
                      </button>
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Total</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>Payment</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>Lifecycle</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {purchases.map((p) => {
                    const ref = p.reference_no ? p.reference_no : `#${p.id}`
                    const supplier = p.supplier_id ? (supplierName.get(p.supplier_id) ?? `Supplier #${p.supplier_id}`) : '—'
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
                          <PaymentBadge state={p.payment_state} />
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <LifecycleBadge status={p.lifecycle_status} />
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
                            View
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
                Showing {purchases.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0}–
                {Math.min(pagination.page * pagination.per_page, pagination.total)} of {pagination.total}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <select
                  value={perPage}
                  onChange={(e) => {
                    setPerPage(Number(e.target.value))
                    setPage(1)
                  }}
                  style={{ height: 32, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 8px', fontSize: 12 }}
                  aria-label="Rows per page"
                >
                  <option value={10}>10 / page</option>
                  <option value={25}>25 / page</option>
                  <option value={50}>50 / page</option>
                </select>
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  <ChevronLeft size={14} />
                </Button>
                <span style={{ fontSize: 12, padding: '0 6px' }}>
                  Page {pagination.page} of {pagination.total_pages}
                </span>
                <Button variant="outline" size="sm" disabled={page >= pagination.total_pages} onClick={() => setPage((p) => p + 1)}>
                  <ChevronRight size={14} />
                </Button>
              </div>
            </div>
          </>
        )}
      </section>

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

      {/* Create draft dialog (Phase B) */}
      {createOpen && (
        <CreatePurchaseDialog
          suppliers={suppliers}
          products={products}
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false)
            toast('Draft purchase created')
            router.push(`/purchases/${id}`)
          }}
          onError={(msg) => toast(msg)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Create draft dialog — POST /purchases (capability purchase.create)
//
// Body: PurchaseCreateRequest { supplier_id?, purchase_date?, notes?,
//   reference_no?, lines[>=1] {product_id, quantity, unit_price}, shipping? }
// Lines are always sent inline on create (the backend accepts them in one
// request). Server is authoritative for line_subtotal / allocated_shipping /
// line_total / total_amount — the dialog only previews qty x unit_price.
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
}: {
  suppliers: Contact[]
  products: Product[]
  onClose: () => void
  onCreated: (id: number) => void
  onError: (msg: string) => void
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
    return found ? found.name : id ? `Product #${id}` : '—'
  }

  // Preview only — the server recomputes line_subtotal / line_total / totals.
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
      setError('At least one line is required.')
      return
    }
    const payloadLines = []
    for (const l of lines) {
      if (!l.product_id) {
        setError('Every line needs a product.')
        return
      }
      const q = Number(l.quantity)
      const u = Number(l.unit_price)
      if (!Number.isFinite(q) || q <= 0) {
        setError('Line quantity must be greater than 0.')
        return
      }
      if (!Number.isFinite(u) || u < 0) {
        setError('Line unit price must be 0 or greater.')
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
      if (!Number.isFinite(id)) throw new Error('Server did not return a purchase id.')
      onCreated(id)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Create failed'
      const code = (err as Error & { code?: string }).code
      if (code === 'conflict' || /reference|duplicate|unique/i.test(msg)) {
        setError('That reference number is already in use. Choose a different one.')
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
            <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>New Purchase</h2>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: '#718198' }}>
              Creates a draft. Nothing affects stock until the purchase is posted later.
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
              <label style={labelStyle}>Supplier (optional)</label>
              <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)} style={fieldStyle}>
                <option value="">No supplier (counter / cash buy)</option>
                {suppliers.map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>Purchase date</label>
              <input
                type="date"
                value={purchaseDate}
                onChange={(e) => setPurchaseDate(e.target.value)}
                style={fieldStyle}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>Reference no (optional)</label>
              <input
                value={referenceNo}
                onChange={(e) => setReferenceNo(e.target.value)}
                maxLength={50}
                placeholder="Unique supplier invoice / PO ref"
                style={fieldStyle}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>Notes (optional)</label>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} style={fieldStyle} />
            </div>
          </div>

          {/* Items */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <h3 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>Purchase items</h3>
              <Button variant="outline" size="sm" onClick={addLine}>
                <Plus size={13} className="mr-1" /> Add line
              </Button>
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>Product</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>Quantity</th>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>Unit</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>Unit price</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>Subtotal</th>
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
                            <option value="">Select product…</option>
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
                          {product?.unit_id ? `unit #${product.unit_id}` : '—'}
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
                            aria-label="Remove line"
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
              Subtotal is a preview of quantity × unit price. The server computes the authoritative totals and
              shipping allocation on save.
            </p>
          </div>

          {/* Shipping */}
          <div>
            <h3 style={{ fontSize: 13, fontWeight: 700, margin: '0 0 8px' }}>
              Shipping <span style={{ fontWeight: 400, color: '#8a98ab' }}>(landed cost — optional)</span>
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={labelStyle}>Shipping amount</label>
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
                <label style={labelStyle}>Shipping supplier (optional)</label>
                <select
                  value={shipSupplierId}
                  onChange={(e) => setShipSupplierId(e.target.value)}
                  style={fieldStyle}
                >
                  <option value="">None</option>
                  {suppliers.map((s) => (
                    <option key={s.id} value={String(s.id)}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <label style={labelStyle}>Description</label>
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
              Shipping paid in cash now
            </label>
          </div>

          {/* Summary */}
          <div style={{ background: '#f8fafc', border: '1px solid var(--border)', borderRadius: 10, padding: 14, fontSize: 13 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ color: '#718198' }}>Line subtotal</span>
              <span>{formatIDR(previewSubtotal)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ color: '#718198' }}>Shipping</span>
              <span>{formatIDR(previewShipping)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 8, fontWeight: 700 }}>
              <span>Total (preview)</span>
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
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Creating…' : 'Create draft'}
          </Button>
        </div>
      </div>
    </div>
  )
}
