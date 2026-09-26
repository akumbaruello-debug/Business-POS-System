'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Plus,
  Receipt,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { api, isApiError } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { CreateEntryDialog } from '@/components/manual-entries/create-dialog'
import { CancelEntryDialog } from '@/components/manual-entries/cancel-dialog'
import type {
  FinancialCategory,
  ManualEntry,
  ManualEntryListResponse,
  PaymentMethod,
} from '@/lib/finance-types'
import { fetchFinancialCategories } from '@/lib/financial-categories-service'
import { fetchPaymentMethods } from '@/lib/payment-methods-service'
import { formatIDR, formatDate, formatInt } from '@/lib/format'
import { num } from '@/lib/sale-ui'
import { useSession } from '@/lib/session'
import { useCan } from '@/lib/authz'
import { useLanguage } from '@/lib/i18n'

// Backend contract (backend/app/api/v1/manual_entries.py):
//   GET  /manual-entries?...                    → MetaEnvelope[ManualEntryResponse]
//   GET  /manual-entries/{id}                   → ManualEntryResponse (ETag header)
//   POST /manual-entries                        → ManualEntryResponse (Idempotency-Key required)
//   POST /manual-entries/{id}/cancel            → ManualEntryResponse (Idempotency-Key + If-Match)
//
// Capabilities (NOT the stale finance.manual_* from Database-Design §14):
//   manual_entry.view           → list + get
//   manual_entry.create_income  → POST income
//   manual_entry.create_expense → POST expense
//   manual_entry.cancel         → POST /cancel (with If-Match ETag)

export default function ManualEntriesPage() {
  const user = useSession()
  const { t, language } = useLanguage()
  const { can } = useCan()

  const canView = can('manual_entry.view')
  const canCreateIncome = can('manual_entry.create_income')
  const canCreateExpense = can('manual_entry.create_expense')
  const canCancel = can('manual_entry.cancel')

  const canCreate = canCreateIncome || canCreateExpense

  // Data
  const [entries, setEntries] = useState<ManualEntry[]>([])
  const [categories, setCategories] = useState<FinancialCategory[]>([])
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])

  // State
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // Filters
  const [entryTypeFilter, setEntryTypeFilter] = useState<string>('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [queryInput, setQueryInput] = useState('')

  // Pagination
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [totalPages, setTotalPages] = useState(0)
  const [totalItems, setTotalItems] = useState(0)

  // Dialogs
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false)
  const [cancelEntry, setCancelEntry] = useState<ManualEntry | null>(null)
  const [cancelEtag, setCancelEtag] = useState('')
  const [busy, setBusy] = useState(false)

  // Debounced search
  const [searchQuery, setSearchQuery] = useState('')
  useEffect(() => {
    const h = setTimeout(() => setSearchQuery(queryInput.trim()), 350)
    return () => clearTimeout(h)
  }, [queryInput])

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const fetchEntries = useCallback(async () => {
    if (!canView) return
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string | number> = {
        page: page,
        per_page: perPage,
        sort: '-id',
      }
      if (entryTypeFilter) params['filter[entry_type]'] = entryTypeFilter
      if (categoryFilter) params['filter[category_id]'] = Number(categoryFilter)
      if (statusFilter) params['filter[lifecycle_status]'] = statusFilter
      if (fromDate) params.from = new Date(`${fromDate}T00:00:00`).toISOString()
      if (toDate) params.to = new Date(`${toDate}T23:59:59`).toISOString()
      if (searchQuery) params.q = searchQuery

      const res = await api.get<ManualEntryListResponse>('/manual-entries', { params })
      setEntries(res.data ?? [])
      setTotalItems(res.pagination?.total ?? 0)
      setTotalPages(res.pagination?.total_pages ?? 0)
    } catch (e) {
      setError(
        isApiError(e)
          ? e.message
          : t('manualEntries.failedToLoad'),
      )
    } finally {
      setLoading(false)
    }
  }, [canView, page, perPage, entryTypeFilter, categoryFilter, statusFilter, fromDate, toDate, searchQuery, t])

  const fetchMasterData = useCallback(async () => {
    try {
      const [cats, methods] = await Promise.all([
        fetchFinancialCategories(),
        fetchPaymentMethods(),
      ])
      setCategories(cats)
      setPaymentMethods(methods)
    } catch (e) {
      // Master data failures shouldn't block the page — categories/methods are needed for create,
      // but listing works without them.
      console.error('Failed to fetch master data', e)
    }
  }, [])

  useEffect(() => {
    void fetchMasterData()
  }, [fetchMasterData])

  useEffect(() => {
    if (canView) void fetchEntries()
  }, [canView, fetchEntries])

  // Reset to page 1 when filters change
  useEffect(() => {
    setPage(1)
  }, [entryTypeFilter, categoryFilter, statusFilter, fromDate, toDate, searchQuery])

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      await Promise.all([fetchEntries(), fetchMasterData()])
    } finally {
      setRefreshing(false)
    }
  }

  const availableTypes = (
    canCreateIncome && canCreateExpense
      ? ['income', 'expense']
      : canCreateIncome
        ? ['income']
        : canCreateExpense
          ? ['expense']
          : []
  ) as ('income' | 'expense')[]

  const openCancel = async (id: number) => {
    try {
      const res = await api.headers.get<ManualEntry>(`/manual-entries/${id}`)
      setCancelEntry(res.data)
      setCancelEtag(res.headers.get('etag') ?? '')
      setCancelDialogOpen(true)
    } catch (e) {
      console.error('Failed to fetch entry for cancel', e)
    }
  }

  const handleCreated = (_entry: ManualEntry) => {
    void fetchEntries()
  }

  const handleCancelled = (refreshed: ManualEntry) => {
    void fetchEntries()
    setCancelEntry(null)
    setCancelEtag('')
  }

  const handleCancelConflict = () => {
    // ETag mismatch on cancel — the entry was modified elsewhere.
    // The cancel dialog already surfaces the error; close it and let the
    // list refresh to reflect the latest server state.
    setCancelDialogOpen(false)
    setCancelEntry(null)
    setCancelEtag('')
    void fetchEntries()
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (!canView) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <h1>{t('manualEntries.title')}</h1>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={32} />
          </div>
          <strong>{t('common.accessRestricted')}</strong>
          <p>{t('common.permissionContactAdmin')}</p>
        </div>
      </div>
    )
  }

  const methodMap = new Map(paymentMethods.map((m) => [m.id, m]))

  const statusBadge = (status: string) => {
    if (status === 'posted') {
      return <span className="tag tag-green">{t('manualEntries.status.posted')}</span>
    }
    return <span className="tag tag-grey">{t('manualEntries.status.cancelled')}</span>
  }

  const typeBadge = (type: string) => {
    if (type === 'income') {
      return <span className="tag tag-blue">{t('manualEntries.income')}</span>
    }
    return <span className="tag tag-amber">{t('manualEntries.expense')}</span>
  }

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <h1>{t('manualEntries.title')}</h1>
          <p className="muted">{t('manualEntries.subtitle')}</p>
        </div>
        <div className="heading-actions">
          <span className="as-of-note">
            {totalItems > 0
              ? t('manualEntries.count', { n: totalItems })
              : ''}
          </span>
          {canCreate && (
            <Button onClick={() => setCreateDialogOpen(true)} disabled={busy || loading}>
              <Plus size={14} /> {t('manualEntries.addEntry')}
            </Button>
          )}
          <Button variant="outline" onClick={handleRefresh} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
            {refreshing ? t('common.refreshing') : t('common.refresh')}
          </Button>
        </div>
      </div>

      {error && (
        <div className="notice error">
          {error}
          <button type="button" className="link" onClick={() => void fetchEntries()}>
            {t('common.retry')}
          </button>
        </div>
      )}

      {/* Filters */}
      <div className="dashboard-filters" style={{ marginBottom: 12 }}>
        <label className="filter-field">
          <span className="filter-label">{t('manualEntries.filter.entryType')}</span>
          <select
            aria-label={t('manualEntries.filter.entryType')}
            value={entryTypeFilter}
            onChange={(e) => setEntryTypeFilter(e.target.value)}
            disabled={loading}
          >
            <option value="">{t('manualEntries.filter.allTypes')}</option>
            <option value="income">{t('manualEntries.income')}</option>
            <option value="expense">{t('manualEntries.expense')}</option>
          </select>
        </label>

        <label className="filter-field">
          <span className="filter-label">{t('manualEntries.filter.category')}</span>
          <select
            aria-label={t('manualEntries.filter.category')}
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            disabled={loading || categories.length === 0}
          >
            <option value="">{t('manualEntries.filter.allCategories')}</option>
            {categories
              .filter((c) => c.is_active)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.entry_type})
                </option>
              ))}
          </select>
        </label>

        <label className="filter-field">
          <span className="filter-label">{t('manualEntries.filter.status')}</span>
          <select
            aria-label={t('manualEntries.filter.status')}
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            disabled={loading}
          >
            <option value="">{t('manualEntries.filter.allStatuses')}</option>
            <option value="posted">{t('manualEntries.status.posted')}</option>
            <option value="cancelled">{t('manualEntries.status.cancelled')}</option>
          </select>
        </label>

        <label className="filter-field">
          <span className="filter-label">{t('manualEntries.filter.dateRange')}</span>
          <div className="sales-date-range">
            <input
              type="date"
              aria-label={t('manualEntries.filter.from')}
              value={fromDate}
              max={toDate || undefined}
              onChange={(e) => setFromDate(e.target.value)}
              disabled={loading}
            />
            <span className="sales-date-dash">–</span>
            <input
              type="date"
              aria-label={t('manualEntries.filter.to')}
              value={toDate}
              min={fromDate || undefined}
              onChange={(e) => setToDate(e.target.value)}
              disabled={loading}
            />
          </div>
        </label>

        <label className="filter-field" style={{ flex: 1 }}>
          <span className="filter-label">{t('manualEntries.filter.search')}</span>
          <div className="search-box">
            <Search size={15} />
            <input
              value={queryInput}
              onChange={(e) => setQueryInput(e.target.value)}
              placeholder={t('manualEntries.filter.searchPlaceholder')}
              disabled={loading}
            />
            {queryInput && (
              <button
                type="button"
                className="icon-btn"
                onClick={() => setQueryInput('')}
                disabled={loading}
              >
                <X size={14} />
              </button>
            )}
          </div>
        </label>
      </div>

      {/* Table */}
      <section className="panel">
        {loading ? (
          <div style={{ padding: 16 }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="skeleton" style={{ height: 46, marginBottom: 4 }} />
            ))}
          </div>
        ) : entries.length === 0 ? (
          <div className="sales-table-wrap">
            <div className="empty-workspace">
              <div className="empty-icon">
                <Receipt size={26} />
              </div>
              <strong>{t('manualEntries.empty.title')}</strong>
              <p>{t('manualEntries.empty.desc')}</p>
            </div>
          </div>
        ) : (
          <>
            <div className="sales-table-wrap">
              <table className="sales-table">
                <thead>
                  <tr>
                    <th>{t('manualEntries.col.date')}</th>
                    <th>{t('manualEntries.col.type')}</th>
                    <th>{t('manualEntries.col.category')}</th>
                    <th>{t('manualEntries.col.paymentMethod')}</th>
                    <th className="sales-td-right">{t('manualEntries.col.amount')}</th>
                    <th>{t('manualEntries.col.status')}</th>
                    <th>{t('manualEntries.col.createdBy')}</th>
                    <th className="sales-td-right">{t('manualEntries.col.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => {
                    const method = methodMap.get(e.payment_method_id)
                    return (
                      <tr key={e.id}>
                        <td>{formatDate(e.entry_date, language)}</td>
                        <td>{typeBadge(e.entry_type)}</td>
                        <td>
                          <Link href={`/finance/manual-entries/${e.id}`} className="sales-ref">
                            <strong>{e.category_name ?? `#${e.category_id}`}</strong>
                          </Link>
                        </td>
                        <td className="muted">{method?.name ?? `#${e.payment_method_id}`}</td>
                        <td className="sales-td-right sales-num">
                          {formatIDR(num(e.amount), language)}
                        </td>
                        <td>{statusBadge(e.lifecycle_status)}</td>
                        <td className="muted">{formatInt(e.created_by)}</td>
                        <td className="row-actions">
                          {canCancel &&
                            e.lifecycle_status === 'posted' && (
                            <button
                              type="button"
                              className="icon-btn danger"
                              aria-label={t('manualEntries.cancelEntry')}
                              onClick={() => void openCancel(e.id)}
                              title={t('manualEntries.cancelEntry')}
                            >
                              <X size={14} />
                            </button>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="sales-pagination">
              <span>
                {totalItems === 0
                  ? '0 / 0'
                  : `${Math.min((page - 1) * perPage + 1, totalItems)}–${Math.min(page * perPage, totalItems)} of ${totalItems}`}
              </span>
              <div className="sales-pagination-nav">
                <label className="sales-field" style={{ gap: 0 }}>
                  <span className="sr-only">Rows per page</span>
                  <select
                    className="sales-perpage"
                    aria-label="Rows per page"
                    value={String(perPage)}
                    onChange={(e) => {
                      setPerPage(Number(e.target.value))
                      setPage(1)
                    }}
                  >
                    {[10, 25, 50, 100].map((n) => (
                      <option key={n} value={String(n)}>
                        {n} / page
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="sales-pager"
                  aria-label="Previous page"
                  disabled={page <= 1 || loading}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  <ChevronLeft size={16} />
                </button>
                <span style={{ fontWeight: 600, color: '#475569' }}>
                  {page} / {totalPages || 1}
                </span>
                <button
                  type="button"
                  className="sales-pager"
                  aria-label="Next page"
                  disabled={page >= totalPages || loading}
                  onClick={() => setPage((p) => p + 1)}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      {/* Create dialog */}
      {canCreate && (
        <CreateEntryDialog
          open={createDialogOpen}
          onClose={() => setCreateDialogOpen(false)}
          categories={categories}
          paymentMethods={paymentMethods}
          availableTypes={availableTypes}
          onCreated={handleCreated}
          t={(key, params) => t(key, params)}
        />
      )}

      {/* Cancel dialog */}
      {cancelDialogOpen && cancelEntry && (
        <CancelEntryDialog
          open={cancelDialogOpen}
          onClose={() => {
            setCancelDialogOpen(false)
            setCancelEntry(null)
            setCancelEtag('')
          }}
          entry={cancelEntry}
          entryEtag={cancelEtag}
          onCancelled={handleCancelled}
          onConflict={handleCancelConflict}
          t={(key, params) => t(key, params)}
        />
      )}
    </div>
  )
}
