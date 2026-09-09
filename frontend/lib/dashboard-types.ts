/**
 * Dashboard API types — mirrors openapi.yaml DashboardResponse (line 9176).
 *
 * The backend `GET /dashboard` endpoint returns:
 *   {
 *     kpis: { total_sales, total_purchases, net_profit|null,
 *             cash_on_hand|null, inventory_value, low_stock_count },
 *     charts: { sales_trend, best_sellers, expense_breakdown },
 *     comparison: PeriodComparison | null,
 *     as_of: ISO date-time,
 *   }
 *
 * `GET /dashboard/inventory` returns:
 *   { data: { total_inventory_value, products_count,
 *             low_stock_count, out_of_stock_count } }
 *
 * Period query: `period` (today|this_week|this_month|this_year|custom),
 * `from`/`to` ISO timestamps for period=custom, `compare_to` alias.
 * The route requires `finance.view_profit` (Owner only → 403 for Staff).
 */

export interface DashboardKpis {
  total_sales: number
  total_purchases: number
  net_profit: number | null
  cash_on_hand: number | null
  inventory_value: number
  low_stock_count: number
}

export interface PeriodComparison {
  previous_period: { from: string; to: string }
  delta: Record<string, number>
  delta_pct: Record<string, number | null>
}

/** Sales trend point — backend shape: { period_start, revenue, sales_count }. */
export interface SalesTrendPoint {
  period_start: string | null
  revenue: number
  sales_count: number
  [key: string]: unknown
}

/** Best-seller point — backend shape: { product_id, name, quantity, revenue }. */
export interface BestSellerPoint {
  product_id: number
  name: string
  quantity: number
  revenue: number
  [key: string]: unknown
}

/** Expense point — backend shape: { category, total }. */
export interface ExpensePoint {
  category: string
  total: number
  [key: string]: unknown
}

export interface DashboardCharts {
  sales_trend: SalesTrendPoint[]
  best_sellers: BestSellerPoint[]
  expense_breakdown: ExpensePoint[]
}

export interface DashboardResponse {
  kpis: DashboardKpis
  charts: DashboardCharts
  comparison: PeriodComparison | null
  as_of: string
}

export interface InventorySnapshot {
  total_inventory_value: number
  products_count: number
  low_stock_count: number
  out_of_stock_count: number
}

export interface InventoryReportResponse {
  data: InventorySnapshot
}

/** Valid `period` query values per backend period.py (_VALID_PERIODS). */
export const PERIOD_OPTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'this_week', label: 'This week' },
  { value: 'this_month', label: 'This month' },
  { value: 'this_year', label: 'This year' },
] as const

export type PeriodValue = (typeof PERIOD_OPTIONS)[number]['value']

/** `compare_to` values the backend accepts (period aliases). */
export const COMPARE_OPTIONS = [
  { value: 'none', label: 'No comparison' },
  { value: 'today', label: 'vs previous day' },
  { value: 'this_week', label: 'vs previous week' },
  { value: 'this_month', label: 'vs previous month' },
  { value: 'this_year', label: 'vs previous year' },
] as const

export type CompareValue = (typeof COMPARE_OPTIONS)[number]['value']
