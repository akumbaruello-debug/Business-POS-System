'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '@/lib/api-client'
import { useLanguage } from '@/lib/i18n'
import type {
  CompareValue,
  DashboardResponse,
  InventoryReportResponse,
} from '@/lib/dashboard-types'
import type { SalesReportResponse } from '@/lib/report-types'
import { COMPARE_OPTIONS, PERIOD_OPTIONS } from '@/lib/dashboard-types'
import { formatIDR, formatInt, formatPct } from '@/lib/format'
import { initialsFor, useSession } from '@/lib/session'
import {
  AlertCircle,
  ArrowUpRight,
  Banknote,
  BarChart3,
  Boxes,
  CircleDollarSign,
  History,
  Package,
  RefreshCw,
  ShoppingCart,
  TrendingUp,
  Undo2,
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

function greetingForHour(hour: number): string {
  if (hour < 12) return 'dashboard.greeting.morning'
  if (hour < 17) return 'dashboard.greeting.afternoon'
  return 'dashboard.greeting.evening'
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

function currentMonthWindow(now = new Date()): { from: string; to: string } {
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 1)
  return {
    from: start.toISOString(),
    to: new Date(end.getTime() - 1).toISOString(),
  }
}

function localDayKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function completeTrendWindow(data: TrendPoint[], from: Date, to: Date): TrendPoint[] {
  const byDay = new Map<string, TrendPoint>(
    data
      .filter((point) => point.period_start != null)
      .map((point) => [localDayKey(new Date(point.period_start!)), point]),
  )
  const start = new Date(from.getFullYear(), from.getMonth(), from.getDate())
  const end = new Date(to.getFullYear(), to.getMonth(), to.getDate())
  const days = Math.max(0, Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1)
  return Array.from({ length: days }, (_, index) => {
    const day = new Date(start.getFullYear(), start.getMonth(), start.getDate() + index)
    const key = localDayKey(day)
    return byDay.get(key) ?? { period_start: day.toISOString(), revenue: 0, sales_count: 0 }
  })
}

function completeCurrentMonthTrend(data: TrendPoint[], now = new Date()): TrendPoint[] {
  return completeTrendWindow(
    data,
    new Date(now.getFullYear(), now.getMonth(), 1),
    new Date(now.getFullYear(), now.getMonth() + 1, 0),
  )
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
  const { t } = useLanguage()
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
          <span className="change-note">{t('dashboard.vsPrevPeriod')}</span>
        </div>
      ) : (
        <div className="metric-change">
          <span className="change-note">{t('dashboard.currentPeriod')}</span>
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

function SalesTrendChart({ data, comparisonData = null, loading }: {
  data: TrendPoint[]
  comparisonData?: TrendPoint[] | null
  loading: boolean
}) {
  const { t } = useLanguage()
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState<{ width: number; height: number }>({ width: 0, height: 0 })
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)
  const [hoveredSeries, setHoveredSeries] = useState<'current' | 'previous'>('current')

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
          <strong>{t('dashboard.noSalesData')}</strong>
          <p>{t('dashboard.revenueAppearsHere')}</p>
        </div>
      </div>
    )
  }

  const { pathD, points, yScale, tickVals, labelFn, plotBottom, barWidth } = computeChartGeometry({
    data,
    comparisonData: comparisonData ?? undefined,
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
  const hoverRevenue = comparisonData && hoveredSeries === 'previous'
    ? comparisonData[hoveredIdx ?? -1]?.revenue ?? 0
    : hovered?.revenue ?? 0
  const hoverLabel = hovered?.label
    ? `${hovered.label}${comparisonData ? ` · ${hoveredSeries === 'current' ? t('dashboard.currentPeriod') : t('dashboard.vsPrevPeriod')}` : ''}`
    : null
  const hoverSalesCount = comparisonData && hoveredSeries === 'previous'
    ? comparisonData[hoveredIdx ?? -1]?.sales_count as number | undefined
    : hovered?.salesCount

  return (
    <div className="line-chart" ref={containerRef}>
      <svg
        className="line-chart-svg"
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        role="img"
        aria-label={t('dashboard.salesTrend')}
      >
        {/* horizontal gridlines */}
        {tickVals.map((val, i) => {
          const y = yScale(val)
          return (
            <g key={i}>
              <line
                className="line-chart-grid"
                x1={CHART_PAD.left}
                y1={y}
                x2={svgWidth - CHART_PAD.right}
                y2={y}
              />
              <text
                  className="line-chart-axis-text"
                  x={CHART_PAD.left - 8}
                  y={y + 3}
                  textAnchor="end"
                >
                  {labelFn(val)}
              </text>
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

        {comparisonData ? (
          <g className="line-chart-comparison-bars">
            {points.map((point, i) => {
              const currentWidth = Math.max(2, barWidth * 0.42)
              const previousWidth = currentWidth
              const previousValue = comparisonData[i]?.revenue ?? 0
              const currentX = point.x - barWidth / 2
              const previousX = point.x + barWidth / 2 - previousWidth
              return (
                <g key={i}>
                  <rect className="line-chart-bar-current" x={currentX} y={point.y} width={currentWidth} height={Math.max(0, plotBottom - point.y)} rx={2} onMouseEnter={() => { setHoveredIdx(i); setHoveredSeries('current') }} onMouseLeave={() => setHoveredIdx(null)} />
                  <rect className="line-chart-bar-previous" x={previousX} y={yScale(previousValue)} width={previousWidth} height={Math.max(0, plotBottom - yScale(previousValue))} rx={2} onMouseEnter={() => { setHoveredIdx(i); setHoveredSeries('previous') }} onMouseLeave={() => setHoveredIdx(null)} />
                </g>
              )
            })}
            {hovered && hoveredIdx !== null && (
              <SvgTooltip cx={hovered.x} cy={hoveredSeries === 'previous' ? yScale(hoverRevenue) : hovered.y} svgWidth={svgWidth} svgHeight={svgHeight} label={hoverLabel} revenue={hoverRevenue} salesCount={hoverSalesCount} />
            )}
          </g>
        ) : (
          <>
            <defs>
              <linearGradient id="sales-area-fill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="var(--primary)" stopOpacity="0.28" />
                <stop offset="100%" stopColor="var(--primary)" stopOpacity="0.02" />
              </linearGradient>
            </defs>
            <path className="line-chart-area" d={`${pathD} L ${points[points.length - 1].x} ${plotBottom} L ${points[0].x} ${plotBottom} Z`} />
            <path className="line-chart-line" d={pathD} />
          </>
        )}

        {/* data points + hover markers */}
        {!comparisonData && points.map((p, i) => {
          const isHovered = hoveredIdx === i
          return (
            <g key={i}>
              <circle
                className={`line-chart-dot${isHovered ? ' hovered' : ''}`}
                cx={p.x}
                cy={p.y}
                r={isHovered ? 5 : points.length > 14 ? 2.5 : 4}
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
  const { t } = useLanguage()
  if (!label) return null

  const lines = [`${label}`, `${formatIDR(revenue)}`]
  if (salesCount !== undefined) lines.push(`${salesCount} ${salesCount === 1 ? t('dashboard.sale') : t('dashboard.sales')}`)

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
  comparisonData,
  plotW,
  plotH,
  pad,
  tension = 0.3,
}: {
  data: TrendPoint[]
  comparisonData?: TrendPoint[]
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

  const revenues = [
    ...points.map((p) => p.revenue),
    ...(comparisonData ?? []).map((p) => p.revenue ?? 0),
  ]
  const maxRev = Math.max(...revenues, 1) * 1.15

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
  const plotBottom = pad.top + plotH
  const barWidth = Math.min(18, Math.max(4, xStep * 0.8))

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

  return { pathD, points, yScale, tickVals, labelFn, plotBottom, barWidth }
}

// -----------------------------------------------------------------------
// Donut breakdowns — category share with exact values and percentages.
// -----------------------------------------------------------------------

interface BreakdownRow {
  name: string
  value: number
}

const BREAKDOWN_COLORS = ['#1769e0', '#10a879', '#f59e0b', '#8b5cf6', '#ef5b52', '#0891b2', '#84a20b', '#db2777', '#64748b', '#a16207']

function DonutBreakdown({
  data,
  loading,
  emptyMessage,
  centerLabel,
}: {
  data: BreakdownRow[]
  loading: boolean
  emptyMessage: string
  centerLabel?: string
}) {
  const { t } = useLanguage()
  const rows = data.filter((row) => Number.isFinite(row.value) && row.value > 0)
  const total = rows.reduce((sum, row) => sum + row.value, 0)

  if (loading) {
    return <div className="donut-loading"><div className="skeleton skeleton-donut" /><div className="donut-loading-legend">{[0, 1, 2, 3].map((i) => <div className="skeleton" key={i} />)}</div></div>
  }
  if (rows.length === 0 || total <= 0) {
    return <div className="donut-empty"><div className="donut-empty-icon"><BarChart3 size={20} /></div><strong>{t('dashboard.noDataYet')}</strong><p>{emptyMessage}</p></div>
  }

  let running = 0
  const stops = rows.map((row, index) => {
    const start = running
    running += (row.value / total) * 100
    return `${BREAKDOWN_COLORS[index % BREAKDOWN_COLORS.length]} ${start}% ${running}%`
  })

  return (
    <div className="donut-breakdown">
      <div className="donut-plot" role="img" aria-label={rows.map((row) => `${row.name}: ${(row.value / total * 100).toFixed(1)}%, ${formatIDR(row.value)}`).join('; ')} style={{ background: `conic-gradient(${stops.join(', ')})` }}>
        <div className="donut-hole"><strong>{formatIDR(total)}</strong><span>{centerLabel ?? t('dashboard.periodTotal')}</span></div>
      </div>
      <div className="donut-legend">
        {rows.map((row, index) => (
          <div className="donut-legend-row" key={`${row.name}-${index}`} title={`${row.name}: ${formatIDR(row.value)} (${(row.value / total * 100).toFixed(1)}%)`}>
            <i style={{ background: BREAKDOWN_COLORS[index % BREAKDOWN_COLORS.length] }} />
            <span className="donut-legend-name">{row.name}</span>
            <span className="donut-legend-share">{(row.value / total * 100).toFixed(1)}%</span>
            <strong>{formatIDR(row.value)}</strong>
          </div>
        ))}
      </div>
    </div>
  )
}

// -----------------------------------------------------------------------
// Main page
// -----------------------------------------------------------------------

export default function DashboardPage() {
  const user = useSession()
  const initials = initialsFor(user)
  const { t } = useLanguage()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null)
  const [comparisonTrend, setComparisonTrend] = useState<TrendPoint[] | null>(null)
  const [salesBreakdown, setSalesBreakdown] = useState<SalesReportResponse['data'] | null>(null)
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
        const params: Record<string, string> = period === 'this_month'
          ? { period: 'custom', ...currentMonthWindow() }
          : { period }
        if (compareTo !== 'none') params.compare_to = compareTo
        // Fetch independently: /dashboard requires finance.view_profit
        // (Staff → 403), /dashboard/inventory only inventory.view (Staff OK).
        // A forbidden financial block must not hide the inventory KPIs.
        const [dashRes, invRes, salesRes] = await Promise.allSettled([
          fetchDashboard(params),
          fetchInventory(),
          api.get<SalesReportResponse>('/reports/sales', { params }),
        ])
        setSalesBreakdown(salesRes.status === 'fulfilled' ? salesRes.value.data : null)
        if (dashRes.status === 'fulfilled') {
          setDashboard(dashRes.value)
          const previous = dashRes.value.comparison?.previous_period
          if (compareTo !== 'none' && previous) {
            try {
              const comparisonDashboard = await fetchDashboard({
                period: 'custom',
                from: previous.from,
                to: previous.to,
              })
              setComparisonTrend(comparisonDashboard.charts?.sales_trend ?? [])
            } catch {
              setComparisonTrend(null)
            }
          } else {
            setComparisonTrend(null)
          }
        } else if (isForbidden(dashRes.reason)) {
          setDashboard(null)
          setComparisonTrend(null)
          setError('forbidden')
        } else {
          setError(dashRes.reason instanceof Error ? dashRes.reason.message : t('common.failedToLoad', { resource: 'dashboard' }))
        }
        if (invRes.status === 'fulfilled') {
          setInventory(invRes.value)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : t('common.failedToLoad', { resource: 'dashboard' }))
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
            <h1>{t('nav.dashboard')}</h1>
            <p>{t('dashboard.subtitle')}</p>
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

    if (inventory) {
      return (
        <>
          {financialBlock}
          <div className="content">
            <section className="metrics">
              <MetricCard
                label={t('dashboard.inventoryAtCost')}
                value={formatIDR(inventory.data.total_inventory_value)}
                deltaPct={null}
                icon={Package}
                loading={false}
              />
              <MetricCard
                label={t('dashboard.lowStockItems')}
                value={formatInt(inventory.data.low_stock_count)}
                deltaPct={null}
                icon={AlertCircle}
                loading={false}
              />
              <MetricCard
                label={t('dashboard.outOfStock')}
                value={formatInt(inventory.data.out_of_stock_count)}
                deltaPct={null}
                icon={Boxes}
                loading={false}
              />
              <MetricCard
                label={t('dashboard.products')}
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
            <h1>{t('nav.dashboard')}</h1>
            <p>{t('dashboard.subtitle')}</p>
          </div>
          <div className="heading-actions">
            <button className="button button-secondary" onClick={handleRefresh}>
              <RefreshCw size={14} />
              {t('common.retry')}
            </button>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={32} />
          </div>
          <strong>{t('common.failedToLoad', { resource: 'dashboard' })}</strong>
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
            {t(greetingForHour(now.getHours()))}, {user.full_name || user.username}
          </h1>
          <p>{t('dashboard.businessUpdate')}</p>
        </div>
        <div className="heading-actions">
          <span className="as-of-note">
            {dashboard ? t('common.updated', { time: fmtAsOf(dashboard.as_of) }) : ''}
          </span>
          <button
            className="button button-secondary"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw size={14} className={refreshing ? 'spin' : ''} />
            {refreshing ? t('common.refreshing') : t('common.refresh')}
          </button>
        </div>
      </div>

      {/* Period / comparison filters */}
      <div className="dashboard-filters">
        <label className="filter-field">
          <span className="filter-label">{t('dashboard.period')}</span>
          <select
            aria-label={t('dashboard.period')}
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
          <span className="filter-label">{t('dashboard.compare')}</span>
          <select
            aria-label={t('dashboard.compare')}
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
          <span className="filter-hint">{t('dashboard.customRangesNotAvailable')}</span>
        )}
      </div>

      {/* KPI cards */}
      <section className="metrics">
        <MetricCard
          label={t('dashboard.totalRevenue')}
          value={kpis ? formatIDR(kpis.total_sales) : ''}
          deltaPct={comparison ? deltaPctOf('total_sales') : null}
          icon={CircleDollarSign}
          loading={loading}
        />
        <MetricCard
          label={t('dashboard.totalPurchases')}
          value={kpis ? formatIDR(kpis.total_purchases) : ''}
          deltaPct={comparison ? deltaPctOf('total_purchases') : null}
          icon={ShoppingCart}
          loading={loading}
        />
        <MetricCard
          label={t('dashboard.netProfit')}
          value={kpis ? formatIDR(kpis.net_profit) : ''}
          deltaPct={comparison ? deltaPctOf('net_profit') : null}
          icon={TrendingUp}
          loading={loading}
        />
        <MetricCard
          label={t('dashboard.cashOnHand')}
          value={kpis ? formatIDR(kpis.cash_on_hand) : ''}
          deltaPct={null}
          icon={BarChart3}
          loading={loading}
        />
      </section>

      {/* Row 2: Inventory metrics */}
      <section className="metrics metrics-secondary">
        <MetricCard
          label={t('dashboard.inventoryAtCost')}
          value={kpis ? formatIDR(kpis.inventory_value) : ''}
          deltaPct={null}
          icon={Package}
          loading={loading}
        />
        <MetricCard
          label={t('dashboard.lowStockItems')}
          value={kpis ? formatInt(kpis.low_stock_count) : ''}
          deltaPct={null}
          icon={AlertCircle}
          loading={loading}
        />
        {inventory && (
          <>
            <MetricCard
              label={t('dashboard.outOfStock')}
              value={formatInt(inventory.data.out_of_stock_count)}
              deltaPct={null}
              icon={Boxes}
              loading={false}
            />
            <MetricCard
              label={t('dashboard.products')}
              value={formatInt(inventory.data.products_count)}
              deltaPct={null}
              icon={Users}
              loading={false}
            />
          </>
        )}
      </section>

      {/* Charts row */}
      <section className="dashboard-grid dashboard-visuals-grid">
        {/* Sales trend */}
        <article className="panel dashboard-trend-panel">
          <div className="panel-header">
            <div>
              <h2>{t('dashboard.salesTrend')}</h2>
              <p>{compareTo !== 'none' ? `${t('dashboard.currentPeriod')} ${t('dashboard.vsPrevPeriod')}` : t('dashboard.salesTrendDesc')}</p>
            </div>
            {compareTo !== 'none' && (
              <div className="chart-legend" aria-label="Chart legend">
                <span><i className="chart-legend-current" />{t('dashboard.currentPeriod')}</span>
                <span><i className="chart-legend-previous" />{t('dashboard.vsPrevPeriod')}</span>
              </div>
            )}
            {comparison && (
              <ComparisonBadge
                delta={comparison.delta['total_sales']}
                deltaPct={comparison.delta_pct['total_sales']}
                loading={loading}
              />
            )}
          </div>
          <SalesTrendChart
            data={period === 'this_month'
              ? completeCurrentMonthTrend(dashboard?.charts?.sales_trend ?? [])
              : dashboard?.charts?.sales_trend ?? []}
            comparisonData={compareTo === 'none' || !comparisonTrend
              ? null
              : period === 'this_month' && comparison?.previous_period
                ? completeTrendWindow(
                    comparisonTrend,
                    new Date(comparison.previous_period.from),
                    new Date(comparison.previous_period.to),
                  )
                : comparisonTrend}
            loading={loading}
          />
        </article>

        {/* Sales composition */}
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>{t('dashboard.revenueComposition')}</h2>
              <p>{t('dashboard.revenueCompositionDesc')}</p>
            </div>
          </div>
          <DonutBreakdown
            data={salesBreakdown ? [
              { name: t('reports.cogs'), value: salesBreakdown.cogs },
              { name: t('reports.grossProfit'), value: salesBreakdown.gross_profit },
            ] : []}
            loading={loading}
            emptyMessage={t('dashboard.noSalesData')}
          />
        </article>

        {/* Best sellers */}
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>{t('dashboard.bestSellers')}</h2>
              <p>{t('dashboard.bestSellersDesc')}</p>
            </div>
          </div>
          <DonutBreakdown
            data={(dashboard?.charts?.best_sellers ?? []).map((p) => ({
              name: p.name || '—',
              value: p.revenue ?? 0,
            }))}
            loading={loading}
            emptyMessage={t('dashboard.noProductSalesThisPeriod')}
            centerLabel={t('dashboard.topProductsTotal')}
          />
        </article>

        {/* Expense breakdown */}
        <article className="panel">
          <div className="panel-header">
            <div>
              <h2>{t('dashboard.expenseBreakdown')}</h2>
              <p>{t('dashboard.expenseBreakdownDesc')}</p>
            </div>
          </div>
          <DonutBreakdown
            data={(dashboard?.charts?.expense_breakdown ?? []).map((e) => ({
              name: e.category || '—',
              value: e.total ?? 0,
            }))}
            loading={loading}
            emptyMessage={t('dashboard.noExpensesThisPeriod')}
          />
        </article>
      </section>

      {/* Quick actions (V0 baseline — common tasks) */}
      <section className="dashboard-grid dashboard-grid-bottom">
        <article className="panel workspace-card">
          <div className="panel-header">
            <div>
              <h2>{t('dashboard.quickActions')}</h2>
              <p>{t('dashboard.quickActionsDesc')}</p>
            </div>
          </div>
          <div className="quick-actions">
            {/* V1 P0-4: previously withheld quick-actions restored now that
                the pages ship; each gated by the same capability the
                destination page checks (mirrors sidebar). Labels reuse
                existing dictionary keys. */}
            {([
              { href: '/pos', required: 'sale.create', icon: ShoppingCart, color: 'blue',
                strong: 'sales.newSale', small: 'pos.subtitle' },
              { href: '/payments', required: 'sale.view', icon: Banknote, color: 'green',
                strong: 'payments.title', small: 'payments.subtitle' },
              { href: '/finance/cash', required: 'finance.view_cash', icon: CircleDollarSign, color: 'amber',
                strong: 'cash.title', small: 'cash.subtitle' },
              { href: '/purchases/returns', required: 'purchase.view', icon: Undo2, color: 'green',
                strong: 'purchaseReturns.title', small: 'purchaseReturns.subtitle' },
              { href: '/inventory/movements', required: 'inventory.view', icon: History, color: 'blue',
                strong: 'stockMovements.title', small: 'stockMovements.subtitle' },
            ] as const)
              .filter((a) => user.capabilities.includes(a.required))
              .map((a) => (
                <a key={a.href} href={a.href} className="quick-action-link">
                  <span className={`action-icon ${a.color}`}>
                    <a.icon size={16} />
                  </span>
                  <span>
                    <strong>{t(a.strong)}</strong>
                    <small>{t(a.small)}</small>
                  </span>
                  <ArrowUpRight size={15} />
                </a>
              ))}
            <a href="/products" className="quick-action-link">
              <span className="action-icon green">
                <Package size={16} />
              </span>
              <span>
                <strong>{t('dashboard.addProduct')}</strong>
                <small>{t('dashboard.updateCatalogue')}</small>
              </span>
              <ArrowUpRight size={15} />
            </a>
          </div>
        </article>
      </section>
    </div>
  )
}
