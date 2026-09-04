'use client'

import { useState } from 'react'
import { api } from '@/lib/api-client'
import type { DashboardResponse, InventoryReportResponse } from '@/lib/dashboard-types'
import { formatIDR, formatInt, formatPct } from '@/lib/format'
import {
  AlertCircle,
  ArrowUpRight,
  BarChart3,
  BarChartHorizontal,
  CircleDollarSign,
  ClipboardList,
  Package,
  RefreshCw,
  ShoppingCart,
  TrendingUp,
  Users,
} from 'lucide-react'

// -----------------------------------------------------------------------
// Data fetching
// -----------------------------------------------------------------------

async function fetchDashboard(): Promise<DashboardResponse> {
  return api.get<DashboardResponse>('/dashboard')
}

async function fetchInventory(): Promise<InventoryReportResponse> {
  return api.get<InventoryReportResponse>('/dashboard/inventory')
}

// -----------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------

function is403(err: unknown): boolean {
  return err instanceof Error && err.message === 'Unauthorized'
}

function isForbidden(err: unknown): boolean {
  // The api client throws generic "API error" on 403; the response body
  // should carry the detail. Check the thrown message.
  const msg = err instanceof Error ? err.message : ''
  return msg.toLowerCase().includes('forbidden') || msg.includes('capability')
}

const PERMISSIONS_MSG =
  "You don't have permission to view the financial dashboard. Contact your administrator."

// -----------------------------------------------------------------------
// MetricCard
// -----------------------------------------------------------------------

interface MetricCardProps {
  label: string
  value: string
  change: string | null
  icon: React.ElementType
  loading?: boolean
}

function MetricCard({ label, value, change, icon: Icon, loading }: MetricCardProps) {
  return (
    <article className={`metric-card${loading ? ' loading' : ''}`}>
      <div className="metric-top">
        <span className="metric-label">{label}</span>
        <span className="metric-icon">
          <Icon size={18} />
        </span>
      </div>
      {loading ? (
        <div className="skeleton skeleton-value" />
      ) : (
        <div className="metric-value">{value}</div>
      )}
      {loading ? (
        <div className="skeleton skeleton-change" />
      ) : change !== null ? (
        <div className="metric-change positive">
          ↗ {change}
          <span className="change-note">vs. prev. period</span>
        </div>
      ) : null}
    </article>
  )
}

// -----------------------------------------------------------------------
// ComparisonBadge — shows delta vs previous period
// -----------------------------------------------------------------------

interface ComparisonBadgeProps {
  delta: number | null
  deltaPct: number | null
  loading?: boolean
}

function ComparisonBadge({ delta, deltaPct, loading }: ComparisonBadgeProps) {
  if (loading) return <span className="skeleton skeleton-badge" />
  if (delta === null && deltaPct === null) return null

  const positive = (delta ?? 0) >= 0
  const colorClass = positive ? 'positive' : 'negative'
  const sign = positive ? '+' : ''

  const pctStr = deltaPct !== null ? formatPct(deltaPct) : null
  const deltaStr = delta !== null ? formatIDR(delta) : null

  return (
    <span className={`comparison-badge ${colorClass}`}>
      <TrendingUp size={11} />
      {pctStr && <span>{pctStr}</span>}
      {deltaStr && <span>{deltaStr}</span>}
    </span>
  )
}

// -----------------------------------------------------------------------
// Chart — bar-style trend (renders from sales_trend data)
// -----------------------------------------------------------------------

interface ChartProps {
  data: DashboardResponse['charts']['sales_trend']
  loading?: boolean
  title: string
  unit?: string
}

function BarChart({ data, loading, title, unit }: ChartProps) {
  if (loading) {
    return (
      <div className="chart-placeholder">
        <div className="chart-skeleton">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="chart-bar-skeleton" style={{ height: `${30 + (i % 4) * 15}%` }} />
          ))}
        </div>
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="empty-workspace">
        <div className="empty-icon"><BarChart3 size={22} /></div>
        <strong>No {title.toLowerCase()} data</strong>
        <p>Data will appear once transactions are recorded.</p>
      </div>
    )
  }

  const max = Math.max(...data.map((d) => d.value ?? 0))

  return (
    <div className="bar-chart">
      {data.map((point, i) => (
        <div key={i} className="bar-chart-item" title={point.bucket}>
          <div
            className="bar-chart-bar"
            style={{ height: max > 0 ? `${((point.value ?? 0) / max) * 100}%` : '0%' }}
          />
          <span className="bar-chart-label">{point.bucket}</span>
          {unit && <span className="bar-chart-value">{formatIDR(point.value ?? 0)}</span>}
        </div>
      ))}
    </div>
  )
}

// -----------------------------------------------------------------------
// DataTable — best sellers / expense breakdown
// -----------------------------------------------------------------------

interface DataTableProps {
  data: Array<{ name: string; value: number; [key: string]: unknown }>
  loading?: boolean
  unit?: string
}

function DataTable({ data, loading, unit }: DataTableProps) {
  if (loading) {
    return (
      <div className="data-table">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="data-table-row-skeleton">
            <div className="skeleton skeleton-cell" />
            <div className="skeleton skeleton-cell short" />
          </div>
        ))}
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="empty-workspace">
        <div className="empty-icon"><BarChartHorizontal size={22} /></div>
        <strong>No data yet</strong>
        <p>Records will appear here once available.</p>
      </div>
    )
  }

  const max = Math.max(...data.map((d) => d.value ?? 0))

  return (
    <div className="data-table">
      {data.map((row, i) => (
        <div key={i} className="data-table-row">
          <span className="data-table-rank">{i + 1}</span>
          <span className="data-table-name">{row.name}</span>
          <div className="data-table-bar-wrap">
            <div
              className="data-table-bar"
              style={{ width: max > 0 ? `${((row.value ?? 0) / max) * 100}%` : '0%' }}
            />
          </div>
          <span className="data-table-value">{unit ? formatIDR(row.value ?? 0) : formatInt(row.value ?? 0)}</span>
        </div>
      ))}
    </div>
  )
}

// -----------------------------------------------------------------------
// Main page
// -----------------------------------------------------------------------

export default function DashboardPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null)
  const [inventory, setInventory] = useState<InventoryReportResponse | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // Build greeting from date
  const [greeting, setGreeting] = useState('')
  useState(() => {
    const h = new Date().getHours()
    if (h < 12) setGreeting('Good morning')
    else if (h < 17) setGreeting('Good afternoon')
    else setGreeting('Good evening')
  })

  const load = async () => {
    setError(null)
    try {
      const [dash, inv] = await Promise.all([fetchDashboard(), fetchInventory()])
      setDashboard(dash)
      setInventory(inv)
    } catch (err) {
      if (isForbidden(err)) {
        setError('forbidden')
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load dashboard')
      }
    } finally {
      setLoading(false)
    }
  }

  useState(() => {
    load()
  })

  const handleRefresh = async () => {
    setRefreshing(true)
    await load()
    setRefreshing(false)
  }

  const comparison = dashboard?.comparison ?? null
  const kpis = dashboard?.kpis ?? null

  // Derive change strings from comparison delta_pct
  const pctChange = (key: string): string | null => {
    if (!comparison) return null
    const pct = comparison.delta_pct[key]
    if (pct === null || pct === undefined) return null
    return formatPct(pct)
  }

  if (error === 'forbidden') {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <h1>Dashboard</h1>
            <p>Financial overview</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon"><AlertCircle size={32} /></div>
          <strong>Access restricted</strong>
          <p>{PERMISSIONS_MSG}</p>
        </div>
      </div>
    )
  }

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <h1>Dashboard</h1>
            <p>Financial overview</p>
          </div>
          <div className="heading-actions">
            <button className="button button-secondary" onClick={handleRefresh}>
              <RefreshCw size={14} />
              Retry
            </button>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon"><AlertCircle size={32} /></div>
          <strong>Failed to load dashboard</strong>
          <p>{error}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Page header */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">
            {new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
          </div>
          <h1>{greeting || 'Good morning'}, Owner</h1>
          <p>Here&apos;s what&apos;s happening with your business.</p>
        </div>
        <div className="heading-actions">
          <button
            className="button button-secondary"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
          <button className="button button-primary">+ New transaction</button>
        </div>
      </div>

      {/* KPI cards */}
      <section className="metrics">
        <MetricCard
          label="Total Revenue"
          value={kpis ? formatIDR(kpis.total_sales) : ''}
          change={comparison ? pctChange('total_sales') : null}
          icon={CircleDollarSign}
          loading={loading}
        />
        <MetricCard
          label="Total Purchases"
          value={kpis ? formatIDR(kpis.total_purchases) : ''}
          change={comparison ? pctChange('total_purchases') : null}
          icon={ShoppingCart}
          loading={loading}
        />
        <MetricCard
          label="Net Profit"
          value={kpis ? formatIDR(kpis.net_profit) : ''}
          change={comparison ? pctChange('net_profit') : null}
          icon={TrendingUp}
          loading={loading}
        />
        <MetricCard
          label="Cash on Hand"
          value={kpis ? formatIDR(kpis.cash_on_hand) : ''}
          change={null}
          icon={BarChart3}
          loading={loading}
        />
      </section>

      {/* Row 2: Inventory metrics */}
      <section className="metrics metrics-secondary">
        <MetricCard
          label="Inventory Value"
          value={kpis ? formatIDR(kpis.inventory_value) : ''}
          change={null}
          icon={Package}
          loading={loading}
        />
        <MetricCard
          label="Low Stock Items"
          value={kpis ? formatInt(kpis.low_stock_count) : ''}
          change={null}
          icon={AlertCircle}
          loading={loading}
        />
        {inventory && (
          <>
            <MetricCard
              label="Out of Stock"
              value={formatInt(inventory.data.out_of_stock_count)}
              change={null}
              icon={Package}
              loading={false}
            />
            <MetricCard
              label="Products"
              value={formatInt(inventory.data.products_count)}
              change={null}
              icon={Users}
              loading={false}
            />
          </>
        )}
      </section>

      {/* Charts row */}
      <section className="dashboard-grid">
        {/* Sales trend */}
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>Sales trend</h2>
              <p>Revenue over the selected period</p>
            </div>
            {comparison && (
              <ComparisonBadge
                delta={comparison.delta['total_sales']}
                deltaPct={comparison.delta_pct['total_sales']}
                loading={loading}
              />
            )}
          </div>
          <BarChart
            data={dashboard?.charts?.sales_trend ?? []}
            loading={loading}
            title="Sales"
            unit="IDR"
          />
        </article>

        {/* Best sellers */}
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>Best sellers</h2>
              <p>Top products by revenue</p>
            </div>
          </div>
          <DataTable
            data={(dashboard?.charts?.best_sellers ?? []).map((p) => ({
              name: (p.product_name as string) ?? '—',
              value: (p.value as number) ?? 0,
            }))}
            loading={loading}
            unit="IDR"
          />
        </article>

        {/* Expense breakdown */}
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>Expense breakdown</h2>
              <p>Top expense categories</p>
            </div>
          </div>
          <DataTable
            data={(dashboard?.charts?.expense_breakdown ?? []).map((e) => ({
              name: (e.category as string) ?? '—',
              value: (e.amount as number) ?? 0,
            }))}
            loading={loading}
            unit="IDR"
          />
        </article>
      </section>
    </div>
  )
}
