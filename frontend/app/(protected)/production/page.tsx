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
import { formatIDR, formatDate } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import type { Pagination, ProductionRun } from '@/lib/production-types'
import type { Product } from '@/lib/product-types'
import type { CostType } from '@/lib/production-types'

// ---------------------------------------------------------------------------
// Backend contract (openapi.yaml §15 + backend/app/api/v1/production_runs.py):
//   GET /production-runs?page=&per_page=&sort=&q=&from=ISO&to=ISO
//       &filter[lifecycle_status]=draft|posted|completed|cancelled
//       &filter[output_product_id]=int
//     → { data: ProductionRun[], pagination }  (capability production.view)
//   POST /production-runs (Idempotency-Key required, capability production.create)
//     body: { run_date?, output_product_id, output_quantity, notes?,
//             inputs: [{product_id, quantity}], cost_lines?: [{cost_type_id, amount, paid_in_cash?, description?}] }
//   Output product must have is_producible = TRUE and be active.
// ---------------------------------------------------------------------------

interface ProductionRunListResponse {
  data: ProductionRun[]
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

function statusLabel(s: string): string {
  const map: Record<string, string> = {
    draft: 'status.draft',
    posted: 'status.posted',
    completed: 'status.completed',
    cancelled: 'status.cancelled',
  }
  return map[s] ?? s
}

function StatusBadge({ status, t }: { status: string; t: (key: string, params?: Record<string, string | number>) => string }) {
  const tone: Record<string, string> = {
    draft: 'background:#f1f5f9;color:#475569;border:1px solid #e2e8f0',
    posted: 'background:#eff6ff;color:#2563eb;border:1px solid #dbeafe',
    completed: 'background:#ecfdf5;color:#059669;border:1px solid #d1fae5',
    cancelled: 'background:#fef2f2;color:#dc2626;border:1px solid #fecaca',
  }
  const style = tone[status] ?? tone.draft
  const parsed: Record<string, string> = {}
  style.split(';').forEach((part) => {
    const [k, v] = part.split(':').map((x) => x?.trim())
    if (k && v) parsed[k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v
  })
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
        ...parsed,
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
      {t(statusLabel(status), { defaultValue: status })}
    </span>
  )
}

export default function ProductionPage() {
  const user = useSession()
  const { t } = useLanguage()
  const router = useRouter()
  const canView = user.capabilities.includes('production.view')
  const canCreate = user.capabilities.includes('production.create')

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [runs, setRuns] = useState<ProductionRun[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)
  const [products, setProducts] = useState<Product[]>([])
  const [costTypes, setCostTypes] = useState<CostType[]>([])
  const [createOpen, setCreateOpen] = useState(false)

  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [productFilter, setProductFilter] = useState<string>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [sortKey, setSortKey] = useState('run_date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const [notice, setNotice] = useState('')
  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const productName = useMemo(() => {
    const m = new Map<number, string>()
    products.forEach((p) => m.set(p.id, p.name))
    return m
  }, [products])

  const fetchProducts = useCallback(async () => {
    try {
      const res = await api.get<{ data: Product[] }>('/products', {
        params: { per_page: '500' },
      })
      setProducts(res.data.filter((p) => p.is_producible && p.is_active))
    } catch {
      // enrichment only
    }
  }, [])

  const fetchCostTypes = useCallback(async () => {
    try {
      const res = await api.get<{ data: CostType[] }>('/cost-types', {
        params: { 'filter[is_active]': 'true', per_page: '200' },
      })
      setCostTypes(res.data)
    } catch {
      // enrichment only
    }
  }, [])

  useEffect(() => {
    fetchProducts()
    fetchCostTypes()
  }, [fetchProducts, fetchCostTypes])

  const fetchRuns = useCallback(async () => {
    if (!canView) {
      setLoading(false)
      setError(t('errors.missingCapability', { capability: 'production.view' }))
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
      if (statusFilter !== 'all') params['filter[lifecycle_status]'] = statusFilter
      if (productFilter !== 'all') params['filter[output_product_id]'] = productFilter
      if (fromDate) params.from = new Date(`${fromDate}T00:00:00`).toISOString()
      if (toDate) params.to = new Date(`${toDate}T23:59:59`).toISOString()

      const res = await api.get<ProductionRunListResponse>('/production-runs', { params })
      setRuns(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('production.failedToLoad'))
    } finally {
      setLoading(false)
    }
  }, [canView, page, perPage, query, statusFilter, productFilter, fromDate, toDate, sortKey, sortDir])

  useEffect(() => {
    fetchRuns()
  }, [fetchRuns])

  const handleSort = (key: string) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir('asc')
    }
    setPage(1)
  }

  const clearFilters = () => {
    setStatusFilter('all')
    setProductFilter('all')
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
            <div className="eyebrow">{t('production.eyebrow')}</div>
            <h1>{t('production.title')}</h1>
            <p>{t('production.subtitle')}</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>{t('errors.forbidden')}</strong>
          <p>{t('errors.lacksCapability', { capability: 'production.view' })}</p>
        </div>
      </div>
    )
  }

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">{t('production.eyebrow')}</div>
            <h1>{t('production.title')}</h1>
            <p>{t('production.subtitle')}</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>{t('production.failedToLoad')}</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchRuns}>
            {t('common.retry')}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('production.eyebrow')}</div>
          <h1>{t('production.title')}</h1>
          <p>{t('production.subtitle')}</p>
        </div>
        {canCreate && (
          <div className="heading-actions">
            <Button onClick={() => setCreateOpen(true)}>
              <Plus size={14} className="mr-1" /> {t('production.newRun')}
            </Button>
          </div>
        )}
      </div>

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
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setPage(1)
              }}
              placeholder={t('production.searchPlaceholder')}
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
            {t('production.status')}
          </label>
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value)
              setPage(1)
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          >
            <option value="all">{t('production.allStatuses')}</option>
            <option value="draft">{t('status.draft')}</option>
            <option value="posted">{t('status.posted')}</option>
            <option value="cancelled">{t('status.cancelled')}</option>
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('production.outputProduct')}
          </label>
          <select
            value={productFilter}
            onChange={(e) => {
              setProductFilter(e.target.value)
              setPage(1)
            }}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13, minWidth: 150 }}
          >
            <option value="all">{t('production.allProducts')}</option>
            {products.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            {t('production.from')}
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
            {t('production.to')}
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
          {t('common.clearFilters')}
        </Button>
      </div>

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
        ) : runs.length === 0 ? (
          <div className="error-state" style={{ padding: 48 }}>
            <div className="error-icon" style={{ background: '#eff6ff', color: 'var(--primary)' }}>
              <ClipboardList size={28} />
            </div>
            <strong>{t('production.noRunsFound')}</strong>
            <p style={{ maxWidth: 420, color: '#718198', fontSize: 13, textAlign: 'center' }}>
              {query || statusFilter !== 'all' || productFilter !== 'all' || fromDate || toDate
                ? t('production.noRunsMatchFilters')
                : t('production.runsAppearHere')}
            </p>
            {(query || statusFilter !== 'all' || productFilter !== 'all' || fromDate || toDate) && (
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
                        {t('production.run')} <ChevronsUpDown size={12} style={{ opacity: sortKey === 'id' ? 1 : 0.35 }} />
                      </button>
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>{t('production.outputProduct')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      <button
                        onClick={() => handleSort('run_date')}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'none', border: 0, color: 'inherit', font: 'inherit', cursor: 'pointer' }}
                      >
                        {t('production.runDate')} <ChevronsUpDown size={12} style={{ opacity: sortKey === 'run_date' ? 1 : 0.35 }} />
                      </button>
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>{t('production.outputQuantity')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>{t('production.finishedUnitCost')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>{t('production.totalCost')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>{t('production.status')}</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => {
                    const product = productName.get(run.output_product_id) ?? `${t('production.product')} #${run.output_product_id}`
                    const totalCost = run.total_raw_cost + run.total_overhead_cost
                    return (
                      <tr key={run.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                        <td style={{ padding: '12px 14px' }}>
                          <Link href={`/production/${run.id}`} style={{ fontWeight: 600, color: 'var(--primary)', textDecoration: 'none' }}>
                            #{run.id}
                          </Link>
                        </td>
                        <td style={{ padding: '12px 14px', color: '#334155', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{product}</td>
                        <td style={{ padding: '12px 14px', color: '#475569', whiteSpace: 'nowrap' }}>{formatDate(run.run_date)}</td>
                        <td style={{ padding: '12px 14px', textAlign: 'right' }}>{run.output_quantity}</td>
                        <td style={{ padding: '12px 14px', textAlign: 'right', fontWeight: 600, whiteSpace: 'nowrap' }}>{formatIDR(run.finished_unit_cost)}</td>
                        <td style={{ padding: '12px 14px', textAlign: 'right', fontWeight: 600, whiteSpace: 'nowrap' }}>{formatIDR(totalCost)}</td>
                        <td style={{ padding: '12px 14px' }}>
                          <StatusBadge status={run.lifecycle_status} t={t} />
                        </td>
                        <td style={{ padding: '12px 14px', textAlign: 'right' }}>
                          <Link
                            href={`/production/${run.id}`}
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
                            {t('production.view')}
                          </Link>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

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
                {t('common.showing')} {runs.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0}–
                {Math.min(pagination.page * pagination.per_page, pagination.total)} {t('common.of')} {pagination.total}
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
                  <option value={10}>10 / {t('common.page')}</option>
                  <option value={25}>25 / {t('common.page')}</option>
                  <option value={50}>50 / {t('common.page')}</option>
                </select>
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  <ChevronLeft size={14} />
                </Button>
                <span style={{ fontSize: 12, padding: '0 6px' }}>
                  {t('common.page')} {pagination.page} {t('common.of')} {pagination.total_pages}
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

      {createOpen && (
        <CreateProductionDialog
          products={products}
          costTypes={costTypes}
          t={t}
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => {
            setCreateOpen(false)
            toast(t('production.createdToast'))
            router.push(`/production/${id}`)
          }}
          onError={(msg) => toast(msg)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Create draft dialog
// ---------------------------------------------------------------------------

interface InputDraft {
  key: string
  product_id: string
  quantity: string
}

interface CostLineDraft {
  key: string
  cost_type_id: string
  amount: string
  paid_in_cash: boolean
  description: string
}

function emptyInput(): InputDraft {
  return { key: crypto.randomUUID(), product_id: '', quantity: '1' }
}

function emptyCostLine(): CostLineDraft {
  return { key: crypto.randomUUID(), cost_type_id: '', amount: '', paid_in_cash: true, description: '' }
}

function CreateProductionDialog({
  products,
  costTypes,
  onClose,
  onCreated,
  onError,
  t,
}: {
  products: Product[]
  costTypes: CostType[]
  onClose: () => void
  onCreated: (id: number) => void
  onError: (msg: string) => void
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const [runDate, setRunDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [outputProductId, setOutputProductId] = useState('')
  const [outputQuantity, setOutputQuantity] = useState('1')
  const [notes, setNotes] = useState('')
  const [inputs, setInputs] = useState<InputDraft[]>([emptyInput()])
  const [costLines, setCostLines] = useState<CostLineDraft[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const updateInput = (key: string, patch: Partial<InputDraft>) =>
    setInputs((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const addInput = () => setInputs((rows) => [...rows, emptyInput()])
  const removeInput = (key: string) =>
    setInputs((rows) => (rows.length > 1 ? rows.filter((r) => r.key !== key) : rows))

  const updateCostLine = (key: string, patch: Partial<CostLineDraft>) =>
    setCostLines((rows) => rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const addCostLine = () => setCostLines((rows) => [...rows, emptyCostLine()])
  const removeCostLine = (key: string) => setCostLines((rows) => rows.filter((r) => r.key !== key))

  const submit = async () => {
    setError(null)
    if (!outputProductId) {
      setError(t('production.outputProductRequired'))
      return
    }
    const outQty = Number(outputQuantity)
    if (!Number.isFinite(outQty) || outQty <= 0) {
      setError(t('production.outputQuantityPositive'))
      return
    }
    if (inputs.length === 0) {
      setError(t('production.inputRequired'))
      return
    }
    const payloadInputs = []
    for (const l of inputs) {
      if (!l.product_id) {
        setError(t('production.inputProductRequired'))
        return
      }
      const q = Number(l.quantity)
      if (!Number.isFinite(q) || q <= 0) {
        setError(t('production.inputQuantityPositive'))
        return
      }
      payloadInputs.push({ product_id: Number(l.product_id), quantity: q })
    }

    const payloadCostLines = []
    for (const l of costLines) {
      if (!l.cost_type_id) {
        setError(t('production.costTypeRequired'))
        return
      }
      const a = Number(l.amount)
      if (!Number.isFinite(a) || a <= 0) {
        setError(t('production.costAmountPositive'))
        return
      }
      payloadCostLines.push({
        cost_type_id: Number(l.cost_type_id),
        amount: a,
        paid_in_cash: l.paid_in_cash,
        description: l.description.trim() || null,
      })
    }

    const body: Record<string, unknown> = {
      output_product_id: Number(outputProductId),
      output_quantity: outQty,
      inputs: payloadInputs,
    }
    if (runDate) body.run_date = new Date(`${runDate}T00:00:00`).toISOString()
    if (notes.trim()) body.notes = notes.trim()
    if (payloadCostLines.length > 0) body.cost_lines = payloadCostLines

    setBusy(true)
    try {
      const res = await api.headers.post<ProductionRun>('/production-runs', body, {
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      const id = Number(res.data?.id)
      if (!Number.isFinite(id)) throw new Error(t('production.serverNoId'))
      onCreated(id)
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      const msg = err instanceof Error ? err.message : t('production.createFailed')
      if (code === 'cost_type_deactivated') {
        setError(t('production.costTypeDeactivated'))
      } else {
        setError(msg)
      }
      onError(msg)
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
          maxWidth: 900,
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
            <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>{t('production.newRunDialog')}</h2>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: '#718198' }}>{t('production.creatingDraftHelp')}</p>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ border: 0, background: 'none', color: '#8a98ab' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('production.runDate')}</label>
              <input type="date" value={runDate} onChange={(e) => setRunDate(e.target.value)} style={fieldStyle} />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('production.outputProduct')} *</label>
              <select value={outputProductId} onChange={(e) => setOutputProductId(e.target.value)} style={fieldStyle}>
                <option value="">{t('production.selectOutputProduct')}</option>
                {products.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('production.outputQuantity')} *</label>
              <input
                type="number"
                min="0"
                step="0.0001"
                value={outputQuantity}
                onChange={(e) => setOutputQuantity(e.target.value)}
                style={fieldStyle}
              />
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={labelStyle}>{t('common.notes')}</label>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} style={fieldStyle} />
            </div>
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <h3 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>{t('production.rawInputs')}</h3>
              <Button variant="outline" size="sm" onClick={addInput}>
                <Plus size={13} className="mr-1" /> {t('production.addInput')}
              </Button>
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>{t('production.product')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>{t('production.quantity')}</th>
                    <th style={{ padding: '9px 12px', width: 40 }} />
                  </tr>
                </thead>
                <tbody>
                  {inputs.map((l) => (
                    <tr key={l.key} style={{ borderTop: '1px solid #f0f4f9' }}>
                      <td style={{ padding: '8px 12px', minWidth: 200 }}>
                        <select
                          value={l.product_id}
                          onChange={(e) => updateInput(l.key, { product_id: e.target.value })}
                          style={{ ...fieldStyle, height: 32 }}
                        >
                          <option value="">{t('production.selectProduct')}</option>
                          {products.map((p) => (
                            <option key={p.id} value={String(p.id)}>
                              {p.name}
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
                          onChange={(e) => updateInput(l.key, { quantity: e.target.value })}
                          style={{ ...fieldStyle, height: 32, textAlign: 'right' }}
                        />
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                        <button
                          onClick={() => removeInput(l.key)}
                          disabled={inputs.length <= 1}
                          aria-label={t('production.removeInput')}
                          style={{ border: 0, background: 'none', color: inputs.length <= 1 ? '#cbd5e1' : '#dc2626' }}
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <h3 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>{t('production.costLines')}</h3>
              <Button variant="outline" size="sm" onClick={addCostLine}>
                <Plus size={13} className="mr-1" /> {t('production.addCostLine')}
              </Button>
            </div>
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>{t('production.costType')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 700 }}>{t('production.amount')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>{t('production.paidInCash')}</th>
                    <th style={{ padding: '9px 12px', textAlign: 'left', fontWeight: 700 }}>{t('production.description')}</th>
                    <th style={{ padding: '9px 12px', width: 40 }} />
                  </tr>
                </thead>
                <tbody>
                  {costLines.map((l) => (
                    <tr key={l.key} style={{ borderTop: '1px solid #f0f4f9' }}>
                      <td style={{ padding: '8px 12px', minWidth: 160 }}>
                        <select
                          value={l.cost_type_id}
                          onChange={(e) => updateCostLine(l.key, { cost_type_id: e.target.value })}
                          style={{ ...fieldStyle, height: 32 }}
                        >
                          <option value="">{t('production.selectCostType')}</option>
                          {costTypes.map((ct) => (
                            <option key={ct.id} value={String(ct.id)}>
                              {ct.name}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          value={l.amount}
                          onChange={(e) => updateCostLine(l.key, { amount: e.target.value })}
                          style={{ ...fieldStyle, height: 32, textAlign: 'right' }}
                        />
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <input
                          type="checkbox"
                          checked={l.paid_in_cash}
                          onChange={(e) => updateCostLine(l.key, { paid_in_cash: e.target.checked })}
                        />
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <input
                          value={l.description}
                          onChange={(e) => updateCostLine(l.key, { description: e.target.value })}
                          maxLength={500}
                          style={{ ...fieldStyle, height: 32 }}
                        />
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                        <button
                          onClick={() => removeCostLine(l.key)}
                          aria-label={t('production.removeCostLine')}
                          style={{ border: 0, background: 'none', color: '#dc2626' }}
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {costLines.length === 0 && (
                <div style={{ padding: '12px', fontSize: 12, color: '#94a3b8', textAlign: 'center' }}>
                  {t('production.noCostLinesYet')}
                </div>
              )}
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
            {busy ? t('production.creating') : t('production.createDraft')}
          </Button>
        </div>
      </div>
    </div>
  )
}
