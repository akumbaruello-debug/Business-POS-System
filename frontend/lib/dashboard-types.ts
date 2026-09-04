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
 * Period query: `period` (e.g. "this_month"), `from`, `to`, `compare_to`.
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

export interface ChartPoint {
  bucket?: string
  value?: number
  product_id?: number
  product_name?: string
  category?: string
  amount?: number
  [key: string]: unknown
}

export interface DashboardCharts {
  sales_trend: ChartPoint[]
  best_sellers: ChartPoint[]
  expense_breakdown: ChartPoint[]
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
