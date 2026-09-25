'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  FileText,
  RefreshCw,
  Search,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'

// ---------------------------------------------------------------------------
// Types — match openapi.yaml AuditEntry + Pagination envelope
// ---------------------------------------------------------------------------

interface AuditEntry {
  id: number
  event_time: string
  user_id: number | null
  action: string
  entity_type: string
  entity_id: number | null
  old_values: Record<string, unknown> | null
  new_values: Record<string, unknown> | null
  reason: string | null
  ip_address: string | null
}

interface Pagination {
  page: number
  per_page: number
  total: number
  total_pages: number
}

interface AuditResponse {
  data: AuditEntry[]
  pagination: Pagination
  links?: {
    self?: string | null
    next?: string | null
    prev?: string | null
  }
}

const DEFAULT_PAGINATION: Pagination = {
  page: 1,
  per_page: 50,
  total: 0,
  total_pages: 0,
}

const PER_PAGE_OPTIONS = [10, 25, 50, 100]

const ENTITY_TYPE_OPTIONS = [
  'sale', 'sale_line', 'sale_payment', 'sales_return',
  'purchase', 'purchase_line', 'purchase_payment', 'purchase_return',
  'purchase_shipping', 'product', 'category', 'unit', 'contact',
  'payment_method', 'cost_type', 'financial_category', 'user', 'role',
  'manual_finance_entry', 'cash_movement', 'stock_movement',
  'production_run', 'refund', 'supplier_repayment', 'system_settings',
  'capability_override',
]

const ACTION_OPTIONS = [
  'create', 'post', 'cancel', 'complete', 'return', 'adjust', 'movement',
  'price_override', 'payment', 'refund', 'permission_grant',
  'permission_revoke', 'settings_change', 'deactivate', 'update',
  'finalise', 'arrival', 'override',
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtDate(iso: string, locale: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// ---------------------------------------------------------------------------
// Fetch
// ---------------------------------------------------------------------------

async function fetchAudit(params: Record<string, string>): Promise<AuditResponse> {
  return api.get<AuditResponse>('/audit', { params })
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function AuditPage() {
  const session = useSession()
  const { t, language } = useLanguage()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<AuditResponse | null>(null)
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)

  // Capability check
  const hasCap = session.capabilities?.includes('audit.view') ?? false

  const [entityType, setEntityType] = useState('')
  const [action, setAction] = useState('')
  const [userId, setUserId] = useState('')
  const [entityId, setEntityId] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')

  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(50)
  const [sort, setSort] = useState('-event_time')

  const locale = language === 'id' ? 'id-ID' : 'en-US'

  // Debounce search
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(searchInput)
      setPage(1)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  const buildParams = useCallback((): Record<string, string> => {
    const params: Record<string, string> = {
      page: String(page),
      per_page: String(perPage),
    }
    if (sort) params.sort = sort
    if (entityType) params['filter[entity_type]'] = entityType
    if (action) params['filter[action]'] = action
    if (userId) params['filter[user_id]'] = userId
    if (entityId) params['filter[entity_id]'] = entityId
    if (from) params.from = new Date(`${from}T00:00:00`).toISOString()
    if (to) params.to = new Date(`${to}T23:59:59`).toISOString()
    if (q.trim()) params.q = q.trim()
    return params
  }, [page, perPage, sort, entityType, action, userId, entityId, from, to, q])

  const load = useCallback(async () => {
    if (!hasCap) {
      setError('restricted')
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const params = buildParams()
      const res = await fetchAudit(params)
      setData(res)
      setPagination(res.pagination ?? DEFAULT_PAGINATION)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit entries')
      setData(null)
      setPagination(DEFAULT_PAGINATION)
    } finally {
      setLoading(false)
    }
  }, [buildParams, hasCap])

  useEffect(() => {
    load()
  }, [load])

  const handleClearFilters = () => {
    setEntityType('')
    setAction('')
    setUserId('')
    setEntityId('')
    setFrom('')
    setTo('')
    setSearchInput('')
    setQ('')
    setPage(1)
    setSort('-event_time')
  }

  const activeFilterCount = useMemo(() => {
    let count = 0
    if (entityType) count += 1
    if (action) count += 1
    if (userId) count += 1
    if (entityId) count += 1
    if (from || to) count += 1
    if (q) count += 1
    return count
  }, [entityType, action, userId, entityId, from, to, q])

  const entries = data?.data ?? []

  if (error === 'restricted') {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <h1>{t('nav.audit')}</h1>
            <p>{t('audit.subtitle')}</p>
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

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">{t('audit.eyebrow')}</div>
            <h1>{t('audit.title')}</h1>
            <p>{t('audit.subtitle')}</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={28} />
          </div>
          <strong>{t('audit.failedToLoad')}</strong>
          <p style={{ maxWidth: 440, fontSize: 13 }}>{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('audit.eyebrow')}</div>
          <h1>{t('audit.title')}</h1>
          <p>{t('audit.subtitle')}</p>
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
            <span className="sales-field-label">{t('audit.entityType')}</span>
            <div className="sales-select">
              <select
                aria-label={t('audit.entityType')}
                value={entityType}
                onChange={(e) => { setEntityType(e.target.value); setPage(1) }}
              >
                <option value="">{t('audit.allEntityTypes')}</option>
                {ENTITY_TYPE_OPTIONS.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
              <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
            </div>
          </div>

          <div className="sales-field">
            <span className="sales-field-label">{t('audit.action')}</span>
            <div className="sales-select">
              <select
                aria-label={t('audit.action')}
                value={action}
                onChange={(e) => { setAction(e.target.value); setPage(1) }}
              >
                <option value="">{t('audit.allActions')}</option>
                {ACTION_OPTIONS.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>
              <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
            </div>
          </div>

          <div className="sales-field" style={{ maxWidth: 120 }}>
            <span className="sales-field-label">{t('audit.userId')}</span>
            <input
              type="number"
              min="1"
              placeholder="ID"
              value={userId}
              onChange={(e) => { setUserId(e.target.value); setPage(1) }}
              style={{ width: '100%' }}
            />
          </div>

          <div className="sales-field" style={{ maxWidth: 120 }}>
            <span className="sales-field-label">{t('audit.entityId')}</span>
            <input
              type="number"
              min="1"
              placeholder="ID"
              value={entityId}
              onChange={(e) => { setEntityId(e.target.value); setPage(1) }}
              style={{ width: '100%' }}
            />
          </div>

          <div className="sales-field">
            <span className="sales-field-label">{t('common.date')}</span>
            <div className="sales-date-range">
              <input
                type="date"
                aria-label="From date"
                value={from}
                onChange={(e) => { setFrom(e.target.value); setPage(1) }}
              />
              <span className="sales-date-dash">–</span>
              <input
                type="date"
                aria-label="To date"
                value={to}
                onChange={(e) => { setTo(e.target.value); setPage(1) }}
              />
            </div>
          </div>

          <div className="sales-field sales-field-grow">
            <span className="sales-field-label">Search</span>
            <div className="sales-search">
              <Search size={15} />
              <input
                aria-label="Search audit"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={t('reports.searchPlaceholder')}
              />
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

      {/* Content */}
      {loading ? (
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 46 }} />
          ))}
        </div>
      ) : (
        <>
          {!entries || entries.length === 0 ? (
            <div className="empty-workspace">
              <div className="empty-icon">
                <FileText size={26} />
              </div>
              <strong>{t('audit.noEntries')}</strong>
              <p>
                {activeFilterCount > 0
                  ? t('audit.noEntriesMatch')
                  : t('audit.entriesAppearHere')}
              </p>
            </div>
          ) : (
            <section className="sales-card">
              <div className="sales-table-wrap">
                <table className="sales-table">
                  <thead>
                    <tr>
                      <th>{t('audit.eventTime')}</th>
                      <th>{t('audit.action')}</th>
                      <th>{t('audit.entityType')}</th>
                      <th>{t('audit.entityId')}</th>
                      <th>{t('audit.userId')}</th>
                      <th>{t('audit.reason')}</th>
                      <th>{t('audit.ipAddress')}</th>
                      <th>{t('common.actions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map((e) => (
                      <tr key={e.id}>
                        <td>{fmtDate(e.event_time, locale)}</td>
                        <td>{e.action}</td>
                        <td>{e.entity_type}</td>
                        <td>{e.entity_id ?? '—'}</td>
                        <td>{e.user_id ?? '—'}</td>
                        <td>{e.reason ?? '—'}</td>
                        <td>{e.ip_address ?? '—'}</td>
                        <td>
                          <button
                            type="button"
                            className="button button-secondary"
                            style={{ fontSize: 12, padding: '2px 8px' }}
                            onClick={() => {
                              const detail = {
                                id: e.id,
                                event_time: e.event_time,
                                user_id: e.user_id,
                                action: e.action,
                                entity_type: e.entity_type,
                                entity_id: e.entity_id,
                                old_values: e.old_values,
                                new_values: e.new_values,
                                reason: e.reason,
                                ip_address: e.ip_address,
                              }
                              alert(JSON.stringify(detail, null, 2))
                            }}
                          >
                            {t('common.view')}
                          </button>
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
                    start: Math.min((pagination.page - 1) * pagination.per_page + 1, pagination.total),
                    end: Math.min(pagination.page * pagination.per_page, pagination.total),
                    count: pagination.total,
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
                    disabled={pagination.page <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <span style={{ fontWeight: 600, color: '#475569' }}>
                    {pagination.page} / {pagination.total_pages || 1}
                  </span>
                  <button
                    type="button"
                    className="sales-pager"
                    aria-label="Next page"
                    disabled={pagination.page >= pagination.total_pages}
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
