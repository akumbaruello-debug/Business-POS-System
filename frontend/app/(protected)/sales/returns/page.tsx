'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  ClipboardList,
  RotateCcw,
  Search,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Pagination, Refund, SalesReturn, SaleListResponse } from '@/lib/sale-types'
import type { Contact, ContactListResponse } from '@/lib/contact-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'

// -----------------------------------------------------------------------------
// Returns / Refunds workspace (V1)
//
// Backend contract:
//   GET /sales-returns?sort=&from=&to=&filter[sale_id]=&filter[customer_id]=&filter[lifecycle_status]=
//     → { data: SalesReturn[], pagination }
//   GET /refunds?sort=&from=&to=&filter[sale_id]=&filter[payment_method_id]=
//     → { data: Refund[], pagination }
//   GET /sales?customer_id= → { data: Sale[], pagination }
//   GET /contacts?filter[type]=customer → { data: Contact[], pagination }
//
// We render both returns and refunds on one workspace so the user can follow
// the sale → return → refund chain without guessing which route to open.
// -----------------------------------------------------------------------------

interface ReturnsListResponse {
  data: SalesReturn[]
  pagination: Pagination
}

interface RefundsListResponse {
  data: Refund[]
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

function fmtDate(iso: string | null | undefined): string {
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
  if (s === 'partially_returned') return 'Partially returned'
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function LifecycleBadge({ status }: { status: string }) {
  const tone: Record<string, string> = {
    posted: 'background:#eff6ff;color:#2563eb;border:1px solid #dbeafe',
    cancelled: 'background:#fef2f2;color:#dc2626;border:1px solid #fecaca',
  }
  const style = tone[status] ?? tone.posted
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
      {lifecycleLabel(status)}
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

export default function ReturnsPage() {
  const user = useSession()
  const canView = user.capabilities.includes('sale.view')

  // Tabs: returns or refunds
  const [tab, setTab] = useState<'returns' | 'refunds'>('returns')

  // Shared filter state
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [sortKey, setSortKey] = useState('id')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const [query, setQuery] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  // Returns-only filters
  const [lifecycleFilter, setLifecycleFilter] = useState<string>('all')
  const [customerFilter, setCustomerFilter] = useState<string>('all')
  const [saleFilter, setSaleFilter] = useState<string>('')

  // Refunds-only filters
  const [paymentMethodFilter, setPaymentMethodFilter] = useState<string>('all')

  // Data
  const [returns, setReturns] = useState<SalesReturn[]>([])
  const [refunds, setRefunds] = useState<Refund[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)
  const [customers, setCustomers] = useState<Contact[]>([])
  const [sales, setSales] = useState<SaleListResponse['data']>([])
  const [paymentMethods, setPaymentMethods] = useState<{ id: number; name: string }[]>([])

  const [notice, setNotice] = useState('')
  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const customerName = useMemo(() => {
    const m = new Map<number, string>()
    customers.forEach((c) => m.set(c.id, c.name ?? `Customer #${c.id}`))
    return m
  }, [customers])

  const saleReference = useMemo(() => {
    const m = new Map<number, string>()
    sales.forEach((s) => m.set(s.id, s.reference_no ?? `Sale #${s.id}`))
    return m
  }, [sales])

  const fetchCustomers = useCallback(async () => {
    try {
      const res = await api.get<ContactListResponse>('/contacts', {
        params: { 'filter[type]': 'customer', per_page: '500' },
      })
      setCustomers(res.data)
    } catch {
      // enrichment only
    }
  }, [])

  const fetchSales = useCallback(async () => {
    try {
      const res = await api.get<SaleListResponse>('/sales', {
        params: { per_page: '500', sort: 'id' },
      })
      setSales(res.data)
    } catch {
      // enrichment only
    }
  }, [])

  const fetchPaymentMethods = useCallback(async () => {
    try {
      const res = await api.get<{ data: { id: number; name: string }[] }>('/payment-methods', {
        params: { per_page: '500' },
      })
      setPaymentMethods(res.data ?? [])
    } catch {
      // enrichment only
    }
  }, [])

  useEffect(() => {
    fetchCustomers()
    fetchSales()
    fetchPaymentMethods()
  }, [fetchCustomers, fetchSales, fetchPaymentMethods])

  const buildParams = useCallback(
    (base: Record<string, string>) => {
      const params: Record<string, string> = { ...base }
      if (query.trim()) params.q = query.trim()
      if (fromDate) params.from = new Date(`${fromDate}T00:00:00`).toISOString()
      if (toDate) params.to = new Date(`${toDate}T23:59:59`).toISOString()
      if (saleFilter.trim()) params['filter[sale_id]'] = saleFilter.trim()
      return params
    },
    [query, fromDate, toDate, saleFilter]
  )

  const fetchReturns = useCallback(async () => {
    if (!canView) {
      setLoading(false)
      setError('Missing capability: sale.view')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const params = buildParams({
        page: String(page),
        per_page: String(perPage),
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
      })
      if (lifecycleFilter !== 'all') params['filter[lifecycle_status]'] = lifecycleFilter
      if (customerFilter !== 'all') params['filter[customer_id]'] = customerFilter

      const res = await api.get<ReturnsListResponse>('/sales-returns', { params })
      setReturns(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load returns')
    } finally {
      setLoading(false)
    }
  }, [canView, page, perPage, sortKey, sortDir, buildParams, lifecycleFilter, customerFilter])

  const fetchRefunds = useCallback(async () => {
    if (!canView) {
      setLoading(false)
      setError('Missing capability: sale.view')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const params = buildParams({
        page: String(page),
        per_page: String(perPage),
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
      })
      if (paymentMethodFilter !== 'all') params['filter[payment_method_id]'] = paymentMethodFilter

      const res = await api.get<RefundsListResponse>('/refunds', { params })
      setRefunds(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load refunds')
    } finally {
      setLoading(false)
    }
  }, [canView, page, perPage, sortKey, sortDir, buildParams, paymentMethodFilter])

  useEffect(() => {
    setPage(1)
  }, [tab, query, fromDate, toDate, lifecycleFilter, customerFilter, paymentMethodFilter, saleFilter, sortKey, sortDir, perPage])

  useEffect(() => {
    if (tab === 'returns') fetchReturns()
    else fetchRefunds()
  }, [tab, fetchReturns, fetchRefunds])

  const handleSort = (key: string) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir('desc')
    }
    setPage(1)
  }

  const clearFilters = () => {
    setQuery('')
    setFromDate('')
    setToDate('')
    setLifecycleFilter('all')
    setCustomerFilter('all')
    setPaymentMethodFilter('all')
    setSaleFilter('')
    setSortKey('id')
    setSortDir('desc')
    setPage(1)
  }

  const activeFiltersCount = [
    query.trim(),
    fromDate,
    toDate,
    saleFilter.trim(),
    tab === 'returns' && lifecycleFilter !== 'all' ? lifecycleFilter : '',
    tab === 'returns' && customerFilter !== 'all' ? customerFilter : '',
    tab === 'refunds' && paymentMethodFilter !== 'all' ? paymentMethodFilter : '',
  ].filter(Boolean).length

  if (!canView && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Sales / Returns</div>
            <h1>Returns &amp; refunds</h1>
            <p>View sales returns and customer refunds.</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>Forbidden</strong>
          <p>Your account lacks the sale.view capability.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">Sales / Returns</div>
          <h1>Returns &amp; refunds</h1>
          <p>Track returned goods and customer refund disbursements.</p>
        </div>
        <div className="heading-actions">
          <Button variant="outline" onClick={() => (tab === 'returns' ? fetchReturns() : fetchRefunds())} disabled={loading}>
            <RotateCcw size={14} className="mr-1" /> Refresh
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <div className="sales-tabs" role="tablist" aria-label="Returns and refunds">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'returns'}
          className="sales-tab"
          onClick={() => setTab('returns')}
        >
          Sales returns
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'refunds'}
          className="sales-tab"
          onClick={() => setTab('refunds')}
        >
          Refunds
        </button>
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
              onChange={(e) => setQuery(e.target.value)}
              placeholder={tab === 'returns' ? 'Search return reason…' : 'Search refund reason…'}
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
            Sale
          </label>
          <select
            value={saleFilter}
            onChange={(e) => setSaleFilter(e.target.value)}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13, minWidth: 160 }}
          >
            <option value="">All sales</option>
            {sales.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.reference_no ?? `Sale #${s.id}`}
              </option>
            ))}
          </select>
        </div>

        {tab === 'returns' && (
          <>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
                Status
              </label>
              <select
                value={lifecycleFilter}
                onChange={(e) => setLifecycleFilter(e.target.value)}
                style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
              >
                <option value="all">All statuses</option>
                <option value="posted">Posted</option>
                <option value="cancelled">Cancelled</option>
              </select>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
                Customer
              </label>
              <select
                value={customerFilter}
                onChange={(e) => setCustomerFilter(e.target.value)}
                style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13, minWidth: 150 }}
              >
                <option value="all">All customers</option>
                {customers.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.name ?? `Customer #${c.id}`}
                  </option>
                ))}
              </select>
            </div>
          </>
        )}

        {tab === 'refunds' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
              Payment method
            </label>
            <select
              value={paymentMethodFilter}
              onChange={(e) => setPaymentMethodFilter(e.target.value)}
              style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13, minWidth: 150 }}
            >
              <option value="all">All methods</option>
              {paymentMethods.map((m) => (
                <option key={m.id} value={String(m.id)}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase', color: '#8a98ab' }}>
            From
          </label>
          <input
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
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
            onChange={(e) => setToDate(e.target.value)}
            style={{ height: 34, borderRadius: 8, border: '1px solid var(--border)', background: 'white', padding: '0 10px', fontSize: 13 }}
          />
        </div>

        {activeFiltersCount > 0 && (
          <Button variant="outline" size="sm" onClick={clearFilters} style={{ height: 34 }}>
            <X size={13} className="mr-1" /> Clear
          </Button>
        )}
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
        ) : error ? (
          <div className="error-state" style={{ padding: 48 }}>
            <div className="error-icon">
              <AlertTriangle size={32} />
            </div>
            <strong>Failed to load {tab}</strong>
            <p style={{ maxWidth: 420, color: '#718198', fontSize: 13, textAlign: 'center' }}>{error}</p>
            <Button variant="outline" onClick={() => (tab === 'returns' ? fetchReturns() : fetchRefunds())}>
              Retry
            </Button>
          </div>
        ) : tab === 'returns' ? (
          returns.length === 0 ? (
            <EmptyState
              icon={<ClipboardList size={28} />}
              title="No returns found"
              message={
                activeFiltersCount > 0
                  ? 'No returns match the current filters.'
                  : 'No sales returns have been recorded yet.'
              }
              onClear={activeFiltersCount > 0 ? clearFilters : undefined}
            />
          ) : (
            <>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: '#f8fafc', textAlign: 'left', fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', color: '#64748b' }}>
                      <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                        <SortButton label="Return" active={sortKey === 'id'} onClick={() => handleSort('id')} sortDir={sortDir} />
                      </th>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Sale</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Customer</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                        <SortButton label="Date" active={sortKey === 'return_date'} onClick={() => handleSort('return_date')} sortDir={sortDir} />
                      </th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>
                        <SortButton label="Value" active={sortKey === 'total_selling_price_returned'} onClick={() => handleSort('total_selling_price_returned')} sortDir={sortDir} align="right" />
                      </th>
                      <th style={{ padding: '10px 14px', fontWeight: 700 }}>Status</th>
                      <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {returns.map((r) => (
                      <tr key={r.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                        <td style={{ padding: '12px 14px' }}>
                          <span style={{ fontWeight: 600 }}>Return #{r.id}</span>
                          {r.reason && <div style={{ fontSize: 11, color: '#8a98ab', marginTop: 2 }}>{r.reason}</div>}
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <Link href={`/sales/${r.sale_id}`} style={{ color: 'var(--primary)', fontWeight: 600, textDecoration: 'none' }}>
                            {saleReference.get(r.sale_id) ?? `Sale #${r.sale_id}`}
                          </Link>
                        </td>
                        <td style={{ padding: '12px 14px', color: '#334155' }}>
                          {customerName.get(r.sale_id) ?? '—'}
                        </td>
                        <td style={{ padding: '12px 14px', color: '#475569', whiteSpace: 'nowrap' }}>{fmtDate(r.return_date)}</td>
                        <td style={{ padding: '12px 14px', textAlign: 'right', fontWeight: 700, whiteSpace: 'nowrap' }}>
                          {formatIDR(r.total_selling_price_returned)}
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <LifecycleBadge status={r.lifecycle_status} />
                        </td>
                        <td style={{ padding: '12px 14px', textAlign: 'right' }}>
                          <Link
                            href={`/sales/${r.sale_id}`}
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
                            View sale
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <PaginationFooter
                pagination={pagination}
                page={page}
                perPage={perPage}
                onPageChange={setPage}
                onPerPageChange={setPerPage}
              />
            </>
          )
        ) : refunds.length === 0 ? (
          <EmptyState
            icon={<ClipboardList size={28} />}
            title="No refunds found"
            message={
              activeFiltersCount > 0
                ? 'No refunds match the current filters.'
                : 'No customer refunds have been recorded yet.'
            }
            onClear={activeFiltersCount > 0 ? clearFilters : undefined}
          />
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', textAlign: 'left', fontSize: 11, letterSpacing: 0.6, textTransform: 'uppercase', color: '#64748b' }}>
                    <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      <SortButton label="Refund" active={sortKey === 'id'} onClick={() => handleSort('id')} sortDir={sortDir} />
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>Sale</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>Customer</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      <SortButton label="Date" active={sortKey === 'refund_date'} onClick={() => handleSort('refund_date')} sortDir={sortDir} />
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700 }}>Method</th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>
                      <SortButton label="Amount" active={sortKey === 'amount'} onClick={() => handleSort('amount')} sortDir={sortDir} align="right" />
                    </th>
                    <th style={{ padding: '10px 14px', fontWeight: 700, textAlign: 'right' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {refunds.map((r) => (
                    <tr key={r.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                      <td style={{ padding: '12px 14px' }}>
                        <span style={{ fontWeight: 600 }}>Refund #{r.id}</span>
                        {r.reason && <div style={{ fontSize: 11, color: '#8a98ab', marginTop: 2 }}>{r.reason}</div>}
                      </td>
                      <td style={{ padding: '12px 14px' }}>
                        <Link href={`/sales/${r.sale_id}`} style={{ color: 'var(--primary)', fontWeight: 600, textDecoration: 'none' }}>
                          {saleReference.get(r.sale_id) ?? `Sale #${r.sale_id}`}
                        </Link>
                      </td>
                      <td style={{ padding: '12px 14px', color: '#334155' }}>
                        {customerName.get(r.sale_id) ?? '—'}
                      </td>
                      <td style={{ padding: '12px 14px', color: '#475569', whiteSpace: 'nowrap' }}>{fmtDate(r.refund_date)}</td>
                      <td style={{ padding: '12px 14px', color: '#334155' }}>
                        {paymentMethods.find((m) => m.id === r.payment_method_id)?.name ?? `Method #${r.payment_method_id}`}
                      </td>
                      <td style={{ padding: '12px 14px', textAlign: 'right', fontWeight: 700, whiteSpace: 'nowrap' }}>
                        {formatIDR(r.amount)}
                      </td>
                      <td style={{ padding: '12px 14px', textAlign: 'right' }}>
                        <Link
                          href={`/sales/${r.sale_id}`}
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
                          View sale
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <PaginationFooter
              pagination={pagination}
              page={page}
              perPage={perPage}
              onPageChange={setPage}
              onPerPageChange={setPerPage}
            />
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
    </div>
  )
}

function SortButton({
  label,
  active,
  onClick,
  sortDir,
  align,
}: {
  label: string
  active: boolean
  onClick: () => void
  sortDir: 'asc' | 'desc'
  align?: 'right'
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        background: 'none',
        border: 0,
        color: 'inherit',
        font: 'inherit',
        cursor: 'pointer',
        flexDirection: align === 'right' ? 'row-reverse' : 'row',
      }}
    >
      {label}
      <ChevronsUpDown
        size={12}
        style={{
          opacity: active ? 1 : 0.35,
          transform: active && sortDir === 'asc' ? 'rotate(180deg)' : undefined,
        }}
      />
    </button>
  )
}

function PaginationFooter({
  pagination,
  page,
  perPage,
  onPageChange,
  onPerPageChange,
}: {
  pagination: Pagination
  page: number
  perPage: number
  onPageChange: (p: number) => void
  onPerPageChange: (n: number) => void
}) {
  return (
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
        Showing {pagination.total > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0}–
        {Math.min(pagination.page * pagination.per_page, pagination.total)} of {pagination.total}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <select
          value={perPage}
          onChange={(e) => {
            onPerPageChange(Number(e.target.value))
            onPageChange(1)
          }}
          style={{ height: 32, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 8px', fontSize: 12 }}
          aria-label="Rows per page"
        >
          <option value={10}>10 / page</option>
          <option value={25}>25 / page</option>
          <option value={50}>50 / page</option>
        </select>
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          <ChevronLeft size={14} />
        </Button>
        <span style={{ fontSize: 12, padding: '0 6px' }}>
          Page {pagination.page} of {pagination.total_pages}
        </span>
        <Button variant="outline" size="sm" disabled={page >= pagination.total_pages} onClick={() => onPageChange(page + 1)}>
          <ChevronRight size={14} />
        </Button>
      </div>
    </div>
  )
}

function EmptyState({
  icon,
  title,
  message,
  onClear,
}: {
  icon: React.ReactNode
  title: string
  message: string
  onClear?: () => void
}) {
  return (
    <div className="error-state" style={{ padding: 48 }}>
      <div className="error-icon" style={{ background: '#eff6ff', color: 'var(--primary)' }}>
        {icon}
      </div>
      <strong>{title}</strong>
      <p style={{ maxWidth: 420, color: '#718198', fontSize: 13, textAlign: 'center' }}>{message}</p>
      {onClear && (
        <Button variant="outline" onClick={onClear}>
          Clear filters
        </Button>
      )}
    </div>
  )
}
