/**
 * Purchase API types — mirrors openapi.yaml §13 + backend app/validation/purchases_schemas.py
 * and service enrichment (total_amount, payment_state, etc. are server-derived).
 */

export interface Pagination {
  page: number
  per_page: number
  total: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
}

export interface PagedResponse<T> {
  data: T[]
  pagination: Pagination
  links?: Record<string, string>
}

export type LifecycleStatus =
  | 'draft'
  | 'posted'
  | 'completed'
  | 'partially_returned'
  | 'returned'
  | 'cancelled'

export type PaymentState = 'unpaid' | 'partial' | 'paid'

export interface PurchaseShipping {
  id: number
  purchase_id: number
  amount: number
  paid_in_cash: boolean
  supplier_id: number | null
  description: string | null
}

export interface PurchaseLine {
  id: number
  purchase_id: number
  product_id: number
  quantity: number
  unit_price: number
  line_subtotal: number
  allocated_shipping: number
  line_total: number
  line_number: number
}

export interface PurchasePayment {
  id: number
  purchase_id: number
  payment_method_id: number
  amount: number
  tendered_amount: number | null
  change_amount: number | null
  payment_date: string
  reference: string | null
  created_at: string
  created_by: number
}

export interface PurchaseReturnLine {
  id: number
  purchase_return_id: number
  purchase_line_id: number
  product_id: number
  quantity: number
  unit_cost_snapshot: number
  line_value: number
  line_number: number
}

export interface PurchaseReturn {
  id: number
  purchase_id: number
  return_date: string
  reason: string | null
  total_value_returned: number
  lifecycle_status: string
  lines: PurchaseReturnLine[]
  created_at: string
  created_by: number
  updated_at: string | null
}

export interface Purchase {
  id: number
  reference_no: string | null
  supplier_id: number | null
  purchase_date: string
  received_date: string | null
  lifecycle_status: LifecycleStatus
  posted_at: string | null
  posted_by: number | null
  cancellation_date: string | null
  cancellation_reason: string | null
  cancelled_by: number | null
  notes: string | null
  created_at: string
  updated_at: string
  created_by: number
  version: number
  // server-derived
  total_amount: number
  payment_state: PaymentState
  paid_amount: number
  outstanding: number
  ap: number
  supplier_receivable: number
  etag: string
  // expansions
  lines?: PurchaseLine[]
  shipping?: PurchaseShipping | null
  payments?: PurchasePayment[]
  returns?: PurchaseReturn[]
}

export interface PurchaseListResponse {
  data: Purchase[]
  pagination: Pagination
  links?: Record<string, string>
}
