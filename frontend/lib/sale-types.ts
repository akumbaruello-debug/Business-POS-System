// -----------------------------------------------------------------------------
// Backend contract mirrors for the Sales feature.
//
// Source of truth = backend/app/validation/sales_schemas.py (B.8) + the
// backend/app/api/v1/sales.py route signatures. The OpenAPI YAML at the repo
// root lags the route handler in places (it lists `filter[...]`, `sort`,
// `from_iso`/`to_iso` params and `?include=` sub-resource embedding that the
// current FastAPI handler does not expose). We follow the actual handler:
//   GET  /sales                    → { data: Sale[], pagination }
//   GET  /sales/{id}               → Sale   (NO embedded lines/payments/returns)
//   GET  /sales/{id}/lines         → { data: SaleLine[], pagination? }
//   GET  /sales/{id}/payments      → { data: SalePayment[], pagination? }
//   GET  /sales/{id}/returns       → { data: SalesReturn[], pagination? }
//
// Server-computed fields (payment_state, paid_amount, outstanding, ar, crl,
// lifecycle_status, total_amount, etag, version) are emitted by the service
// layer's _enrich_sale on every read. The frontend must not recompute them.
// -----------------------------------------------------------------------------

export interface Pagination {
  page: number
  per_page: number
  total: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
}

export interface SaleLine {
  id: number
  sale_id: number
  product_id: number
  quantity: number
  unit_price: number
  discount_amount: number
  line_total: number
  unit_cost_snapshot?: number | null
  cogs_total_snapshot?: number | null
  is_negative_stock_fallback?: boolean
  stock_movement_id?: number | null
  line_number: number
}

export interface SalePayment {
  id: number
  sale_id: number
  payment_method_id: number
  amount: number
  tendered_amount?: number | null
  change_amount?: number | null
  payment_date: string
  reference?: string | null
  created_at: string
  created_by: number
}

export interface SalesReturnLine {
  id: number
  sales_return_id: number
  sale_line_id: number
  product_id: number
  quantity: number
  returned_selling_price: number
  returned_unit_cost: number
  line_value: number
  line_number: number
}

export interface SalesReturn {
  id: number
  sale_id: number
  return_date: string
  reason?: string | null
  total_selling_price_returned: number
  total_cost_returned: number
  lifecycle_status: 'posted' | 'cancelled'
  lines: SalesReturnLine[]
  created_at: string
  created_by: number
}

/** Server-enriched contact summary (G4). Shape mirrors the subset of the
 *  `contacts` row that SaleService._enrich_sale projects. Present on list +
 *  detail reads whenever `customer_id` resolves; `null` for walk-in sales. */
export interface SaleCustomer {
  id: number
  name: string | null
  type?: string | null
  email?: string | null
  phone?: string | null
  address?: string | null
  is_active?: boolean | null
}

export interface Sale {
  id: number
  reference_no: string | null
  customer_id: number | null
  customer?: SaleCustomer | null
  sale_date: string
  /** List rows serialize NUMERIC as a string ("485000.00"); detail returns a
   *  number. Coerce with Number()/num() before arithmetic. */
  total_amount: number | string
  discount_amount: number | string
  lifecycle_status:
    | 'draft'
    | 'posted'
    | 'completed'
    | 'partially_returned'
    | 'returned'
    | 'cancelled'
  payment_state: 'unpaid' | 'partial' | 'paid'
  paid_amount: number
  outstanding: number
  ar: number
  crl: number
  cancellation_date?: string | null
  cancellation_reason?: string | null
  cancelled_by?: number | null
  posted_at?: string | null
  posted_by?: number | null
  notes?: string | null
  created_at: string
  updated_at: string
  created_by: number
  version: number
  // Server-emitted (set by SaleService._enrich_sale when listing):
  etag?: string
}

export interface SaleListResponse {
  data: Sale[]
  pagination: Pagination
}

// -----------------------------------------------------------------------------
// Request payloads (mirrors app/validation/sales_schemas.py).
// -----------------------------------------------------------------------------

export interface SaleLineInput {
  product_id: number
  quantity: number
  unit_price?: number | null
  discount_amount?: number | null
}

export interface SaleLinePatch {
  quantity?: number
  unit_price?: number | null
  discount_amount?: number | null
}

export interface SaleCreateRequest {
  customer_id?: number | null
  sale_date?: string | null
  discount_amount?: number
  notes?: string | null
  reference_no?: string | null
  lines: SaleLineInput[]
}

export interface SalePatch {
  customer_id?: number | null
  sale_date?: string | null
  discount_amount?: number
  notes?: string | null
}

export interface SalePaymentInput {
  payment_method_id: number
  amount: number
  tendered_amount?: number | null
  change_amount?: number | null
  payment_date?: string | null
  reference?: string | null
}

export interface SaleCancelRequest {
  reason: string
}

export interface SaleReturnLineRequest {
  sale_line_id: number
  quantity: number
}

export interface SaleReturnRequest {
  reason: string
  lines: SaleReturnLineRequest[]
}

// -----------------------------------------------------------------------------
// Refunds (M4 / Phase D.1)
// GET /refunds, POST /refunds, GET /refunds/{id}
// -----------------------------------------------------------------------------

export interface Refund {
  id: number
  sale_id: number
  amount: number
  payment_method_id: number
  refund_date: string
  reason?: string | null
  refundable_amount_snapshot: number
  created_at: string
  created_by: number
}

export interface RefundRequest {
  sale_id: number
  amount: number
  payment_method_id: number
  refund_date?: string | null
  reason?: string | null
}

export interface RefundListResponse {
  data: Refund[]
  pagination: Pagination
}

// -----------------------------------------------------------------------------
// Payment methods (capability payment_method.view or auth scope).
// -----------------------------------------------------------------------------

export interface PaymentMethod {
  id: number
  code?: string
  name: string
  kind?: string
  is_active?: boolean
  is_cash?: boolean
  allows_change?: boolean
}