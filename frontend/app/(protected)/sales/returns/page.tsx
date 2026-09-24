'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  RotateCcw,
  Search,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Pagination, Refund, SalesReturn, SaleListResponse } from '@/lib/sale-types'
import type { PaymentMethod } from '@/lib/sale-types'
import type { Contact, ContactListResponse } from '@/lib/contact-types'
import { fmtDate } from '@/lib/sale-ui'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { LifecycleBadge } from '@/components/sales/sale-badges'
import { SortButton } from '@/components/sales/sort-button'
import { useLanguage } from '@/lib/i18n'

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

export default function ReturnsPage() {
  const user = useSession()
  const { t } = useLanguage()
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
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])

  const [notice, setNotice] = useState('')
  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const customerName = useMemo(() => {
    const m = new Map<number, string>()
    customers.forEach((c) => m.set(c.id, c.name ?? `${t('sales.customer')} #${c.id}`))
    return m
  }, [customers, t])

  // Task 1 fix: sale_id → customer_id mapping for correct name lookup
  const saleCustomerMap = useMemo(() => {
    const m = new Map<number, number | null>()
    sales.forEach((s) => m.set(s.id, s.customer_id ?? null))
    return m
  }, [sales])

  const saleReference = useMemo(() => {
    const m = new Map<number, string>()
    sales.forEach((s) => m.set(s.id, s.reference_no ?? `${t('returns.sale')} #${s.id}`))
    return m
  }, [sales, t])

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
      const res = await api.get<{ data: PaymentMethod[] }>('/payment-methods', {
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
    [query, fromDate, toDate, saleFilter],
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
      setError(err instanceof Error ? err.message : t('returns.failedToLoad', { tab: 'returns' }))
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
      setError(err instanceof Error ? err.message : t('returns.failedToLoad', { tab: 'refunds' }))
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
            <div className="eyebrow">{t('returns.eyebrow')}</div>
            <h1>{t('returns.title')}</h1>
            <p>{t('returns.subtitle')}</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>{t('errors.forbidden')}</strong>
          <p>{t('errors.lacksCapability', { capability: 'sale.view' })}</p>
        </div>
      </div>
    )
  }

  // Helper: resolve customer name from a sale_id
  const customerForSale = (saleId: number): string => {
    const customerId = saleCustomerMap.get(saleId)
    if (customerId == null) return t('sales.walkInCustomer')
    return customerName.get(customerId) ?? `${t('sales.customer')} #${customerId}`
  }

  return (
    <div className="content">
      {/* Heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('returns.eyebrow')}</div>
          <h1>{t('returns.title')}</h1>
          <p>{t('returns.subtitle')}</p>
        </div>
        <div className="heading-actions">
          <Button variant="outline" onClick={() => (tab === 'returns' ? fetchReturns() : fetchRefunds())} disabled={loading}>
            <RotateCcw size={14} className="mr-1" /> {t('common.refresh')}
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
          {t('returns.salesReturns')}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'refunds'}
          className="sales-tab"
          onClick={() => setTab('refunds')}
        >
          {t('returns.refunds')}
        </button>
      </div>

      {/* Filters — using shared CSS classes from the list page */}
      <div className="sales-toolbar">
        <div className="sales-toolbar-row">
          <div className="sales-toolbar-fields">
            <div className="sales-field sales-field-grow">
              <span className="sales-field-label">{t('common.search')}</span>
              <div className="sales-search">
                <Search size={15} />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={tab === 'returns' ? t('returns.searchReturnsPlaceholder') : t('returns.searchRefundsPlaceholder')}
                />
              </div>
            </div>

            <div className="sales-field">
              <span className="sales-field-label">{t('returns.sale')}</span>
              <div className="sales-select">
                <select value={saleFilter} onChange={(e) => setSaleFilter(e.target.value)}>
                  <option value="">{t('returns.allSales')}</option>
                  {sales.map((s) => (
                    <option key={s.id} value={String(s.id)}>
                      {s.reference_no ?? `Sale #${s.id}`}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {tab === 'returns' && (
              <>
                <div className="sales-field">
                  <span className="sales-field-label">{t('common.status')}</span>
                  <div className="sales-select">
                    <select value={lifecycleFilter} onChange={(e) => setLifecycleFilter(e.target.value)}>
                      <option value="all">{t('returns.allStatuses')}</option>
                      <option value="posted">{t('status.posted')}</option>
                      <option value="cancelled">{t('status.cancelled')}</option>
                    </select>
                  </div>
                </div>

                <div className="sales-field">
                  <span className="sales-field-label">{t('sales.customer')}</span>
                  <div className="sales-select">
                    <select value={customerFilter} onChange={(e) => setCustomerFilter(e.target.value)}>
                      <option value="all">{t('sales.allCustomers')}</option>
                      {customers.map((c) => (
                        <option key={c.id} value={String(c.id)}>
                          {c.name ?? `${t('sales.customer')} #${c.id}`}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </>
            )}

            {tab === 'refunds' && (
              <div className="sales-field">
                <span className="sales-field-label">{t('returns.paymentMethod')}</span>
                <div className="sales-select">
                  <select value={paymentMethodFilter} onChange={(e) => setPaymentMethodFilter(e.target.value)}>
                    <option value="all">{t('returns.allMethods')}</option>
                    {paymentMethods.map((m) => (
                      <option key={m.id} value={String(m.id)}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}

            <div className="sales-field">
              <span className="sales-field-label">{t('returns.dateRange')}</span>
              <div className="sales-date-range">
                <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} aria-label="From date" />
                <span className="sales-date-dash">–</span>
                <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} aria-label="To date" />
              </div>
            </div>
          </div>

          <div className="sales-toolbar-actions">
            {activeFiltersCount > 0 && (
              <Button variant="outline" onClick={clearFilters}>
                <X size={14} />
                {t('common.clearFilters')}
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Table */}
      <section className="sales-card">
        {loading ? (
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="skeleton" style={{ height: 46 }} />
            ))}
          </div>
        ) : error ? (
          <div className="error-state">
            <div className="error-icon">
              <AlertTriangle size={28} />
            </div>
            <strong>{t('returns.failedToLoad', { tab })}</strong>
            <p style={{ maxWidth: 440, fontSize: 13 }}>{error}</p>
            <Button variant="outline" onClick={() => (tab === 'returns' ? fetchReturns() : fetchRefunds())}>
              <RotateCcw size={14} />
              {t('common.tryAgain')}
            </Button>
          </div>
        ) : tab === 'returns' ? (
          returns.length === 0 ? (
            <EmptyState
              icon={<ClipboardList size={28} />}
              title={t('returns.noReturnsFound')}
              message={
                activeFiltersCount > 0
                  ? t('returns.noReturnsMatchFilters')
                  : t('returns.noReturnsRecorded')
              }
              onClear={activeFiltersCount > 0 ? clearFilters : undefined}
              t={t}
            />
          ) : (
            <>
              <div className="sales-table-wrap">
                <table className="sales-table">
                  <thead>
                    <tr>
                      <th>
                        <SortButton label={t('returns.return')} active={sortKey === 'id'} onClick={() => handleSort('id')} sortDir={sortDir} />
                      </th>
                      <th>{t('returns.sale')}</th>
                      <th>{t('sales.customer')}</th>
                      <th>
                        <SortButton label={t('common.date')} active={sortKey === 'return_date'} onClick={() => handleSort('return_date')} sortDir={sortDir} />
                      </th>
                      <th className="sales-th-right">
                        <SortButton label={t('returns.value')} active={sortKey === 'total_selling_price_returned'} onClick={() => handleSort('total_selling_price_returned')} sortDir={sortDir} align="right" />
                      </th>
                      <th>{t('common.status')}</th>
                      <th className="sales-th-actions">
                        <span className="sr-only">{t('common.actions')}</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {returns.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <span style={{ fontWeight: 600 }}>{t('returns.return')} #{r.id}</span>
                          {r.reason && <div className="sales-muted" style={{ fontSize: 11, marginTop: 2 }}>{r.reason}</div>}
                        </td>
                        <td>
                          <Link href={`/sales/${r.sale_id}`} className="sales-ref">
                            {saleReference.get(r.sale_id) ?? `${t('returns.sale')} #${r.sale_id}`}
                          </Link>
                        </td>
                        <td className="sales-muted">{customerForSale(r.sale_id)}</td>
                        <td className="sales-muted" style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.return_date)}</td>
                        <td className="sales-td-right sales-num">{formatIDR(r.total_selling_price_returned)}</td>
                        <td><LifecycleBadge status={r.lifecycle_status} /></td>
                        <td className="sales-td-actions">
                          <Link href={`/sales/${r.sale_id}`} className="sales-ref" style={{ fontSize: 12 }}>
                            {t('returns.viewSale')}
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
                t={t}
              />
            </>
          )
        ) : refunds.length === 0 ? (
          <EmptyState
            icon={<ClipboardList size={28} />}
            title={t('returns.noRefundsFound')}
            message={
              activeFiltersCount > 0
                ? t('returns.noRefundsMatchFilters')
                : t('returns.noRefundsRecorded')
            }
            onClear={activeFiltersCount > 0 ? clearFilters : undefined}
            t={t}
          />
        ) : (
          <>
            <div className="sales-table-wrap">
              <table className="sales-table">
                <thead>
                  <tr>
                    <th>
                      <SortButton label={t('returns.refund')} active={sortKey === 'id'} onClick={() => handleSort('id')} sortDir={sortDir} />
                    </th>
                    <th>{t('returns.sale')}</th>
                    <th>{t('sales.customer')}</th>
                    <th>
                      <SortButton label={t('common.date')} active={sortKey === 'refund_date'} onClick={() => handleSort('refund_date')} sortDir={sortDir} />
                    </th>
                    <th>{t('returns.method')}</th>
                    <th className="sales-th-right">
                      <SortButton label={t('sales.amount')} active={sortKey === 'amount'} onClick={() => handleSort('amount')} sortDir={sortDir} align="right" />
                    </th>
                    <th className="sales-th-actions">
                      <span className="sr-only">{t('common.actions')}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {refunds.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <span style={{ fontWeight: 600 }}>{t('returns.refund')} #{r.id}</span>
                        {r.reason && <div className="sales-muted" style={{ fontSize: 11, marginTop: 2 }}>{r.reason}</div>}
                      </td>
                      <td>
                        <Link href={`/sales/${r.sale_id}`} className="sales-ref">
                          {saleReference.get(r.sale_id) ?? `${t('returns.sale')} #${r.sale_id}`}
                        </Link>
                      </td>
                      <td className="sales-muted">{customerForSale(r.sale_id)}</td>
                      <td className="sales-muted" style={{ whiteSpace: 'nowrap' }}>{fmtDate(r.refund_date)}</td>
                      <td className="sales-muted">
                        {paymentMethods.find((m) => m.id === r.payment_method_id)?.name ?? `${t('returns.method')} #${r.payment_method_id}`}
                      </td>
                      <td className="sales-td-right sales-num">{formatIDR(r.amount)}</td>
                      <td className="sales-td-actions">
                        <Link href={`/sales/${r.sale_id}`} className="sales-ref" style={{ fontSize: 12 }}>
                          {t('returns.viewSale')}
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
              t={t}
            />
          </>
        )}
      </section>

      {notice && (
        <div className="sales-toast" role="status">
          {notice}
        </div>
      )}
    </div>
  )
}

function PaginationFooter({
  pagination,
  page,
  perPage,
  onPageChange,
  onPerPageChange,
  t,
}: {
  pagination: Pagination
  page: number
  perPage: number
  onPageChange: (p: number) => void
  onPerPageChange: (n: number) => void
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const rangeStart = pagination.total === 0 ? 0 : (pagination.page - 1) * pagination.per_page + 1
  const rangeEnd = Math.min(pagination.page * pagination.per_page, pagination.total)

  return (
    <div className="sales-pagination">
      <span>
        {t('common.showing')} {rangeStart}–{rangeEnd} {t('common.of')} {pagination.total}
      </span>
      <div className="sales-pagination-nav">
        <label className="sales-field" style={{ gap: 0 }}>
          <span className="sr-only">{t('common.rowsPerPage')}</span>
          <select
            className="sales-perpage"
            aria-label="Rows per page"
            value={String(perPage)}
            onChange={(e) => {
              onPerPageChange(Number(e.target.value))
              onPageChange(1)
            }}
          >
            <option value={10}>10 / {t('common.page')}</option>
            <option value={25}>25 / {t('common.page')}</option>
            <option value={50}>50 / {t('common.page')}</option>
          </select>
        </label>
        <button
          type="button"
          className="sales-pager"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span style={{ fontWeight: 600, color: '#475569', fontSize: 13 }}>
          {pagination.page} / {pagination.total_pages}
        </span>
        <button
          type="button"
          className="sales-pager"
          aria-label="Next page"
          disabled={page >= pagination.total_pages}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  )
}

function EmptyState({
  icon,
  title,
  message,
  onClear,
  t,
}: {
  icon: React.ReactNode
  title: string
  message: string
  onClear?: () => void
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  return (
    <div className="empty-workspace">
      <div className="empty-icon">{icon}</div>
      <strong>{title}</strong>
      <p>{message}</p>
      {onClear && (
        <Button variant="outline" onClick={onClear}>
          {t('common.clearFilters')}
        </Button>
      )}
    </div>
  )
}