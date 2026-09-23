// -----------------------------------------------------------------------------
// Reports API contract — mirrors backend/app/api/v1/reports.py + schemas.py.
//
// Sources of truth:
//   - backend/app/reports/schemas.py
//   - backend/app/validation/exports_schemas.py
//   - openapi.yaml §12 /reports/*
//
// The backend returns two shapes:
//   1) AggregateReportResponse<T> — { data: T, comparison?: PeriodComparison }
//      Used by /reports/sales, /reports/p-and-l.
//   2) PagedReportResponse<T> — { data: T[], pagination: Pagination, comparison?: PeriodComparison }
//      Used by /reports/purchases, /reports/inventory-movements, /reports/sales-returns,
//      /reports/purchase-returns, /reports/production, /reports/manual-income,
//      /reports/manual-expense, /reports/refunds, /reports/supplier-repayments,
//      /reports/cash-flow, /reports/receivables, /reports/payables,
//      /reports/supplier-receivables, /reports/customer-refund-liabilities.
//
// /reports/inventory returns { data: InventorySnapshot } (no comparison, no pagination).
// -----------------------------------------------------------------------------

export interface Pagination {
  page: number
  per_page: number
  total: number
  total_pages: number
}

export interface PeriodComparison {
  previous_period: { from: string; to: string }
  delta: Record<string, number>
  delta_pct: Record<string, number | null>
}

export interface AggregateReportResponse<T> {
  data: T
  comparison?: PeriodComparison | null
}

export interface PagedReportResponse<T> {
  data: T[]
  pagination: Pagination
  comparison?: PeriodComparison | null
}

export interface SalesReportData {
  revenue: number
  cogs: number
  gross_profit: number
  sales_count: number
  average_ticket: number
}

export type SalesReportResponse = AggregateReportResponse<SalesReportData>

export interface InventorySnapshot {
  total_inventory_value: number
  products_count: number
  low_stock_count: number
  out_of_stock_count: number
}

export type InventoryReportResponse = AggregateReportResponse<InventorySnapshot>

export interface PnLData {
  revenue: number
  cogs: number
  gross_profit: number
  other_income: number
  operating_expenses: number
  net_profit: number
}

export type PnLReportResponse = AggregateReportResponse<PnLData>

/** Untyped row for list-based reports. Backend row shapes are stable but vary per report. */
export type ReportRow = Record<string, unknown>

export type GenericPagedReportResponse = PagedReportResponse<ReportRow>

/** Report selector options — one entry per backend /reports/* endpoint. */
export type ReportKind =
  | 'sales'
  | 'purchases'
  | 'inventory'
  | 'inventory-movements'
  | 'sales-returns'
  | 'purchase-returns'
  | 'production'
  | 'manual-income'
  | 'manual-expense'
  | 'refunds'
  | 'supplier-repayments'
  | 'cash-flow'
  | 'p-and-l'
  | 'receivables'
  | 'payables'
  | 'supplier-receivables'
  | 'customer-refund-liabilities'

export interface ReportOption {
  kind: ReportKind
  label: string
  /** Capability required beyond report.view. */
  extraCapability?: 'finance.view_profit' | 'finance.view_payables_receivables'
}

export const REPORT_OPTIONS: ReportOption[] = [
  { kind: 'sales', label: 'Sales' },
  { kind: 'purchases', label: 'Purchases' },
  { kind: 'inventory', label: 'Inventory' },
  { kind: 'inventory-movements', label: 'Inventory movements' },
  { kind: 'sales-returns', label: 'Sales returns' },
  { kind: 'purchase-returns', label: 'Purchase returns' },
  { kind: 'production', label: 'Production' },
  { kind: 'manual-income', label: 'Manual income' },
  { kind: 'manual-expense', label: 'Manual expense' },
  { kind: 'refunds', label: 'Refunds' },
  { kind: 'supplier-repayments', label: 'Supplier repayments' },
  { kind: 'cash-flow', label: 'Cash flow' },
  { kind: 'p-and-l', label: 'P&L', extraCapability: 'finance.view_profit' },
  { kind: 'receivables', label: 'Receivables', extraCapability: 'finance.view_payables_receivables' },
  { kind: 'payables', label: 'Payables', extraCapability: 'finance.view_payables_receivables' },
  { kind: 'supplier-receivables', label: 'Supplier receivables', extraCapability: 'finance.view_payables_receivables' },
  { kind: 'customer-refund-liabilities', label: 'Customer refund liabilities', extraCapability: 'finance.view_payables_receivables' },
] as const

export const PERIOD_OPTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'this_week', label: 'This week' },
  { value: 'this_month', label: 'This month' },
  { value: 'this_year', label: 'This year' },
  { value: 'custom', label: 'Custom range' },
] as const

export type PeriodValue = (typeof PERIOD_OPTIONS)[number]['value']

export const COMPARE_OPTIONS = [
  { value: 'none', label: 'No comparison' },
  { value: 'today', label: 'vs previous day' },
  { value: 'this_week', label: 'vs previous week' },
  { value: 'this_month', label: 'vs previous month' },
  { value: 'this_year', label: 'vs previous year' },
] as const

export type CompareValue = (typeof COMPARE_OPTIONS)[number]['value']

/** Export job contract (mirrors backend/app/validation/exports_schemas.py). */
export interface ExportJob {
  export_id: string
  status: 'queued' | 'processing' | 'complete' | 'failed'
  download_url: string | null
  expires_at: string | null
  error: string | null
  created_at: string
}
