'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
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

interface Props {
  initialSales: Sale[]
  initialPagination: Pagination
  initialCustomers: Contact[]
}

export function SalesTable({ initialSales, initialPagination, initialCustomers }: Props) {
  const { t } = useLanguage()
  const router = useRouter()
  const searchParams = useSearchParams()
  const user = useSession()
  const canView = user.capabilities.includes('sale.view')
  const canPost = user.capabilities.includes('sale.post')
  const canCancel = user.capabilities.includes('sale.cancel')

  // Sync local state with server-provided initial data
  const [sales, setSales] = useState<Sale[]>(initialSales)
  const [pagination, setPagination] = useState<Pagination>(initialPagination)
  const [customers, setCustomers] = useState<Contact[]>(initialCustomers)

  // Sync local state with server-provided initial data (RSC re-render on URL change)
  useEffect(() => {
    setSales(initialSales)
    setPagination(initialPagination)
    setCustomers(initialCustomers)
  }, [initialSales, initialPagination, initialCustomers])

  // URL-driven filters
  const query = searchParams.get('q') || ''
  const [queryInput, setQueryInput] = useState(query)
  const statusTab = searchParams.get('lifecycle_status') || ''
  const customerId = searchParams.get('customer_id') || ''
  const paymentState = searchParams.get('payment_state') || ''
  const fromDate = searchParams.get('from_iso') || ''
  const toDate = searchParams.get('to_iso') || ''
  const page = parseInt(searchParams.get('page') || '1')
  const perPage = parseInt(searchParams.get('per_page') || '25')
  const sortKey = (searchParams.get('sort') as 'sale_date' | 'total_amount' | 'id') || 'id'

  const [menuOpen, setMenuOpen] = useState<number | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [dialog, setDialog] = useState<ActiveDialog>(null)
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)
  const noticeTimer = useRef<number | null>(null)

  // Sync queryInput when URL changes externally (e.g. back/forward, clear filters)
  useEffect(() => {
    setQueryInput(query)
  }, [query])

  // Debounce search input → URL update
  useEffect(() => {
    if (queryInput === query) return
    const timer = window.setTimeout(() => {
      if (queryInput.trim()) {
        updateUrl({ q: queryInput.trim(), page: '1' })
      } else {
        updateUrl({ q: '', page: '1' })
      }
    }, 300)
    return () => window.clearTimeout(timer)
  }, [queryInput])

  useEffect(() => {
    return () => {
      if (noticeTimer.current) window.clearTimeout(noticeTimer.current)
    }
  }, [])

  const updateUrl = (newParams: Record<string, string>) => {
    const params = new URLSearchParams(searchParams)
    Object.entries(newParams).forEach(([key, value]) => {
      if (value) params.set(key, value)
      else params.delete(key)
    })
    router.replace(`/sales?${params.toString()}`, { scroll: false })
  }

  const resetUrl = () => {
    router.replace('/sales', { scroll: false })
  }

  const clearFilters = () => {
    resetUrl()
  }

  const toast = useCallback((text: string, isError = false) => {
    setNotice({ text, error: isError })
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(null), 3200)
  }, [])

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

  const handleSort = (key: 'sale_date' | 'total_amount' | 'id') => {
    updateUrl({ sort: key, page: '1' })
  }

  // Mutations
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
      resetUrl()
    } catch (e) {
      const code = (e as Error & { code?: string }).code
      const msg = e instanceof Error ? e.message : t('sales.postFailed')
      if (code === 'version_mismatch' || code === 'precondition_failed') {
        setDialogError(t('sales.serverChanged'))
        resetUrl()
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
      resetUrl()
    } catch (e) {
      const code = (e as Error & { code?: string }).code
      const msg = e instanceof Error ? e.message : t('sales.cancelFailed')
      if (code === 'version_mismatch' || code === 'precondition_failed') {
        setDialogError(t('sales.serverChanged'))
        resetUrl()
      } else {
        setDialogError(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  const activeFilterCount = [
    query.trim(),
    statusTab,
    customerId,
    paymentState,
    fromDate || toDate,
    sortKey !== 'id' ? sortKey : '',
  ].filter(Boolean).length

  return (
    <div className="content">
      {/* Toast */}\
      {notice && (
        <div className={`sales-toast${notice.error ? ' error' : ''}`} role="status">
          {notice.error ? <AlertTriangle size={15} /> : <Check size={15} />}
          {notice.text}
        </div>
      )}

      {/* Heading */}\
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('sales.eyebrow')}</div>
          <h1>{t('sales.title')}</h1>
          <p>{t('sales.subtitle')}</p>
        </div>
        <div className="heading-actions">
          <Button variant="outline" onClick={() => resetUrl()} disabled={busy}>
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

      {/* Page-scoped metrics */}\
      <section className="metrics">
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('sales.transactions')}</span>
            <span className="metric-icon"><FileText size={17} /></span>
          </div>
          <div className="metric-value">{pagination.total}</div>
          <div className="metric-change">
            <span className="change-note">
              {pagination.total > pagination.per_page
                ? `${rangeStart}–${rangeEnd} ${t('common.shown')}`
                : pagination.total > 0
                  ? t('sales.matchingFilters')
                  : t('sales.transactionsOnPage', { page: pagination.page, total_pages: pagination.total_pages })}
            </span>
          </div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('sales.pageValue')}</span>
            <span className="metric-icon"><FileText size={17} /></span>
          </div>
          <div className="metric-value">{formatIDR(pageRevenue)}</div>
          <div className="metric-change">
            <span className="change-note">{sales.length} {t('sales.onThisPage')}</span>
          </div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('sales.pageOutstanding')}</span>
            <span className="metric-icon"><FileText size={17} /></span>
          </div>
          <div className="metric-value">{formatIDR(pageOutstanding)}</div>
          <div className="metric-change">
            <span className="change-note">{t('sales.unpaidOnThisPage')}</span>
          </div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('sales.perPage')}</span>
            <span className="metric-icon"><FileText size={17} /></span>
          </div>
          <div className="metric-value">{perPage}</div>
          <div className="metric-change">
            <span className="change-note">Page {pagination.page} of {pagination.total_pages}</span>
          </div>
        </article>
      </section>

      {/* Status tabs */}\
      <div className="sales-tabs" role="tablist" aria-label="Filter by lifecycle status">
        {STATUS_TABS.map((tab) => {
          const active = statusTab === (tab.value ?? '')
          return (
            <button
              key={tab.labelKey}
              type="button"
              role="tab"
              aria-selected={active}
              className="sales-tab"
              onClick={() => {
                updateUrl({ lifecycle_status: tab.value ?? '', page: '1' })
              }}
            >
              {t(tab.labelKey)}
            </button>
          )
        })}
      </div>

      {/* Toolbar */}\
      <div className="sales-toolbar">
        <div className="sales-toolbar-row">
          <div className="sales-toolbar-fields">
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
                  defaultValue={customerId || ''}
                  onChange={(e) => { updateUrl({ customer_id: e.target.value, page: '1' }) }}
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
                  defaultValue={paymentState || ''}
                  onChange={(e) => { updateUrl({ payment_state: e.target.value, page: '1' }) }}
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
                  defaultValue={fromDate ? new Date(fromDate).toISOString().slice(0, 10) : ''}
                  max={toDate ? new Date(toDate).toISOString().slice(0, 10) : undefined}
                  onChange={(e) => {
                    const d = e.target.value
                    updateUrl({ from_iso: d ? new Date(`${d}T00:00:00`).toISOString() : '', page: '1' })
                  }}
                />
                <span className="sales-date-dash">–</span>
                <input
                  type="date"
                  aria-label="To date"
                  defaultValue={toDate ? new Date(toDate).toISOString().slice(0, 10) : ''}
                  min={fromDate ? new Date(fromDate).toISOString().slice(0, 10) : undefined}
                  onChange={(e) => {
                    const d = e.target.value
                    updateUrl({ to_iso: d ? new Date(`${d}T23:59:59`).toISOString() : '', page: '1' })
                  }}
                />
              </div>
            </div>
          </div>
          <button
            type="button"
            className="sales-filter-toggle"
            onClick={() => {}}
            aria-expanded={false}
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

      {/* Table */}\
      <section className="sales-card">
        {sales.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon"><FileText size={26} /></div>
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

            {/* Pagination */}\
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
                      updateUrl({ per_page: e.target.value, page: '1' })
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
                  onClick={() => updateUrl({ page: String(pagination.page - 1) })}
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
                  onClick={() => updateUrl({ page: String(pagination.page + 1) })}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      {/* Confirmation dialogs */}\
      {createOpen && (
        <CreateSaleDialog
          onClose={() => {
            setCreateOpen(false)
            resetUrl()
          }}
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

// Icon -----------------------------------------------------------------------

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
