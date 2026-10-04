'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  BarChart3,
  CalendarIcon,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import {
  COMPARE_OPTIONS,
  type CompareValue,
  type ExportJob,
  PERIOD_OPTIONS,
  type PeriodValue,
  type PnLReportResponse,
  type ProductionReportResponse,
  type ReportKind,
  type ReportOption,
  REPORT_OPTIONS,
  type SalesReportResponse,
  type InventoryReportResponse,
  type GenericPagedReportResponse,
  type Pagination,
  type PeriodComparison,
} from '@/lib/report-types'
import { formatDate, formatIDR, formatInt, formatPct } from '@/lib/format'
import { API_BASE_URL } from '@/lib/constants'

function SalesReportVisuals({
  revenue,
  cogs,
  grossProfit,
  comparison,
  formatMoney,
}: {
  revenue: number
  cogs: number
  grossProfit: number
  comparison: PeriodComparison | null
  formatMoney: (value: number) => string
}) {
  const { t } = useLanguage()
  const total = Math.max(cogs + grossProfit, 0)
  const cogsShare = total > 0 ? (Math.min(Math.max(cogs, 0), total) / total) * 100 : 0
  const bars = [
    { key: 'revenue', label: t('reports.revenue'), value: revenue, color: 'report-bar-revenue' },
    { key: 'cogs', label: t('reports.cogs'), value: cogs, color: 'report-bar-cogs' },
    { key: 'gross_profit', label: t('reports.grossProfit'), value: grossProfit, color: 'report-bar-profit' },
  ]
  return (
    <section className="report-visuals">
      <article className="report-visual-card">
        <div className="report-visual-heading">
          <h2>Revenue composition</h2>
          <p>Cost of goods sold and gross profit</p>
        </div>
        <div className="report-donut-layout">
          <div
            className="report-donut"
            role="img"
            aria-label={`Revenue composition: ${formatMoney(cogs)} cost and ${formatMoney(grossProfit)} gross profit`}
            style={{ background: `conic-gradient(#2563eb 0 ${cogsShare}%, #10b981 ${cogsShare}% 100%)` }}
          >
            <div><strong>{formatMoney(revenue)}</strong><span>{t('reports.revenue')}</span></div>
          </div>
          <div className="report-visual-legend">
            <span><i className="report-bar-cogs" />{t('reports.cogs')} <b>{formatMoney(cogs)}</b></span>
            <span><i className="report-bar-profit" />{t('reports.grossProfit')} <b>{formatMoney(grossProfit)}</b></span>
          </div>
        </div>
      </article>
      <article className="report-visual-card">
        <div className="report-visual-heading">
          <h2>Sales performance</h2>
          <p>{comparison ? 'Current period and previous period' : 'Revenue, costs, and profit'}</p>
        </div>
        <div className="report-bars">
          {bars.map((bar) => {
            const previous = comparison ? Math.max(0, bar.value - (comparison.delta[bar.key] ?? 0)) : null
            const maxValue = Math.max(revenue, previous ?? 0, 1)
            return (
              <div className="report-bar-row" key={bar.key}>
                <div className="report-bar-label"><span>{bar.label}</span><b>{formatMoney(bar.value)}</b></div>
                <div className="report-bar-track">
                  <i className={bar.color} style={{ width: `${Math.max(1, (bar.value / maxValue) * 100)}%` }} />
                  {previous !== null && <i className="report-bar-old" style={{ width: `${Math.max(1, (previous / maxValue) * 100)}%` }} />}
                </div>
                {previous !== null && <small>Previous: {formatMoney(previous)}</small>}
              </div>
            )
          })}
        </div>
      </article>
    </section>
  )
}

const REPORT_CHART_FIELDS: Partial<Record<ReportKind, { label: string[]; value: string[]; title: string; format?: 'money' | 'count' | 'quantity' }>> = {
  purchases: { label: ['lifecycle_status', 'purchase_date'], value: [], title: 'Purchases by status', format: 'count' },
  'inventory-movements': { label: ['trigger'], value: ['quantity'], title: 'Stock activity by source', format: 'quantity' },
  'sales-returns': { label: ['reason', 'lifecycle_status'], value: ['total_selling_price_returned'], title: 'Returned sales value by reason' },
  'purchase-returns': { label: ['reason', 'lifecycle_status'], value: ['total_value_returned'], title: 'Purchase return value by reason' },
  production: { label: ['output_product_id'], value: ['output_quantity'], title: 'Production output by product', format: 'quantity' },
  'manual-income': { label: ['category_name', 'category'], value: ['amount'], title: 'Income by category' },
  'manual-expense': { label: ['category_name', 'category'], value: ['amount'], title: 'Expenses by category' },
  refunds: { label: ['reason'], value: ['amount'], title: 'Refunds by reason' },
  'supplier-repayments': { label: ['purchase_id', 'reason'], value: ['received_amount'], title: 'Cash received by supplier repayment' },
  'cash-flow': { label: ['direction', 'trigger'], value: ['amount'], title: 'Cash flow by direction' },
  receivables: { label: ['customer_id', 'sale_id'], value: ['open_balance'], title: 'Open receivable by customer sale' },
  payables: { label: ['supplier_id', 'purchase_id'], value: ['open_balance'], title: 'Open payable by supplier purchase' },
  'supplier-receivables': { label: ['supplier_id', 'purchase_id'], value: ['open_balance'], title: 'Open supplier receivable' },
  'customer-refund-liabilities': { label: ['customer_id', 'sale_id'], value: ['open_balance'], title: 'Open refund liability' },
}

function asFinite(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function ReportCategoryChart({ kind, rows, language }: { kind: ReportKind; rows: Record<string, unknown>[]; language: string }) {
  const spec = REPORT_CHART_FIELDS[kind]
  if (!spec || rows.length === 0) return null
  const groups = new Map<string, number>()
  let hasNumericValues = false
  for (const row of rows) {
    const key = spec.label.map((field) => row[field]).find((value) => value !== undefined && value !== null && String(value).trim() !== '')
    if (key === undefined) continue
    const raw = spec.value.map((field) => asFinite(row[field])).find((value) => value !== null)
    if (raw !== undefined && raw !== null) hasNumericValues = true
    const value = raw ?? 1
    groups.set(String(key), (groups.get(String(key)) ?? 0) + value)
  }
  const values = Array.from(groups, ([label, value]) => ({ label, value })).sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, 7)
  if (values.length === 0) return null
  const max = Math.max(...values.map((entry) => Math.abs(entry.value)), 1)
  const valueFormat = spec.format ?? (!hasNumericValues ? 'count' : 'money')
  const formatValue = (value: number) => valueFormat === 'count'
    ? formatInt(value)
    : valueFormat === 'quantity'
      ? new Intl.NumberFormat(language === 'id' ? 'id-ID' : 'en-US', { maximumFractionDigits: 3 }).format(value)
      : formatIDR(value, language)
  return (
    <section className="report-generic-visual" aria-label={spec.title}>
      <div className="report-generic-visual-heading"><div><h2>{spec.title}</h2><p>Grouped from records returned for the selected filters</p></div><span>{rows.length} records</span></div>
      <div className="report-category-bars">
        {values.map((entry) => (
          <div className="report-category-row" key={entry.label}>
            <div className="report-category-label"><span title={entry.label}>{entry.label.replace(/_/g, ' ')}</span><strong>{formatValue(entry.value)}</strong></div>
            <div className="report-category-track"><i style={{ width: `${Math.max(2, Math.abs(entry.value) / max * 100)}%` }} /></div>
          </div>
        ))}
      </div>
    </section>
  )
}

function AggregateReportVisual({ kind, data, language }: { kind: ReportKind; data: Record<string, unknown>; language: string }) {
  let entries: { label: string; value: number; color?: string }[] = []
  let title = ''
  let subtitle = ''
  if (kind === 'p-and-l') {
    title = 'Profit and loss composition'
    subtitle = 'Compare revenue, cost, and profit for the selected period'
    const keys = [
      ['revenue', 'Revenue', '#3b82f6'], ['cogs', 'Cost of goods sold', '#8aa5c8'],
      ['other_income', 'Other income', '#16a777'], ['operating_expenses', 'Operating expenses', '#ed9a24'], ['net_profit', 'Net profit', '#1c55c8'],
    ] as const
    entries = keys.map(([key, label, color]) => ({ label, value: asFinite(data[key]) ?? 0, color }))
  } else if (kind === 'inventory') {
    title = 'Stock health'
    subtitle = 'Products needing attention compared with the active catalogue'
    entries = [
      { label: 'Products tracked', value: asFinite(data.products_count) ?? 0, color: '#3b82f6' },
      { label: 'Low stock', value: asFinite(data.low_stock_count) ?? 0, color: '#ed9a24' },
      { label: 'Out of stock', value: asFinite(data.out_of_stock_count) ?? 0, color: '#dc5b58' },
    ]
  }
  if (!entries.length) return null
  const max = Math.max(...entries.map((entry) => Math.abs(entry.value)), 1)
  const counts = kind === 'inventory'
  return (
    <section className="report-generic-visual" aria-label={title}>
      <div className="report-generic-visual-heading"><div><h2>{title}</h2><p>{subtitle}</p></div></div>
      <div className="report-category-bars">
        {entries.map((entry) => <div className="report-category-row" key={entry.label}>
          <div className="report-category-label"><span>{entry.label}</span><strong>{counts ? formatInt(entry.value) : formatIDR(entry.value, language)}</strong></div>
          <div className="report-category-track"><i style={{ width: `${Math.max(2, Math.abs(entry.value) / max * 100)}%`, background: entry.color }} /></div>
        </div>)}
      </div>
    </section>
  )
}

// -----------------------------------------------------------------------------
// Reports workspace — one reusable page covering every backend /reports/* endpoint.
//
// Contract:
//   - All reports share period (today/this_week/this_month/this_year/custom) and
//     compare_to (none/today/this_week/this_month/this_year) controls.
//   - /reports/purchases and /reports/inventory-movements additionally expose
//     server-side pagination, sorting, search, and their documented filter params.
//   - /reports/inventory is a point-in-time snapshot (period ignored per contract).
//   - /reports/p-and-l requires finance.view_profit.
//   - Receivables/payables/supplier-receivables/customer-refund-liabilities require
//     finance.view_payables_receivables.
//   - Export uses POST /exports with the current filter set.
// -----------------------------------------------------------------------------

const DEFAULT_PAGINATION: Pagination = {
  page: 1,
  per_page: 50,
  total: 0,
  total_pages: 0,
}

const PER_PAGE_OPTIONS = [10, 25, 50, 100]

/** Determine if a report endpoint supports server-side pagination on the wire. */
function hasPagination(kind: ReportKind): boolean {
  return kind === 'purchases' || kind === 'inventory-movements'
}

/** Determine if a report endpoint supports server-side sort on the wire. */
function hasSort(kind: ReportKind): boolean {
  return kind === 'purchases' || kind === 'inventory-movements'
}

/** Determine if a report endpoint supports server-side search on the wire. */
function hasSearch(kind: ReportKind): boolean {
  return kind === 'purchases' || kind === 'inventory-movements'
}

/** Determine if a report endpoint supports comparison. */
function supportsComparison(kind: ReportKind): boolean {
  // Inventory snapshot explicitly has no comparison field per backend contract.
  return kind !== 'inventory'
}

/** Determine if a report response is a paged list. */
function isPagedResponse(body: unknown): body is GenericPagedReportResponse {
  return typeof body === 'object' && body !== null && 'pagination' in body
}

/** Determine if a report response is an aggregate with comparison. */
function isAggregateResponse(body: unknown): body is { data: Record<string, unknown>; comparison?: PeriodComparison | null } {
  return typeof body === 'object' && body !== null && 'data' in body && !('pagination' in body)
}

export default function ReportsPage() {
  const user = useSession()
  const userCaps = user.capabilities
  const { t, language } = useLanguage()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<unknown>(null)
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)

  // Report selection
  const [kind, setKind] = useState<ReportKind>('sales')

  // Period / comparison
  const [period, setPeriod] = useState<PeriodValue>('this_month')
  const [compareTo, setCompareTo] = useState<CompareValue>('none')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  // Paged-report controls
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(50)
  const [sort, setSort] = useState('id')
  const [searchInput, setSearchInput] = useState('')
  const [q, setQ] = useState('')

  // Export dialog
  const [exportOpen, setExportOpen] = useState(false)
  const [exportFormat, setExportFormat] = useState<'pdf' | 'xlsx'>('pdf')
  const [exportJob, setExportJob] = useState<ExportJob | null>(null)
  const [exportSubmitting, setExportSubmitting] = useState(false)
  const [exportDownloading, setExportDownloading] = useState(false)

  // Availability
  const availableReports = useMemo(
    () =>
      REPORT_OPTIONS.filter((r) => {
        if (r.extraCapability === 'finance.view_profit') {
          return userCaps.includes('finance.view_profit')
        }
        if (r.extraCapability === 'finance.view_payables_receivables') {
          return userCaps.includes('finance.view_payables_receivables')
        }
        return userCaps.includes('report.view')
      }),
    [userCaps]
  )

  const canExport = userCaps.includes('export.data')

  // If current report becomes unavailable (capability removed), fall back to the first available.
  useEffect(() => {
    if (availableReports.length === 0) return
    if (!availableReports.some((r) => r.kind === kind)) {
      setKind(availableReports[0].kind)
    }
  }, [availableReports, kind])

  // Debounce search input.
  useEffect(() => {
    const t = window.setTimeout(() => {
      setQ(searchInput)
      setPage(1)
    }, 300)
    return () => window.clearTimeout(t)
  }, [searchInput])

  const currentOption = useMemo(
    () => REPORT_OPTIONS.find((r) => r.kind === kind) as ReportOption,
    [kind]
  )

  const buildParams = useCallback((): Record<string, string> => {
    const params: Record<string, string> = {}

    // Period resolution
    if (period === 'custom') {
      if (fromDate) params.from = new Date(`${fromDate}T00:00:00`).toISOString()
      if (toDate) params.to = new Date(`${toDate}T23:59:59`).toISOString()
    } else {
      params.period = period
    }

    if (supportsComparison(kind) && compareTo !== 'none') {
      params.compare_to = compareTo
    }

    if (hasPagination(kind)) {
      params.page = String(page)
      params.per_page = String(perPage)
      if (sort) params.sort = sort
      if (hasSearch(kind) && q.trim()) params.q = q.trim()
    }

    return params
  }, [kind, period, compareTo, fromDate, toDate, page, perPage, sort, q])

  const fetchReport = useCallback(async () => {
    if (!currentOption) return
    setLoading(true)
    setError(null)
    try {
      const params = buildParams()
      const res = await api.get<unknown>(`/reports/${kind}`, { params })
      setData(res)
      if (isPagedResponse(res)) {
        setPagination(res.pagination ?? DEFAULT_PAGINATION)
      } else {
        setPagination(DEFAULT_PAGINATION)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report')
      setData(null)
      setPagination(DEFAULT_PAGINATION)
    } finally {
      setLoading(false)
    }
  }, [kind, currentOption, buildParams])

  useEffect(() => {
    if (availableReports.length === 0) {
      setLoading(false)
      setError('Missing capability: report.view')
      return
    }
    fetchReport()
  }, [fetchReport, availableReports.length])

  // Poll export job.
  useEffect(() => {
    if (!exportOpen || !exportJob) return
    if (exportJob.status === 'complete' || exportJob.status === 'failed') return
    const MAX_POLLS = 60
    let attempts = 0
    const id = window.setInterval(async () => {
      attempts += 1
      try {
        const next = await api.get<ExportJob>(`/exports/${exportJob.export_id}`)
        setExportJob(next)
        if (next.status === 'complete' || next.status === 'failed') {
          window.clearInterval(id)
        }
      } catch {
        if (attempts >= MAX_POLLS) {
          window.clearInterval(id)
          setExportJob((prev) =>
            prev ? { ...prev, status: 'failed', error: 'Export timed out' } : prev
          )
        }
      }
    }, 1000)
    return () => window.clearInterval(id)
  }, [exportOpen, exportJob])

  const handleExport = async () => {
    if (!canExport) return
    setExportSubmitting(true)
    setExportJob(null)
    try {
      const filters: Record<string, unknown> = {}
      const params = buildParams()
      Object.entries(params).forEach(([k, v]) => {
        if (k === 'from' || k === 'to') {
          // Backend exports expect from_iso/to_iso or period alias.
          filters[k === 'from' ? 'from_iso' : 'to_iso'] = v
        } else {
          filters[k] = v
        }
      })
      const created = await api.post<ExportJob>(
        '/exports',
        { report: kind, format: exportFormat, filters },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } }
      )
      setExportJob(created)
    } catch (err) {
      setExportJob({
        export_id: '',
        status: 'failed',
        download_url: null,
        expires_at: null,
        error: err instanceof Error ? err.message : 'Export failed',
        created_at: new Date().toISOString(),
      })
    } finally {
      setExportSubmitting(false)
    }
  }

  const handleDownload = async () => {
    if (!exportJob || exportJob.status !== 'complete' || !exportJob.download_url) return
    setExportDownloading(true)
    try {
      const token = localStorage.getItem('access_token')
      const url = exportJob.download_url.startsWith('http')
        ? exportJob.download_url
        : `${API_BASE_URL}${exportJob.download_url}`
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        credentials: 'include',
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const filename = `${kind}-${exportJob.export_id}.${exportFormat}`
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(a.href)
    } catch (err) {
      setExportJob((prev) =>
        prev
          ? { ...prev, status: 'failed', error: err instanceof Error ? err.message : 'Download failed' }
          : prev
      )
    } finally {
      setExportDownloading(false)
    }
  }

  const handleClearFilters = () => {
    setPeriod('this_month')
    setCompareTo('none')
    setFromDate('')
    setToDate('')
    setPage(1)
    setSort('id')
    setSearchInput('')
    setQ('')
  }

  const activeFilterCount = useMemo(() => {
    let count = 0
    if (period === 'custom' ? fromDate || toDate : period !== 'this_month') count += 1
    if (compareTo !== 'none') count += 1
    if (q) count += 1
    if (sort !== 'id') count += 1
    return count
  }, [period, fromDate, toDate, compareTo, q, sort])

  const customInvalid = period === 'custom' && (!fromDate || !toDate || fromDate > toDate)

  // UI helpers
  const comparison = useMemo(() => {
    if (!data || typeof data !== 'object' || !('comparison' in data)) return null
    return (data as { comparison?: PeriodComparison | null }).comparison ?? null
  }, [data])

  const renderAggregate = () => {
    if (!data || !isAggregateResponse(data)) return null
    const d = data.data
    const metrics: { label: string; value: string }[] = []
    if (kind === 'sales') {
      const sd = d as unknown as SalesReportResponse['data']
      metrics.push(
        { label: t('reports.revenue'), value: formatIDR(sd.revenue, language) },
        { label: t('reports.cogs'), value: formatIDR(sd.cogs, language) },
        { label: t('reports.grossProfit'), value: formatIDR(sd.gross_profit, language) },
        { label: t('reports.salesCount'), value: formatInt(sd.sales_count) },
        { label: t('reports.averageTicket'), value: formatIDR(sd.average_ticket, language) }
      )
    } else if (kind === 'p-and-l') {
      const pd = d as unknown as PnLReportResponse['data']
      metrics.push(
        { label: t('reports.revenue'), value: formatIDR(pd.revenue, language) },
        { label: t('reports.cogs'), value: formatIDR(pd.cogs, language) },
        { label: t('reports.grossProfit'), value: formatIDR(pd.gross_profit, language) },
        { label: t('reports.otherIncome'), value: formatIDR(pd.other_income, language) },
        { label: t('reports.operatingExpenses'), value: formatIDR(pd.operating_expenses, language) },
        { label: t('reports.netProfit'), value: formatIDR(pd.net_profit, language) }
      )
    } else if (kind === 'inventory') {
      const id = d as unknown as InventoryReportResponse['data']
      metrics.push(
        { label: t('reports.inventoryValue'), value: formatIDR(id.total_inventory_value, language) },
        { label: t('reports.products'), value: formatInt(id.products_count) },
        { label: t('reports.lowStock'), value: formatInt(id.low_stock_count) },
        { label: t('reports.outOfStock'), value: formatInt(id.out_of_stock_count) }
      )
    } else {
      // Fallback for any aggregate we didn't explicitly model.
      Object.entries(d).forEach(([k, v]) => {
        const display = typeof v === 'number' ? (k.includes('count') ? formatInt(v) : formatIDR(v)) : String(v)
        metrics.push({ label: humanLabel(k), value: display })
      })
    }
    return (
      <>
        <section className="metrics">
          {metrics.map((m) => (
            <article key={m.label} className="metric-card">
              <div className="metric-top">
                <span className="metric-label">{m.label}</span>
                <span className="metric-icon">
                  <BarChart3 size={17} />
                </span>
              </div>
              <div className="metric-value">{m.value}</div>
              <div className="metric-change">
                <span className="change-note">{t('reports.currentPeriod')}</span>
              </div>
            </article>
          ))}
        </section>
        {kind === 'sales' && (
          <SalesReportVisuals
            revenue={Number((d as unknown as SalesReportResponse['data']).revenue) || 0}
            cogs={Number((d as unknown as SalesReportResponse['data']).cogs) || 0}
            grossProfit={Number((d as unknown as SalesReportResponse['data']).gross_profit) || 0}
            comparison={comparison}
            formatMoney={(value) => formatIDR(value, language)}
          />
        )}
        {(kind === 'p-and-l' || kind === 'inventory') && (
          <AggregateReportVisual kind={kind} data={d} language={language} />
        )}
      </>
    )
  }

  const renderComparison = () => {
    if (!comparison) return null
    const keys = Object.keys(comparison.delta)
    if (keys.length === 0) return null
    return (
      <section className="metrics metrics-secondary" style={{ marginTop: -8 }}>
        {keys.map((k) => {
          const delta = comparison.delta[k]
          const deltaPct = comparison.delta_pct[k]
          const positive = delta >= 0
          return (
            <article key={k} className="metric-card">
              <div className="metric-top">
                <span className="metric-label">{t('reports.change', { label: humanLabel(k) })}</span>
                <span className="metric-icon">
                  <BarChart3 size={17} />
                </span>
              </div>
              <div className="metric-value">
                {deltaPct !== null && deltaPct !== undefined ? formatPct(deltaPct, true) : '—'}
              </div>
              <div className="metric-change">
                <span className={`change-note ${positive ? 'positive' : 'negative'}`}>
                  {positive ? '+' : ''}
                  {formatIDR(Math.abs(delta))}
                </span>
              </div>
            </article>
          )
        })}
      </section>
    )
  }

  const renderTable = () => {
    if (!data || !isPagedResponse(data)) return null
    const rows = data.data
    if (rows.length === 0) {
      return (
        <div className="empty-workspace">
          <div className="empty-icon">
            <FileText size={26} />
          </div>
          <strong>{t('reports.noRecordsFound')}</strong>
          <p>
            {activeFilterCount > 0
              ? t('reports.noRecordsMatchFilters')
              : t('reports.recordsAppearHere')}
          </p>
        </div>
      )
    }
    const columns = Object.keys(rows[0])
    return (
      <>
        <ReportCategoryChart kind={kind} rows={rows} language={language} />
        <div className="sales-table-wrap">
          <table className="sales-table">
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col}>{humanLabel(col)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr key={idx}>
                  {columns.map((col) => (
                    <td key={col} className={isNumeric(row[col]) ? 'sales-td-right sales-num' : ''}>
                      {formatCell(row[col], language)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {hasPagination(kind) && (
          <div className="sales-pagination">
            <span>
              Showing {Math.min((pagination.page - 1) * pagination.per_page + 1, pagination.total)}–
              {Math.min(pagination.page * pagination.per_page, pagination.total)} of {pagination.total}
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
        )}
      </>
    )
  }

  if (availableReports.length === 0) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <h1>{t('reports.title')}</h1>
            <p>Business reporting and exports</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={32} />
          </div>
          <strong>Access restricted</strong>
          <p>You don&apos;t have permission to view reports. Contact your administrator.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('reports.eyebrow')}</div>
          <h1>{t('reports.title')}</h1>
          <p>{t('reports.subtitle')}</p>
        </div>
        <div className="heading-actions">
          <Button variant="outline" onClick={fetchReport} disabled={loading}>
            <RefreshCw size={14} />
            Refresh
          </Button>
          {canExport && (
            <Button onClick={() => setExportOpen(true)}>
              <Download size={14} />
              {t('reports.export')}
            </Button>
          )}
        </div>
      </div>

      <div className="reports-toolbar">
        <div className="reports-toolbar-row">
          <div className="reports-toolbar-fields">
            <div className="sales-field">
              <span className="sales-field-label">{t('reports.report')}</span>
              <div className="sales-select">
                <select
                  aria-label="Select report"
                  value={kind}
                  onChange={(e) => {
                    setKind(e.target.value as ReportKind)
                    setPage(1)
                    setSort('id')
                    setSearchInput('')
                    setQ('')
                  }}
                >
                  {availableReports.map((r) => (
                    <option key={r.kind} value={r.kind}>
                      {t(r.labelKey) || r.label}
                    </option>
                  ))}
                </select>
                <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
              </div>
            </div>

            <div className="sales-field">
              <span className="sales-field-label">{t('reports.period')}</span>
              <div className="sales-select">
                <select
                  aria-label="Period"
                  value={period}
                  onChange={(e) => setPeriod(e.target.value as PeriodValue)}
                >
                  {PERIOD_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {t(o.labelKey) || o.label}
                    </option>
                  ))}
                </select>
                <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
              </div>
            </div>

            {period === 'custom' && (
              <div className="sales-field">
                <span className="sales-field-label">{t('reports.dateRange')}</span>
                <div className="sales-date-range">
                  <CalendarIcon />
                  <input
                    type="date"
                    aria-label="From date"
                    value={fromDate}
                    max={toDate || undefined}
                    onChange={(e) => setFromDate(e.target.value)}
                  />
                  <span className="sales-date-dash">–</span>
                  <input
                    type="date"
                    aria-label="To date"
                    value={toDate}
                    min={fromDate || undefined}
                    onChange={(e) => setToDate(e.target.value)}
                  />
                </div>
              </div>
            )}

            {supportsComparison(kind) && (
              <div className="sales-field">
                <span className="sales-field-label">{t('reports.compare')}</span>
                <div className="sales-select">
                  <select
                    aria-label="Compare to previous period"
                    value={compareTo}
                    onChange={(e) => setCompareTo(e.target.value as CompareValue)}
                  >
                    {COMPARE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
                </div>
              </div>
            )}

            {hasSearch(kind) && (
              <div className="sales-field sales-field-grow">
                <span className="sales-field-label">Search</span>
                <div className="sales-search">
                  <Search size={15} />
                  <input
                    aria-label="Search report"
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                    placeholder={t('reports.searchPlaceholder')}
                  />
                </div>
              </div>
            )}

            {hasSort(kind) && (
              <div className="sales-field">
                <span className="sales-field-label">{t('reports.sort')}</span>
                <div className="sales-select">
                  <select
                    aria-label="Sort by"
                    value={sort}
                    onChange={(e) => setSort(e.target.value)}
                  >
                    {kind === 'purchases' && (
                      <>
                        <option value="id">ID</option>
                        <option value="-id">ID (desc)</option>
                        <option value="purchase_date">Purchase date</option>
                        <option value="-purchase_date">Purchase date (desc)</option>
                        <option value="lifecycle_status">Lifecycle</option>
                        <option value="created_at">Created</option>
                        <option value="updated_at">Updated</option>
                      </>
                    )}
                    {kind === 'inventory-movements' && (
                      <>
                        <option value="id">ID</option>
                        <option value="-id">ID (desc)</option>
                        <option value="movement_date">Movement date</option>
                        <option value="-movement_date">Movement date (desc)</option>
                        <option value="product_id">Product</option>
                        <option value="created_at">Created</option>
                      </>
                    )}
                  </select>
                  <ChevronLeft size={14} style={{ transform: 'rotate(-90deg)' }} />
                </div>
              </div>
            )}
          </div>

          <div className="sales-toolbar-actions">
            {activeFilterCount > 0 && (
              <Button variant="outline" onClick={handleClearFilters}>
                <X size={14} />
                Clear
              </Button>
            )}
          </div>
        </div>
        {customInvalid && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#dc2626' }}>
            {t('reports.customRangeInvalid')}
          </div>
        )}
      </div>

      {loading ? (
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="skeleton" style={{ height: 46 }} />
          ))}
        </div>
      ) : error ? (
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={28} />
          </div>
          <strong>Failed to load report</strong>
          <p style={{ maxWidth: 440, fontSize: 13 }}>{error}</p>
          <Button variant="outline" onClick={fetchReport}>
            <RefreshCw size={14} />
            Try again
          </Button>
        </div>
      ) : (
        <section className="sales-card">
          {isAggregateResponse(data) && renderAggregate()}
          {renderComparison()}
          {isPagedResponse(data) && renderTable()}
        </section>
      )}

      {exportOpen && (
        <ExportDialog
          reportLabel={t(currentOption.labelKey) || currentOption.label}
          format={exportFormat}
          setFormat={setExportFormat}
          job={exportJob}
          submitting={exportSubmitting}
          downloading={exportDownloading}
          onSubmit={handleExport}
          onDownload={handleDownload}
          t={t}
          onClose={() => {
            if (exportSubmitting || exportDownloading) return
            setExportOpen(false)
            setExportJob(null)
          }}
        />
      )}
    </div>
  )
}

// -----------------------------------------------------------------------------
// Export dialog (minimal, inline to avoid new file)
// -----------------------------------------------------------------------------

function ExportDialog({
  reportLabel,
  format,
  setFormat,
  job,
  submitting,
  downloading,
  onSubmit,
  onDownload,
  onClose,
  t,
}: {
  reportLabel: string
  format: 'pdf' | 'xlsx'
  setFormat: (f: 'pdf' | 'xlsx') => void
  job: ExportJob | null
  submitting: boolean
  downloading: boolean
  onSubmit: () => void
  onDownload: () => void
  onClose: () => void
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(15,23,42,.35)',
        padding: 16,
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-label={`Export ${reportLabel}`}
        style={{
          width: '100%',
          maxWidth: 420,
          background: 'white',
          borderRadius: 14,
          border: '1px solid var(--border)',
          boxShadow: '0 20px 48px rgba(0,0,0,.12)',
          display: 'flex',
          flexDirection: 'column',
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Download size={18} color="var(--primary)" />
            <div>
              <div style={{ fontSize: 16, fontWeight: 700 }}>{t('reports.exportTitle', { report: reportLabel })}</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>{t('reports.exportSubtitle')}</div>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={submitting || downloading}
            aria-label="Close"
            style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, color: 'var(--muted)' }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {!job && (
            <>
              <div>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                  {t('reports.format')}
                </label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  {(['pdf', 'xlsx'] as const).map((f) => {
                    const selected = format === f
                    return (
                      <button
                        key={f}
                        onClick={() => setFormat(f)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 10,
                          padding: '10px 12px',
                          borderRadius: 8,
                          border: `1px solid ${selected ? 'var(--primary)' : 'var(--border)'}`,
                          background: selected ? '#eff6ff' : 'white',
                          cursor: 'pointer',
                        }}
                      >
                        <FileText size={16} color={selected ? 'var(--primary)' : 'var(--muted)'} />
                        <div style={{ textAlign: 'left' }}>
                          <div style={{ fontSize: 13, fontWeight: 600 }}>{f.toUpperCase()}</div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                {t('reports.exportUsesFilters')}
              </div>
            </>
          )}

          {job && (job.status === 'queued' || job.status === 'processing') && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: 16,
                background: '#f8fafc',
                borderRadius: 10,
                border: '1px solid var(--border)',
              }}
            >
              <Loader2 size={20} color="var(--primary)" className="spin" />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>
                  {job.status === 'queued' ? t('reports.queued') : t('reports.generating')}
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>{t('reports.job', { id: job.export_id })}</div>
              </div>
            </div>
          )}

          {job && job.status === 'complete' && (
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 12,
                padding: 16,
                background: '#ecfdf5',
                borderRadius: 10,
                border: '1px solid #a7f3d0',
              }}
            >
              <div style={{ color: '#059669' }}>✓</div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#065f46' }}>{t('reports.exportReady')}</div>
                <div style={{ fontSize: 11, color: '#047857' }}>{t('reports.job', { id: job.export_id })}</div>
              </div>
            </div>
          )}

          {job && job.status === 'failed' && (
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-start',
                gap: 12,
                padding: 16,
                background: '#fef2f2',
                borderRadius: 10,
                border: '1px solid #fecaca',
              }}
            >
              <X size={20} color="#dc2626" />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#991b1b' }}>{t('reports.exportFailed')}</div>
                <div style={{ fontSize: 12, color: '#b91c1c' }}>{job.error || t('reports.unknownError')}</div>
              </div>
            </div>
          )}
        </div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            padding: '12px 20px',
            borderTop: '1px solid var(--border)',
          }}
        >
          {!job && (
            <>
              <Button variant="outline" onClick={onClose} disabled={submitting}>
                Cancel
              </Button>
              <Button onClick={onSubmit} disabled={submitting}>
                {submitting ? (
                  <>
                    <Loader2 size={14} className="spin" /> {t('reports.submitting')}
                  </>
                ) : (
                  <>{t('reports.generate', { format: format.toUpperCase() })}</>
                )}
              </Button>
            </>
          )}
          {job?.status === 'complete' && (
            <>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
              <Button onClick={onDownload} disabled={downloading}>
                {downloading ? (
                  <>
                    <Loader2 size={14} className="spin" /> {t('reports.downloading')}
                  </>
                ) : (
                  <>
                    <Download size={14} /> {t('reports.download')}
                  </>
                )}
              </Button>
            </>
          )}
          {job?.status === 'failed' && (
            <>
              <Button
                variant="outline"
                onClick={() => {
                  // Reset job to allow retry
                  ;(job as ExportJob).status = 'queued'
                  onSubmit()
                }}
              >
                {t('reports.tryAgain')}
              </Button>
              <Button onClick={onClose}>Close</Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// -----------------------------------------------------------------------------
// Presentation utilities
// -----------------------------------------------------------------------------

function humanLabel(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b(id|no)\b/gi, 'ID')
    .replace(/\b(cogs|crl|ap|ar)\b/gi, (m) => m.toUpperCase())
    .replace(/^\w/, (c) => c.toUpperCase())
}

function isNumeric(value: unknown): boolean {
  if (value === null || value === undefined) return false
  if (typeof value === 'number') return Number.isFinite(value)
  if (typeof value === 'string') {
    const n = Number(value)
    return value !== '' && Number.isFinite(n)
  }
  return false
}

function isDateLike(value: unknown): boolean {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(value)
}

function formatCell(value: unknown, locale = 'id-ID'): string {
  if (value === null || value === undefined) return '—'
  if (isDateLike(value)) {
    const d = new Date(value as string)
    return Number.isNaN(d.getTime()) ? String(value) : formatDate(d, locale)
  }
  if (isNumeric(value)) {
    const n = typeof value === 'number' ? value : Number(value)
    return formatIDR(n, locale)
  }
  return String(value)
}
