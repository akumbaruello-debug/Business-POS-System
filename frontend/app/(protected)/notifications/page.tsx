'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Bell,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import { useCan } from '@/lib/authz'
import { formatDateTime } from '@/lib/format'
import type { Notification, NotificationListResponse } from '@/lib/notification-types'

// ---------------------------------------------------------------------------
// Constants — mirror backend whitelists (notifications.py + services/notifications.py)
// ---------------------------------------------------------------------------

const DEFAULT_PAGINATION = {
  page: 1,
  per_page: 50,
  total: 0,
  total_pages: 0,
}

const PER_PAGE_OPTIONS = [10, 25, 50, 100]

// Backend SORT whitelist: created_at, -created_at, id, -id
const SORT_OPTIONS = ['-created_at', 'created_at', '-id', 'id']

// Read filter options
const READ_FILTER_OPTIONS = [
  { value: '', labelKey: 'notifications.filter.all' },
  { value: 'false', labelKey: 'notifications.filter.unread' },
  { value: 'true', labelKey: 'notifications.filter.read' },
] as const

// Type labels — translated via i18n
const TYPE_LABELS: Record<string, string> = {
  low_stock: 'notifications.type.lowStock',
  out_of_stock: 'notifications.type.outOfStock',
  below_cost_sale: 'notifications.type.belowCostSale',
  system: 'notifications.type.system',
  action_confirmation: 'notifications.type.actionConfirmation',
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string, locale: string): string {
  return formatDateTime(iso, locale)
}

function relatedEntityLabel(notif: Notification, t: (k: string) => string): string {
  if (!notif.related_entity_type || notif.related_entity_id == null) {
    return '—'
  }
  return `${capitalize(notif.related_entity_type)} #${notif.related_entity_id}`
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function NotificationsPage() {
  const session = useSession()
  const { t, language } = useLanguage()
  const { can } = useCan()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<NotificationListResponse | null>(null)
  const [pagination] = useState(() => ({ ...DEFAULT_PAGINATION }))

  // Capability check
  const hasViewCap = can('notification.view')
  const canMarkRead = can('notification.mark_read')

  // Filters
  const [readFilter, setReadFilter] = useState('')
  const [q, setQ] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [sort, setSort] = useState('-created_at')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(50)

  // Map readFilter string to is_read boolean | undefined
  const isReadParam = useMemo(() => {
    if (readFilter === '') return undefined
    return readFilter === 'true'
  }, [readFilter])

  const locale = language === 'id' ? 'id-ID' : 'en-US'

  const buildParams = useCallback((): Record<string, string> => {
    const params: Record<string, string> = {
      page: String(page),
      per_page: String(perPage),
    }
    if (sort) params.sort = sort
    if (q) params.q = q
    if (from) params.from = new Date(`${from}T00:00:00`).toISOString()
    if (to) params.to = new Date(`${to}T23:59:59`).toISOString()
    if (isReadParam !== undefined) params['filter[is_read]'] = String(isReadParam)
    return params
  }, [page, perPage, sort, q, from, to, isReadParam])

  const load = useCallback(async () => {
    if (!hasViewCap) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<NotificationListResponse>('/notifications', {
        params: buildParams(),
      })
      setData(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('notifications.loadFailed'))
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [hasViewCap, buildParams, t])

  useEffect(() => {
    load()
  }, [load])

  // --- Mark read actions ---

  const handleMarkRead = async (notif: Notification) => {
    if (!canMarkRead) return
    try {
      await api.post(`/notifications/${notif.id}/mark-read`)
      // Optimistically update: flip is_read on this row
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          data: prev.data.map((n) =>
            n.id === notif.id ? { ...n, is_read: true } : n,
          ),
        }
      })
    } catch (err) {
      // Restore + show error
      const msg = err instanceof Error ? err.message : t('common.error')
      setError(msg)
    }
  }

  const handleMarkAllRead = async () => {
    if (!canMarkRead) return
    try {
      const res = await api.post<{ updated: number }>('/notifications/mark-all-read')
      // Reconcile: mark all loaded rows as read
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          data: prev.data.map((n) => ({ ...n, is_read: true })),
          pagination: {
            ...prev.pagination,
            total: prev.pagination.total - (res.updated ?? 0),
          },
        }
      })
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('common.error')
      setError(msg)
    }
  }

  // --- Filter helpers ---

  const handleClearFilters = () => {
    setReadFilter('')
    setQ('')
    setFrom('')
    setTo('')
    setSort('-created_at')
    setPage(1)
  }

  const activeFilterCount = useMemo(() => {
    let count = 0
    if (readFilter) count += 1
    if (q) count += 1
    if (from || to) count += 1
    return count
  }, [readFilter, q, from, to])

  const notifications = data?.data ?? []
  const hasUnread = notifications.some((n) => !n.is_read)
  const actualPagination = data?.pagination ?? DEFAULT_PAGINATION

  // --- Capability-gated render ---

  if (!hasViewCap) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">{t('nav.notifications')}</div>
            <h1>{t('notifications.title')}</h1>
            <p>{t('notifications.subtitle')}</p>
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

  // --- Error state ---

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">{t('nav.notifications')}</div>
            <h1>{t('notifications.title')}</h1>
            <p>{t('notifications.subtitle')}</p>
          </div>
          <div className="heading-actions">
            <button className="button button-secondary" onClick={() => load()} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'spin' : ''} />
              {t('common.retry')}
            </button>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={28} />
          </div>
          <strong>{t('notifications.loadFailed')}</strong>
          <p style={{ maxWidth: 440, fontSize: 13 }}>{error}</p>
        </div>
      </div>
    )
  }

  // --- Main render ---

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('nav.notifications')}</div>
          <h1>{t('notifications.title')}</h1>
          <p>{t('notifications.subtitle')}</p>
        </div>
        <div className="heading-actions">
          <button className="button button-secondary" onClick={() => load()} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} />
            {loading ? t('common.loading') : t('common.refresh')}
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="sales-toolbar">
        <div className="sales-toolbar-row">
          <div className="sales-field">
            <span className="sales-field-label">{t('notifications.filter.status')}</span>
            <div className="sales-select">
              <select
                aria-label={t('notifications.filter.status')}
                value={readFilter}
                onChange={(e) => {
                  setReadFilter(e.target.value)
                  setPage(1)
                }}
              >
                {READ_FILTER_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {t(o.labelKey)}
                  </option>
                ))}
              </select>
              <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
            </div>
          </div>

          <div className="sales-field">
            <span className="sales-field-label">{t('notifications.search')}</span>
            <input
              type="text"
              aria-label={t('notifications.search')}
              placeholder={t('notifications.searchPlaceholder')}
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setPage(1)
              }}
              style={{ width: '100%' }}
            />
          </div>

          <div className="sales-field">
            <span className="sales-field-label">{t('common.date')}</span>
            <div className="sales-date-range">
              <input
                type="date"
                aria-label={t('notifications.filter.from')}
                value={from}
                onChange={(e) => {
                  setFrom(e.target.value)
                  setPage(1)
                }}
              />
              <span className="sales-date-dash">–</span>
              <input
                type="date"
                aria-label={t('notifications.filter.to')}
                value={to}
                onChange={(e) => {
                  setTo(e.target.value)
                  setPage(1)
                }}
              />
            </div>
          </div>

          <div className="sales-field">
            <span className="sales-field-label">{t('notifications.sort')}</span>
            <div className="sales-select">
              <select
                aria-label={t('notifications.sort')}
                value={sort}
                onChange={(e) => {
                  setSort(e.target.value)
                  setPage(1)
                }}
              >
                {SORT_OPTIONS.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
              <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
            </div>
          </div>
        </div>

        <div className="sales-toolbar-actions">
          {activeFilterCount > 0 && (
            <button className="button button-secondary" onClick={handleClearFilters}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                × {t('common.clearFilters')}
              </span>
            </button>
          )}
        </div>
      </div>

      {/* Mark all read button */}
      {canMarkRead && hasUnread && (
        <div className="heading-actions" style={{ marginBottom: 12 }}>
          <button
            className="button button-secondary"
            onClick={handleMarkAllRead}
            disabled={loading}
          >
            {t('notifications.markAllRead')}
          </button>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 46 }} />
          ))}
        </div>
      ) : (
        <>
          {!notifications || notifications.length === 0 ? (
            <div className="empty-workspace">
              <div className="empty-icon">
                <Bell size={26} />
              </div>
              <strong>
                {activeFilterCount > 0
                  ? t('notifications.noMatch')
                  : t('notifications.empty')}
              </strong>
              <p>
                {activeFilterCount > 0
                  ? t('notifications.noMatchDesc')
                  : t('notifications.noNotifications')}
              </p>
            </div>
          ) : (
            <section className="sales-card">
              <div className="sales-table-wrap">
                <table className="sales-table">
                  <thead>
                    <tr>
                      <th>{t('notifications.col.date')}</th>
                      <th>{t('notifications.col.type')}</th>
                      <th>{t('notifications.col.message')}</th>
                      <th>{t('notifications.col.related')}</th>
                      <th>{t('notifications.col.status')}</th>
                      <th>{t('common.actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {notifications.map((n) => (
                      <tr
                        key={n.id}
                        className={n.is_read ? '' : 'unread-row'}
                      >
                        <td>{fmtDate(n.created_at, locale)}</td>
                        <td>
                          <span
                            className={`status-badge status-badge--${n.type}`}
                            style={{
                              backgroundColor:
                                n.type === 'out_of_stock' || n.type === 'below_cost_sale'
                                  ? '#fee2e2'
                                  : n.type === 'system'
                                  ? '#e0e7ff'
                                  : '#dcfce7',
                            }}
                          >
                            {t(TYPE_LABELS[n.type] ?? `notifications.type.${n.type}`)}
                          </span>
                        </td>
                        <td>{n.message || '—'}</td>
                        <td>{relatedEntityLabel(n, t)}</td>
                        <td>
                          {n.is_read
                            ? t('notifications.status.read')
                            : t('notifications.status.unread')}
                        </td>
                        <td>
                          {!n.is_read && canMarkRead && (
                            <button
                              type="button"
                              className="button button-secondary"
                              style={{ fontSize: 12, padding: '2px 8px' }}
                              onClick={() => handleMarkRead(n)}
                            >
                              {t('notifications.markRead')}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="sales-pagination">
                <span>
                  {t('common.showing', {
                    start:
                      Math.min(
                        (actualPagination.page - 1) * actualPagination.per_page + 1,
                        actualPagination.total,
                      ) || 0,
                    end: Math.min(
                      actualPagination.page * actualPagination.per_page,
                      actualPagination.total,
                    ),
                    count: actualPagination.total,
                  })}
                </span>
                <div className="sales-pagination-nav">
                  <label style={{ display: 'flex', alignItems: 'center' }}>
                    <select
                      className="sales-perpage"
                      aria-label={t('common.perPage')}
                      value={String(perPage)}
                      onChange={(e) => {
                        setPerPage(Number(e.target.value))
                        setPage(1)
                      }}
                    >
                      {PER_PAGE_OPTIONS.map((n) => (
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
                    disabled={actualPagination.page <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <span style={{ fontWeight: 600, color: '#475569' }}>
                    {actualPagination.page} / {actualPagination.total_pages || 1}
                  </span>
                  <button
                    type="button"
                    className="sales-pager"
                    aria-label="Next page"
                    disabled={actualPagination.page >= actualPagination.total_pages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    <ChevronRight size={16} />
                  </button>
                </div>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  )
}
