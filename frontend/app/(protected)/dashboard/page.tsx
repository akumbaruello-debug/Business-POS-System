'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '@/lib/api-client'
import type {
  CompareValue,
  DashboardResponse,
  InventoryReportResponse,
} from '@/lib/dashboard-types'
import { COMPARE_OPTIONS, PERIOD_OPTIONS } from '@/lib/dashboard-types'
import { formatIDR, formatInt, formatPct } from '@/lib/format'
import { initialsFor, useSession } from '@/lib/session'
import {
  AlertCircle,
  ArrowUpRight,
  BarChart3,
  BarChartHorizontal,
  Boxes,
  CircleDollarSign,
  Package,
  RefreshCw,
  ShoppingCart,
  TrendingUp,
  Users,
} from 'lucide-react'

// -----------------------------------------------------------------------
// Data fetching
// -----------------------------------------------------------------------

async function fetchDashboard(params?: Record<string, string>): Promise<DashboardResponse> {
  return api.get<DashboardResponse>('/dashboard', { params })
}

async function fetchInventory(): Promise<InventoryReportResponse> {
  return api.get<InventoryReportResponse>('/dashboard/inventory')
}

function isForbidden(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : ''
  return msg.toLowerCase().includes('permission') || msg.toLowerCase().includes('forbidden')
}

const PERMISSIONS_MSG =
  "You don't have permission to view the financial dashboard. Contact your administrator."

function greetingForHour(hour: number): string {
  if (hour < 12) return 'Good morning'
  if (hour < 17) return 'Good afternoon'
  return 'Good evening'
}

function fmtLongDate(d: Date): string {
  return d.toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

function fmtAsOf(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// -----------------------------------------------------------------------
// MetricCard
// -----------------------------------------------------------------------

interface MetricCardProps {
  label: string
  value: string
  deltaPct: number | null
  icon: React.ElementType
  loading?: boolean
}

function MetricCard({ label, value, deltaPct, icon: Icon, loading }: MetricCardProps) {
  // Delta styling comes from the numeric delta sign, never the formatted
  // string. Positive up (green), negative down (red), zero neutral (muted).
  const dir =
    deltaPct === null ? null : deltaPct > 0 ? 'positive' : deltaPct < 0 ? 'negative' : 'neutral'
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
      ) : deltaPct !== null ? (
        <div className={`metric-change ${dir}`}>
          <span className="metric-delta">
            {deltaPct > 0 ? '↗' : deltaPct < 0 ? '↘' : '→'} {formatPct(deltaPct)}
          </span>
          <span className="change-note">vs. prev. period</span>
        </div>
      ) : (
        <div className="metric-change">
          <span className="change-note">Current period</span>
        </div>
      )}
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
  const deltaStr = delta !== null ? formatIDR(Math.abs(delta)) : null

  return (
    <span className={`comparison-badge ${colorClass}`}>
      <TrendingUp size={11} />
      {pctStr && <span>{pctStr}</span>}
      {deltaStr && (
        <span>
          {sign}
          {deltaStr}
        </span>
      )}
    </span>
  )
}

// -----------------------------------------------------------------------
// SalesTrendChart — responsive line chart (backend: { period_start, revenue, sales_count })
// -----------------------------------------------------------------------

interface TrendPoint {
  period_start: string | null
  revenue: number
  [key: string]: unknown
}

// Chart margins — plot uses most of the measured body; left keeps Y labels
// inside the SVG, bottom keeps X labels + tooltip room.
const CHART_PAD = { top: 12, right: 16, bottom: 26, left: 74 }

function SalesTrendChart({ data, loading }: { data: TrendPoint[]; loading: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState<{ width: number; height: number }>({ width: 0, height: 0 })
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)

  // Measure the actual container size via ResizeObserver — responds to sidebar open/close
  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return

    const measure = () => {
      const cr = el.getBoundingClientRect()
      setDims({ width: Math.round(cr.width), height: Math.round(cr.height) })
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // viewBox = measured container size 1:1 — geometry maps to real pixels.
  // The flex chart body (not the SVG) owns the height: the SVG fills whatever
  // the card's remaining space provides, so it can never push the card taller.
  const svgWidth = Math.max(dims.width, 1)
  const svgHeight = Math.max(dims.height, 1)
  const plotW = Math.max(svgWidth - CHART_PAD.left - CHART_PAD.right, 0)
  const plotH = Math.max(svgHeight - CHART_PAD.top - CHART_PAD.bottom, 0)

  if (loading) {
    return (
      <div className="chart-placeholder" ref={containerRef}>
        <div className="chart-skeleton">
          {Array.from({ length: 7 }).map((_, i) => (
            <div
              key={i}
              className="chart-bar-skeleton"
              style={{ height: `${30 + (i % 4) * 15}%` }}
            />
          ))}
        </div>
      </div>
    )
  }

  if (!data || data.length === 0) {
    return (
      <div className="line-chart" ref={containerRef}>
        <div className="empty-workspace">
          <div className="empty-icon">
            <BarChart3 size={22} />
          </div>
          <strong>No sales data</strong>
          <p>Revenue will appear here once sales are recorded.</p>
        </div>
      </div>
    )
  }

  const { pathD, points, yScale, tickVals, labelFn } = computeChartGeometry({
    data,
    plotW,
    plotH,
    pad: CHART_PAD,
    // barely-visible curvature: only the corner at each vertex is rounded,
    // the run between points stays visually straight.
    tension: 0.06,
  })

  // Safely resolve the hovered point — guards against stale hoveredIdx
  // when data/period changes. No conditional hook here; this is pure derivation.
  const hovered =
    hoveredIdx !== null && hoveredIdx >= 0 && hoveredIdx < points.length
      ? points[hoveredIdx]
      : null
  const hoverRevenue = hovered?.revenue ?? 0
  const hoverLabel = hovered?.label ?? null
  const hoverSalesCount = hovered?.salesCount

  return (
    <div className="line-chart" ref={containerRef}>
      <svg
        className="line-chart-svg"
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        role="img"
        aria-label="Sales trend over time"
      >
        {/* horizontal gridlines */}
        {tickVals.map((val, i) => {
          const y = yScale(val)
          const skipZero = i === 0 && tickVals.length > 1
          return (
            <g key={i}>
              <line
                className="line-chart-grid"
                x1={CHART_PAD.left}
                y1={y}
                x2={svgWidth - CHART_PAD.right}
                y2={y}
              />
              {!skipZero && (
                <text
                  className="line-chart-axis-text"
                  x={CHART_PAD.left - 8}
                  y={y + 3}
                  textAnchor="end"
                >
                  {labelFn(val)}
                </text>
              )}
            </g>
          )
        })}

        {/* X-axis line */}
        <line
          className="line-chart-axis"
          x1={CHART_PAD.left}
          y1={plotH + CHART_PAD.top}
          x2={svgWidth - CHART_PAD.right}
          y2={plotH + CHART_PAD.top}
        />

        {/* X-axis labels — thinned to ~every 60px so text never collides;
            every data point still plots (dots below, unlabeled). */}
        {points.map(
          (p, i) =>
            p.showLabel && (
              <text
                key={i}
                className="line-chart-axis-text"
                x={p.x}
                y={plotH + CHART_PAD.top + 16}
                textAnchor="middle"
              >
                {p.label}
              </text>
            )
        )}

        {/* the line */}
        <path className="line-chart-line" d={pathD} />

        {/* data points + hover markers */}
        {points.map((p, i) => {
          const isHovered = hoveredIdx === i
          return (
            <g key={i}>
              <circle
                className={`line-chart-dot${isHovered ? ' hovered' : ''}`}
                cx={p.x}
                cy={p.y}
                r={isHovered ? 5 : 4}
                onMouseEnter={() => setHoveredIdx(i)}
                onMouseLeave={() => setHoveredIdx(null)}
              />
              {isHovered && (
                <SvgTooltip
                  cx={p.x}
                  cy={p.y}
                  svgWidth={svgWidth}
                  svgHeight={svgHeight}
                  label={hoverLabel}
                  revenue={hoverRevenue}
                  salesCount={hoverSalesCount}
                />
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}

/**
 * SVG-native tooltip that stays inside the chart bounds.
 * Positions to the right of the dot by default; flips left near the right edge.
 */
function SvgTooltip({
  cx,
  cy,
  svgWidth,
  svgHeight,
  label,
  revenue,
  salesCount,
}: {
  cx: number
  cy: number
  svgWidth: number
  svgHeight: number
  label: string | null
  revenue: number
  salesCount?: number
}) {
  if (!label) return null

  const lines = [`${label}`, `${formatIDR(revenue)}`]
  if (salesCount !== undefined) lines.push(`${salesCount} sale${salesCount !== 1 ? 's' : ''}`)

  const fontSize = 11
  const lineHeight = 14
  const padX = 8
  const padY = 6
  const rectW = Math.max(...lines.map((l) => l.length * 6.2 + padX * 2))
  const rectH = lines.length * lineHeight + padY * 2

  // Right-edge detection: prefer tooltip to the right of the dot
  const fitsRight = cx + rectW + 12 <= svgWidth - CHART_PAD.right
  const rectX = fitsRight ? cx + 8 : cx - rectW - 8
  // Vertically clamp so tooltip never goes above/below SVG bounds
  let rectY = cy - rectH / 2 - 8
  const minY = CHART_PAD.top + 4
  const maxY = svgHeight - CHART_PAD.bottom - rectH - 4
  rectY = Math.max(minY, Math.min(maxY, rectY))

  const textX = rectX + padX
  const textY = rectY + padY + lineHeight - 1

  return (
    <g>
      <rect
        className="line-chart-tooltip-bg"
        x={rectX}
        y={rectY}
        width={rectW}
        height={rectH}
        rx={4}
      />
      {lines.map((line, i) => (
        <text
          key={i}
          className="line-chart-tooltip-text"
          x={textX}
          y={textY + i * lineHeight}
          fontSize={fontSize}
          fill="#fff"
          fontWeight={i === 0 ? 500 : 400}
        >
          {line}
        </text>
      ))}
    </g>
  )
}

/**
 * Compute SVG path, X positions, and Y scale for the line chart.
 *
 * @param tension - Catmull-Rom tension. 0 = sharp corners (piecewise linear),
 *                  1 = very rounded. 0.3 gives a polished but defined curve.
 */
function computeChartGeometry({
  data,
  plotW,
  plotH,
  pad,
  tension = 0.3,
}: {
  data: TrendPoint[]
  plotW: number
  plotH: number
  pad: { top: number; right: number; bottom: number; left: number }
  tension?: number
}) {
  // Build a unified list of chart points (skips null dates) so the line,
  // dots, x-labels, and hover all share one consistent index space.
  const points = data
    .map((d) => {
      const label =
        d.period_start != null
          ? new Date(d.period_start).toLocaleDateString('en-US', {
              month: 'short',
              day: 'numeric',
            })
          : null
      return {
        x: 0,
        y: 0,
        label,
        showLabel: false,
        revenue: d.revenue ?? 0,
        salesCount: d.sales_count as number | undefined,
      }
    })
    .filter((p) => p.label != null)

  const n = points.length
  const xStep = n > 1 ? plotW / (n - 1) : plotW / 2
  points.forEach((p, i) => {
    p.x = pad.left + (n > 1 ? i * xStep : plotW / 2)
  })

  // Show a date label roughly every 60px (always the last point).
  // Anchored from the right end so the leftmost visible label never drifts
  // half-off the plot's left edge; every data point still gets a dot.
  const pxPerPoint = n > 1 ? plotW / (n - 1) : plotW
  const labelStep = pxPerPoint > 0 ? Math.max(1, Math.ceil(60 / pxPerPoint)) : 1
  points.forEach((p, i) => {
    p.showLabel = (n - 1 - i) % labelStep === 0
  })

  const revenues = points.map((p) => p.revenue)
  const maxRev = Math.max(...revenues, 1)

  // Y-scale: map [0, maxRev] → [plotH+pad.top, pad.top] (inverted)
  const yScale = (v: number) => pad.top + plotH - (plotH * v) / maxRev
  points.forEach((p) => {
    p.y = yScale(p.revenue)
  })

  // Gridline tick values: 5 ticks from 0 to maxRev
  const ticks = 5
  const step = maxRev / ticks
  const tickVals = Array.from({ length: ticks + 1 }, (_, i) => Math.round(step * i))

  const labelFn = (v: number) => formatIDR(v)

  // Barely-curved connection: cubic segment per pair with tension t.
  // cp1 keeps y0 (horizontal out of the start), cp2 keeps y1 (horizontal into
  // the end); with t≈0.06 the control points hug the chord, so the run between
  // points stays visually straight and only the corner at each vertex rounds.
  // Monotone in y — the curve can never overshoot a neighbouring data point.
  let pathD = ''
  if (points.length === 1) {
    const { x, y } = points[0]
    pathD = `M ${x} ${y}`
  } else if (points.length > 1) {
    pathD = `M ${points[0].x} ${points[0].y}`
    for (let i = 0; i < points.length - 1; i++) {
      const x0 = points[i].x
      const x1 = points[i + 1].x
      const x2 = points[i + 2]?.x ?? x1
      const y0 = points[i].y
      const y1 = points[i + 1].y
      const xc1 = x0 + (x1 - x0) * tension
      const xc2 = x1 - (x2 - x0) * tension
      pathD += ` C ${xc1} ${y0} ${xc2} ${y1} ${x1} ${y1}`
    }
  }

  return { pathD, points, yScale, tickVals, labelFn }
}

// -----------------------------------------------------------------------
// DataTable — best sellers / expense breakdown
// -----------------------------------------------------------------------

interface TableRow {
  name: string
  value: number
}

function DataTable({
  data,
  loading,
  unit,
}: {
  data: TableRow[]
  loading: boolean
  unit?: string
}) {
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
        <div className="empty-icon">
          <BarChartHorizontal size={22} />
        </div>
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
          <span className="data-table-value">
            {unit ? formatIDR(row.value ?? 0) : formatInt(row.value ?? 0)}
          </span>
        </div>
      ))}
    </div>
  )
}

// -----------------------------------------------------------------------
// Main page
// -----------------------------------------------------------------------

export default function DashboardPage() {
  const user = useSession()
  const initials = initialsFor(user)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null)
  const [inventory, setInventory] = useState<InventoryReportResponse | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  // Period + comparison filters (backend query params)
  const [period, setPeriod] = useState<string>('this_month')
  const [compareTo, setCompareTo] = useState<CompareValue>('this_month')

  const load = useCallback(
    async (opts?: { quiet?: boolean }) => {
      if (!opts?.quiet) setLoading(true)
      setError(null)
      try {
        const params: Record<string, string> = { period }
        if (compareTo !== 'none') params.compare_to = compareTo
        // Fetch independently: /dashboard requires finance.view_profit
        // (Staff → 403), /dashboard/inventory only inventory.view (Staff OK).
        // A forbidden financial block must not hide the inventory KPIs.
        const [dashRes, invRes] = await Promise.allSettled([
          fetchDashboard(params),
          fetchInventory(),
        ])
        if (dashRes.status === 'fulfilled') {
          setDashboard(dashRes.value)
        } else if (isForbidden(dashRes.reason)) {
          setDashboard(null)
          setError('forbidden')
        } else {
          setError(dashRes.reason instanceof Error ? dashRes.reason.message : 'Failed to load dashboard')
        }
        if (invRes.status === 'fulfilled') {
          setInventory(invRes.value)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load dashboard')
      } finally {
        setLoading(false)
      }
    },
    [period, compareTo]
  )

  useEffect(() => {
    load()
  }, [load])

  const handleRefresh = async () => {
    setRefreshing(true)
    await load({ quiet: true })
    setRefreshing(false)
  }

  const comparison = dashboard?.comparison ?? null
  const kpis = dashboard?.kpis ?? null

  // Raw numeric delta fraction for the period (drives MetricCard sign styling)
  const deltaPctOf = (key: string): number | null => {
    if (!comparison) return null
    const pct = comparison.delta_pct[key]
    if (pct === null || pct === undefined) return null
    return pct
  }

  if (error === 'forbidden') {
    // Staff (no finance.view_profit): financial block restricted, but the
    // inventory KPIs (inventory.view) may still render below.
    const financialBlock = (
      <div className="content" style={{ paddingBottom: 0 }}>
        <div className="page-heading">
          <div>
            <h1>Dashboard</h1>
            <p>Financial overview</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={32} />
          </div>
          <strong>Access restricted</strong>
          <p>{PERMISSIONS_MSG}</p>
        </div>
      </div>
    )

    if (inventory) {
      return (
        <>
          {financialBlock}
          <div className="content">
            <section className="metrics">
              <MetricCard
                label="Inventory (at cost)"
                value={formatIDR(inventory.data.total_inventory_value)}
                deltaPct={null}
                icon={Package}
                loading={false}
              />
              <MetricCard
                label="Low Stock Items"
                value={formatInt(inventory.data.low_stock_count)}
                deltaPct={null}
                icon={AlertCircle}
                loading={false}
              />
              <MetricCard
                label="Out of Stock"
                value={formatInt(inventory.data.out_of_stock_count)}
                deltaPct={null}
                icon={Boxes}
                loading={false}
              />
              <MetricCard
                label="Products"
                value={formatInt(inventory.data.products_count)}
                deltaPct={null}
                icon={Users}
                loading={false}
              />
            </section>
          </div>
        </>
      )
    }
    return financialBlock
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
          <div className="error-icon">
            <AlertCircle size={32} />
          </div>
          <strong>Failed to load dashboard</strong>
          <p>{error}</p>
        </div>
      </div>
    )
  }

  const now = new Date()
  const greeting = greetingForHour(now.getHours())

  return (
    <div className="content">
      {/* Page header */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">{fmtLongDate(now)}</div>
          <h1>
            {greeting}, {user.full_name || user.username}
          </h1>
          <p>Here&apos;s what&apos;s happening with your business.</p>
        </div>
        <div className="heading-actions">
          <span className="as-of-note">
            {dashboard ? `Updated ${fmtAsOf(dashboard.as_of)}` : ''}
          </span>
          <button
            className="button button-secondary"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
            {refreshing ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Period / comparison filters */}
      <div className="dashboard-filters">
        <label className="filter-field">
          <span className="filter-label">Period</span>
          <select
            aria-label="Period"
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value)
            }}
          >
            {PERIOD_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span className="filter-label">Compare</span>
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
        </label>
        {period === 'custom' && (
          <span className="filter-hint">Custom ranges not yet available</span>
        )}
      </div>

      {/* KPI cards */}
      <section className="metrics">
        <MetricCard
          label="Total Revenue"
          value={kpis ? formatIDR(kpis.total_sales) : ''}
          deltaPct={comparison ? deltaPctOf('total_sales') : null}
          icon={CircleDollarSign}
          loading={loading}
        />
        <MetricCard
          label="Total Purchases"
          value={kpis ? formatIDR(kpis.total_purchases) : ''}
          deltaPct={comparison ? deltaPctOf('total_purchases') : null}
          icon={ShoppingCart}
          loading={loading}
        />
        <MetricCard
          label="Net Profit"
          value={kpis ? formatIDR(kpis.net_profit) : ''}
          deltaPct={comparison ? deltaPctOf('net_profit') : null}
          icon={TrendingUp}
          loading={loading}
        />
        <MetricCard
          label="Cash on Hand"
          value={kpis ? formatIDR(kpis.cash_on_hand) : ''}
          deltaPct={null}
          icon={BarChart3}
          loading={loading}
        />
      </section>

      {/* Row 2: Inventory metrics */}
      <section className="metrics metrics-secondary">
        <MetricCard
          label="Inventory (at cost)"
          value={kpis ? formatIDR(kpis.inventory_value) : ''}
          deltaPct={null}
          icon={Package}
          loading={loading}
        />
        <MetricCard
          label="Low Stock Items"
          value={kpis ? formatInt(kpis.low_stock_count) : ''}
          deltaPct={null}
          icon={AlertCircle}
          loading={loading}
        />
        {inventory && (
          <>
            <MetricCard
              label="Out of Stock"
              value={formatInt(inventory.data.out_of_stock_count)}
              deltaPct={null}
              icon={Boxes}
              loading={false}
            />
            <MetricCard
              label="Products"
              value={formatInt(inventory.data.products_count)}
              deltaPct={null}
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
          <SalesTrendChart data={dashboard?.charts?.sales_trend ?? []} loading={loading} />
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
              name: p.name || '—',
              value: p.revenue ?? 0,
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
              name: e.category || '—',
              value: e.total ?? 0,
            }))}
            loading={loading}
            unit="IDR"
          />
        </article>
      </section>

      {/* Quick actions (V0 baseline — common tasks) */}
      <section className="dashboard-grid dashboard-grid-bottom">
        <article className="panel workspace-card">
          <div className="panel-header">
            <div>
              <h2>Quick actions</h2>
              <p>Common tasks at your fingertips</p>
            </div>
          </div>
          <div className="quick-actions">
            {/* Only live routes ship — dead quick-actions (New sale /pos,
                Record payment /payments) withheld until pages exist. */}
            <a href="/products" className="quick-action-link">
              <span className="action-icon green">
                <Package size={16} />
              </span>
              <span>
                <strong>Add product</strong>
                <small>Update your catalogue</small>
              </span>
              <ArrowUpRight size={15} />
            </a>
          </div>
        </article>
      </section>
    </div>
  )
}
