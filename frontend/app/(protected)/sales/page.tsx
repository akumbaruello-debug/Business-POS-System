'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FileText,
  MoreHorizontal,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import { SortButton } from '@/components/sales/sort-button'
import { api } from '@/lib/api-client'
import type { Contact } from '@/lib/contact-types'
import type { Pagination, Sale, SaleListResponse } from '@/lib/sale-types'
import {
  fmtDate,
  isCancellable,
  num,
  sanitizeEtag,
} from '@/lib/sale-ui'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { LifecycleBadge, PaymentBadge } from '@/components/sales/sale-badges'
import { CancelSaleDialog, PostSaleDialog } from '@/components/sales/sale-dialogs'
import { CreateSaleDialog } from '@/components/sales/create-sale-dialog'
import { useLanguage } from '@/lib/i18n'

// -----------------------------------------------------------------------------
// Backend contract (backend/app/api/v1/sales.py + SaleService.list_sales):
//
//   GET /sales
//     ?page= &per_page= &sort= &q= &lifecycle_status= &customer_id= &payment_state=
//     &from_iso= &to_iso=                                     (capability sale.view)
//     → { data: Sale[], pagination }
//
//   POST /sales/{id}/post    (sale.post)   POST /sales/{id}/cancel (sale.cancel)
//
// `Sale` is the enriched envelope from SaleService._enrich_sale. Its
// `customer` sub-object is populated server-side (G4) — the list renders the
// real contact name and never falls back to an invented value.
// -----------------------------------------------------------------------------

const DEFAULT_PAGINATION: Pagination = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
}

/** Tab → backend `lifecycle_status` value.
 *  `null` means "All" (no lifecycle filter sent).
 *  Labels map 1:1 onto the real enum — no invented "Pending" bucket. */
const STATUS_TABS: ReadonlyArray<{ labelKey: string; value: string | null }> = [
  { labelKey: 'common.all', value: null },
  { labelKey: 'status.draft', value: 'draft' },
  { labelKey: 'status.posted', value: 'posted' },
  { labelKey: 'status.completed', value: 'completed' },
  { labelKey: 'status.partiallyReturned', value: 'partially_returned' },
  { labelKey: 'status.returned', value: 'returned' },
  { labelKey: 'status.cancelled', value: 'cancelled' },
]

const PER_PAGE_OPTIONS = [10, 25, 50, 100]


type ActiveDialog =
  | { kind: 'post'; sale: Sale }
  | { kind: 'cancel'; sale: Sale }
  | null

export default function SalesPage() {
  const user = useSession()
  const { t } = useLanguage()
  const canView = user.capabilities.includes('sale.view')
  const canPost = user.capabilities.includes('sale.post')
  const canCancel = user.capabilities.includes('sale.cancel')

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sales, setSales] = useState<Sale[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)

  // Filters (all server-side; nothing here is re-filtered on the client)
  const [query, setQuery] = useState('')
  const [queryInput, setQueryInput] = useState('')
  const [statusTab, setStatusTab] = useState<string | null>(null)
  const [customerId, setCustomerId] = useState('')
  const [paymentState, setPaymentState] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)

  // Server-side sort. The backend whitelist supports id, sale_date,
  // total_amount, lifecycle_status, created_at, updated_at and orders desc.
  const [sortKey, setSortKey] = useState<'sale_date' | 'total_amount' | 'id'>('id')

  // Real customer list for the filter dropdown (28 customers in dev; fetched once)
  const [customers, setCustomers] = useState<Contact[]>([])
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState<number | null>(null)
  const [createOpen, setCreateOpen] = useState(false)

  const [dialog, setDialog] = useState<ActiveDialog>(null)
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)
  const noticeTimer = useRef<number | null>(null)

  const toast = useCallback((text: string, isError = false) => {
    setNotice({ text, error: isError })
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(null), 3200)
  }, [])

  useEffect(() => {
    return () => {
      if (noticeTimer.current) window.clearTimeout(noticeTimer.current)
    }
  }, [])

  // Query strings for the API. Date bounds become full-day ISO instants so an
  // inclusive end date includes the whole day.
  const apiParams = useMemo(() => {
    const params: Record<string, string> = {
      page: String(page),
      per_page: String(perPage),
      sort: sortKey,
    }
    if (query.trim()) params.q = query.trim()
    if (statusTab) params.lifecycle_status = statusTab
    if (customerId) params.customer_id = customerId
    if (paymentState) params.payment_state = paymentState
    if (fromDate) params.from_iso = new Date(`${fromDate}T00:00:00`).toISOString()
    if (toDate) params.to_iso = new Date(`${toDate}T23:59:59`).toISOString()
    return params
  }, [page, perPage, sortKey, query, statusTab, customerId, paymentState, fromDate, toDate])

  const fetchSales = useCallback(async () => {
    if (!canView) {
      setError(t('errors.missingCapability', { capability: 'sale.view' }))
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<SaleListResponse>('/sales', { params: apiParams })
      setSales(Array.isArray(res.data) ? res.data : [])
      setPagination(res.pagination ?? DEFAULT_PAGINATION)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('sales.failedToLoad'))
      setSales([])
    } finally {
      setLoading(false)
    }
  }, [canView, apiParams])

  useEffect(() => {
    fetchSales()
  }, [fetchSales])

  // Debounce search input — sync to query after 300ms idle
  useEffect(() => {
    const t = window.setTimeout(() => {
      setQuery(queryInput)
      resetToFirstPage()
    }, 300)
    return () => window.clearTimeout(t)
  }, [queryInput])

  // Customer dropdown source — best-effort, never blocks the table.
  useEffect(() => {
    if (!canView) return
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.get<{ data: Contact[] }>('/contacts', {
          params: { 'filter[type]': 'customer', per_page: '500', sort: 'name' },
        })
        if (!cancelled) setCustomers(Array.isArray(res.data) ? res.data : [])
      } catch {
        /* best-effort: filter stays empty rather than inventing customers */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [canView])

  // Close the row menu on outside click / Escape.
  useEffect(() => {
    if (menuOpen == null) return
    const close = () => setMenuOpen(null)
    const key = (e: KeyboardEvent) => e.key === 'Escape' && close()
    document.addEventListener('click', close)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('click', close)
      document.removeEventListener('keydown', key)
    }
  }, [menuOpen])

  const resetToFirstPage = () => setPage(1)

  const clearFilters = () => {
    setQuery('')
    setQueryInput('')
    setStatusTab(null)
    setCustomerId('')
    setPaymentState('')
    setFromDate('')
    setToDate('')
    setSortKey('id')
    setPage(1)
  }

  const activeFilterCount = [
    query.trim(),
    statusTab,
    customerId,
    paymentState,
    fromDate || toDate,
    sortKey !== 'id' ? sortKey : '',
  ].filter(Boolean).length

  const handleSort = (key: 'sale_date' | 'total_amount' | 'id') => {
    setSortKey(key)
    resetToFirstPage()
  }

  // ---------------------------------------------------------------------------
  // Mutations — shared by the toolbar dialog and the row menu
  // ---------------------------------------------------------------------------

  const openPost = (sale: Sale) => {
    setMenuOpen(null)
    setDialogError(null)
    setDialog({ kind: 'post', sale })
  }

  const openCancel = (sale: Sale) => {
    setMenuOpen(null)
    setDialogError(null)
    setDialog({ kind: 'cancel', sale })
  }

  const closeDialog = () => {
    if (busy) return
    setDialog(null)
    setDialogError(null)
  }

  const handlePost = async () => {
    if (!dialog || dialog.kind !== 'post' || busy) return
    const { sale } = dialog
    setBusy(true)
    setDialogError(null)
    try {
      await api.headers.post(`/sales/${sale.id}/post`, {}, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          ...(sanitizeEtag(sale.etag) ? { 'If-Match': sanitizeEtag(sale.etag) } : {}),
        },
      })
      toast(t('sales.postedToast', { ref: sale.reference_no ?? `#${sale.id}` }))
      setDialog(null)
      await fetchSales()
    } catch (e) {
      const code = (e as Error & { code?: string }).code
      const msg = e instanceof Error ? e.message : t('sales.postFailed')
      if (code === 'version_mismatch' || code === 'precondition_failed') {
        setDialogError(t('sales.serverChanged'))
        await fetchSales()
      } else {
        setDialogError(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  const handleCancel = async (reason: string) => {
    if (!dialog || dialog.kind !== 'cancel' || busy) return
    const { sale } = dialog
    setBusy(true)
    setDialogError(null)
    try {
      await api.headers.post(`/sales/${sale.id}/cancel`, { reason }, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          ...(sanitizeEtag(sale.etag) ? { 'If-Match': sanitizeEtag(sale.etag) } : {}),
        },
      })
      toast(t('sales.cancelledToast', { ref: sale.reference_no ?? `#${sale.id}` }))
      setDialog(null)
      await fetchSales()
    } catch (e) {
      const code = (e as Error & { code?: string }).code
      const msg = e instanceof Error ? e.message : t('sales.cancelFailed')
      if (code === 'version_mismatch' || code === 'precondition_failed') {
        setDialogError(t('sales.serverChanged'))
        await fetchSales()
      } else {
        setDialogError(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Filtered-state summary shown to the user (server-derived, not invented)
  // ---------------------------------------------------------------------------

  const rangeStart = pagination.total === 0 ? 0 : (pagination.page - 1) * pagination.per_page + 1
  const rangeEnd = Math.min(pagination.page * pagination.per_page, pagination.total)

  const pageRevenue = useMemo(
    () => sales.reduce((n, s) => n + num(s.total_amount), 0),
    [sales],
  )
  const pageOutstanding = useMemo(
    () => sales.reduce((n, s) => n + num(s.outstanding), 0),
    [sales],
  )

  const customerName = (s: Sale): string => {
    if (s.customer?.name) return s.customer.name
    if (s.customer_id) return `Customer #${s.customer_id}`
    return t('sales.walkInCustomer')
  }

  // ---------------------------------------------------------------------------
  // Filter controls — one definition, rendered in the toolbar and the drawer
  // ---------------------------------------------------------------------------

  const filterFields = (
    <>
      <div className="sales-field sales-field-grow">
        <span className="sales-field-label">{t('common.search')}</span>
        <div className="sales-search">
          <Search size={15} />
          <input
            aria-label="Search sales"
            value={queryInput}
            onChange={(e) => setQueryInput(e.target.value)}
            placeholder={t('sales.searchPlaceholder')}
          />
        </div>
      </div>

      <div className="sales-field">
        <span className="sales-field-label">{t('sales.customer')}</span>
        <div className="sales-select">
          <select
            aria-label="Filter by customer"
            value={customerId}
            onChange={(e) => {
              setCustomerId(e.target.value)
              resetToFirstPage()
            }}
          >
            <option value="">{t('sales.allCustomers')}</option>
            {customers.map((c) => (
              <option key={c.id} value={String(c.id)}>
                {c.name}
              </option>
            ))}
          </select>
          <ChevronDown size={14} />
        </div>
      </div>

      <div className="sales-field">
        <span className="sales-field-label">{t('sales.payment')}</span>
        <div className="sales-select">
          <select
            aria-label="Filter by payment state"
            value={paymentState}
            onChange={(e) => {
              setPaymentState(e.target.value)
              resetToFirstPage()
            }}
          >
            <option value="">{t('sales.allPaymentStates')}</option>
            <option value="unpaid">{t('status.unpaid')}</option>
            <option value="partial">{t('status.partial')}</option>
            <option value="paid">{t('status.paid')}</option>
          </select>
          <ChevronDown size={14} />
        </div>
      </div>

      <div className="sales-field">
        <span className="sales-field-label">{t('sales.saleDate')}</span>
        <div className="sales-date-range">
          <CalendarIcon />
          <input
            type="date"
            aria-label="From date"
            value={fromDate}
            max={toDate || undefined}
            onChange={(e) => {
              setFromDate(e.target.value)
              resetToFirstPage()
            }}
          />
          <span className="sales-date-dash">–</span>
          <input
            type="date"
            aria-label="To date"
            value={toDate}
            min={fromDate || undefined}
            onChange={(e) => {
              setToDate(e.target.value)
              resetToFirstPage()
            }}
          />
        </div>
      </div>
    </>
  )

  return (
    <div className="content">
      {/* Toast */}
      {notice && (
        <div className={`sales-toast${notice.error ? ' error' : ''}`} role="status">
          {notice.error ? <AlertTriangle size={15} /> : <Check size={15} />}
          {notice.text}
        </div>
      )}

      {/* Heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('sales.eyebrow')}</div>
          <h1>{t('sales.title')}</h1>
          <p>{t('sales.subtitle')}</p>
        </div>
        <div className="heading-actions">
          <Button variant="outline" onClick={fetchSales} disabled={loading}>
            <RefreshCw size={14} />
            {t('common.refresh')}
          </Button>
          {user.capabilities.includes('sale.create') && (
            <Button onClick={() => setCreateOpen(true)}>
              <Plus size={14} />
              {t('sales.newSale')}
            </Button>
          )}
        </div>
      </div>

      {/* Page-scoped metrics. Labelled explicitly as current-page figures —
          the list endpoint returns no aggregate totals, so none are implied. */}
      <section className="metrics">
        <MetricCard
          label={t('sales.transactions')}
          value={loading ? '—' : String(pagination.total)}
          note={
            pagination.total > pagination.per_page
              ? `${rangeStart}–${rangeEnd} ${t('common.shown')}`
              : t('sales.matchingFilters')
          }
        />
        <MetricCard
          label={t('sales.pageValue')}
          value={loading ? '—' : formatIDR(pageRevenue)}
          note={`${sales.length} ${t('sales.onThisPage')}`}
        />
        <MetricCard
          label={t('sales.pageOutstanding')}
          value={loading ? '—' : formatIDR(pageOutstanding)}
          note={t('sales.unpaidOnThisPage')}
        />
        <MetricCard
          label={t('sales.perPage')}
          value={String(perPage)}
          note={`Page ${pagination.page} of ${pagination.total_pages}`}
        />
      </section>

      {/* Status tabs — reflect the real lifecycle enum; no fabricated counts. */}
      <div className="sales-tabs" role="tablist" aria-label="Filter by lifecycle status">
        {STATUS_TABS.map((tab) => {
          const active = statusTab === tab.value
          return (
            <button
              key={tab.labelKey}
              type="button"
              role="tab"
              aria-selected={active}
              className="sales-tab"
              onClick={() => {
                setStatusTab(tab.value)
                resetToFirstPage()
              }}
            >
              {t(tab.labelKey)}
            </button>
          )
        })}
      </div>

      {/* Toolbar */}
      <div className="sales-toolbar">
        <div className="sales-toolbar-row">
          <div className="sales-toolbar-fields">{filterFields}</div>

          <button
            type="button"
            className="sales-filter-toggle"
            onClick={() => setMobileFiltersOpen(true)}
            aria-expanded={mobileFiltersOpen}
          >
            <SlidersHorizontal size={15} />
            Filters
            {activeFilterCount > 0 && (
              <span className="sales-filter-count">{activeFilterCount}</span>
            )}
          </button>

          <div className="sales-toolbar-actions">
            {activeFilterCount > 0 && (
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
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="skeleton" style={{ height: 46 }} />
            ))}
          </div>
        ) : error ? (
          <div className="error-state">
            <div className="error-icon">
              <AlertTriangle size={28} />
            </div>
            <strong>{t('sales.failedToLoad')}</strong>
            <p style={{ maxWidth: 440, fontSize: 13 }}>{error}</p>
            <Button variant="outline" onClick={fetchSales}>
              <RefreshCw size={14} />
              {t('common.tryAgain')}
            </Button>
          </div>
        ) : sales.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon">
              <FileText size={26} />
            </div>
            <strong>{t('sales.noSalesFound')}</strong>
            <p>
              {activeFilterCount > 0
                ? 'No sales match the current filters.'
                : 'Sales will appear here once transactions are recorded.'}
            </p>
            {activeFilterCount > 0 && (
              <Button variant="outline" onClick={clearFilters}>
                Clear filters
              </Button>
            )}
          </div>
        ) : (
          <>
            <div className="sales-table-wrap">
              <table className="sales-table">
                <thead>
                  <tr>
                    <th>
                      <SortButton
                        label={t('sales.reference')}
                        active={sortKey === 'id'}
                        sortDir="desc"
                        onClick={() => handleSort('id')}
                      />
                    </th>
                    <th>{t('sales.customer')}</th>
                    <th>{t('common.date')}</th>
                    <th className="sales-th-right">
                      <SortButton
                        label={t('sales.amount')}
                        active={sortKey === 'total_amount'}
                        sortDir="desc"
                        onClick={() => handleSort('total_amount')}
                        align="right"
                      />
                    </th>
                    <th className="sales-th-right">{t('sales.outstanding')}</th>
                    <th>{t('sales.lifecycle')}</th>
                    <th>{t('sales.payment')}</th>
                    <th className="sales-th-actions">
                      <span className="sr-only">{t('common.actions')}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sales.map((s) => {
                    const cancellable = canCancel && isCancellable(s.lifecycle_status)
                    const postable = canPost && s.lifecycle_status === 'draft'
                    const hasActions = cancellable || postable
                    return (
                      <tr key={s.id}>
                        <td>
                          <Link href={`/sales/${s.id}`} className="sales-ref">
                            {s.reference_no ?? `#${s.id}`}
                          </Link>
                        </td>
                        <td className="sales-muted">{customerName(s)}</td>
                        <td className="sales-muted" style={{ whiteSpace: 'nowrap' }}>
                          {fmtDate(s.sale_date)}
                        </td>
                        <td className="sales-td-right sales-num">
                          {formatIDR(num(s.total_amount))}
                        </td>
                        <td className="sales-td-right sales-num">
                          {num(s.outstanding) > 0 ? (
                            <span style={{ color: '#dc2626' }}>
                              {formatIDR(num(s.outstanding))}
                            </span>
                          ) : (
                            <span className="sales-muted">—</span>
                          )}
                        </td>
                        <td>
                          <LifecycleBadge status={s.lifecycle_status} />
                        </td>
                        <td>
                          <PaymentBadge state={s.payment_state} />
                        </td>
                        <td className="sales-td-actions">
                          {hasActions ? (
                            <div
                              className="sales-menu-wrap"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <button
                                type="button"
                                className="sales-menu-trigger"
                                aria-label={`Actions for ${s.reference_no ?? `#${s.id}`}`}
                                aria-haspopup="menu"
                                aria-expanded={menuOpen === s.id}
                                onClick={() => setMenuOpen(menuOpen === s.id ? null : s.id)}
                              >
                                <MoreHorizontal size={16} />
                              </button>
                              {menuOpen === s.id && (
                                <div className="sales-menu" role="menu">
                                  {postable && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      onClick={() => openPost(s)}
                                    >
                                      <Check size={14} />
                                      {t('sales.postSale')}
                                    </button>
                                  )}
                                  {cancellable && (
                                    <button
                                      type="button"
                                      role="menuitem"
                                      className="danger"
                                      onClick={() => openCancel(s)}
                                    >
                                      <X size={14} />
                                      {t('sales.cancelSale')}
                                    </button>
                                  )}
                                </div>
                              )}
                            </div>
                          ) : (
                            <span className="sales-muted" aria-hidden="true">
                              —
                            </span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination — server-driven */}
            <div className="sales-pagination">
              <span>
                Showing {rangeStart}–{rangeEnd} of {pagination.total}
                {activeFilterCount > 0 ? ' matching sales' : ' sales'}
              </span>
              <div className="sales-pagination-nav">
                <label className="sales-field" style={{ gap: 0 }}>
                  <span className="sr-only">{t('common.rowsPerPage')}</span>
                  <select
                    className="sales-perpage"
                    aria-label="Rows per page"
                    value={String(perPage)}
                    onChange={(e) => {
                      setPerPage(Number(e.target.value))
                      resetToFirstPage()
                    }}
                  >
                    {PER_PAGE_OPTIONS.map((n) => (
                      <option key={n} value={String(n)}>
                        {n} / {t('common.page')}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="sales-pager"
                  aria-label="Previous page"
                  disabled={!pagination.has_prev}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  <ChevronLeft size={16} />
                </button>
                <span style={{ fontWeight: 600, color: '#475569' }}>
                  {pagination.page} / {pagination.total_pages}
                </span>
                <button
                  type="button"
                  className="sales-pager"
                  aria-label="Next page"
                  disabled={!pagination.has_next}
                  onClick={() => setPage((p) => p + 1)}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>


      {/* Mobile filter drawer */}
      {mobileFiltersOpen && (
        <div
          className="sales-drawer-backdrop"
          onMouseDown={() => setMobileFiltersOpen(false)}
        >
          <div
            className="sales-drawer"
            role="dialog"
            aria-modal="true"
            aria-label={t('sales.filters')}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="sales-drawer-head">
              <div>
                <h2>{t('sales.filters')}</h2>
                <p>
                  {activeFilterCount} {activeFilterCount === 1 ? t('sales.activeFilter', { count: activeFilterCount }) : t('sales.activeFilters', { count: activeFilterCount })}
                </p>
              </div>
              <button
                type="button"
                className="sales-dialog-close"
                aria-label="Close filters"
                onClick={() => setMobileFiltersOpen(false)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="sales-drawer-body">{filterFields}</div>
            <div className="sales-drawer-foot">
              <Button variant="outline" onClick={clearFilters} disabled={activeFilterCount === 0}>
                {t('sales.clearAll')}
              </Button>
              <Button onClick={() => setMobileFiltersOpen(false)}>{t('common.done')}</Button>
            </div>
          </div>
        </div>
      )}

      {/* Create / confirmation dialogs — one shared system, no window.prompt/confirm */}
      {createOpen && (
        <CreateSaleDialog
          onClose={() => setCreateOpen(false)}
          busy={busy}
          setBusy={setBusy}
        />
      )}

      {dialog?.kind === 'post' && (
        <PostSaleDialog
          reference={dialog.sale.reference_no ?? `#${dialog.sale.id}`}
          busy={busy}
          error={dialogError}
          onClose={closeDialog}
          onConfirm={handlePost}
        />
      )}
      {dialog?.kind === 'cancel' && (
        <CancelSaleDialog
          reference={dialog.sale.reference_no ?? `#${dialog.sale.id}`}
          busy={busy}
          error={dialogError}
          onClose={closeDialog}
          onConfirm={handleCancel}
        />
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Small local presentational helpers
// -----------------------------------------------------------------------------

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



function MetricCard({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note: string
}) {
  return (
    <article className="metric-card">
      <div className="metric-top">
        <span className="metric-label">{label}</span>
        <span className="metric-icon">
          <FileText size={17} />
        </span>
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-change">
        <span className="change-note">{note}</span>
      </div>
    </article>
  )
}
