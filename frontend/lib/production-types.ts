/**
 * Production API types — mirrors openapi.yaml §15 + backend validation schemas.
 *
 * Server-derived cost fields (finished_unit_cost, total_raw_cost,
 * total_overhead_cost, unit_cost_snapshot, line_cost, total_cost) are
 * never accepted in create/patch request bodies.
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

export type ProductionLifecycleStatus = 'draft' | 'posted' | 'completed' | 'cancelled'

export interface ProductionInput {
  id: number
  production_run_id: number
  product_id: number
  quantity: number
  unit_cost_snapshot: number
  line_cost: number
  line_number: number
}

export interface ProductionInputRequest {
  product_id: number
  quantity: number
}

export interface ProductionOutput {
  id: number
  production_run_id: number
  product_id: number
  quantity: number
  unit_cost_snapshot: number
  total_cost: number
}

export interface ProductionCostLine {
  id: number
  production_run_id: number
  cost_type_id: number
  description: string | null
  amount: number
  paid_in_cash: boolean
  line_number: number
}

export interface ProductionCostLineRequest {
  cost_type_id: number
  amount: number
  paid_in_cash?: boolean
  description?: string | null
}

export interface ProductionRun {
  id: number
  run_date: string
  output_product_id: number
  output_quantity: number
  finished_unit_cost: number
  total_raw_cost: number
  total_overhead_cost: number
  lifecycle_status: ProductionLifecycleStatus
  posted_at: string | null
  posted_by: number | null
  cancellation_date: string | null
  cancellation_reason: string | null
  cancelled_by: number | null
  notes: string | null
  inputs: ProductionInput[]
  output: ProductionOutput
  cost_lines: ProductionCostLine[]
  created_at: string
  updated_at: string
  created_by: number
  version: number
  etag?: string
}

export interface ProductionRunCreateRequest {
  run_date?: string | null
  output_product_id: number
  output_quantity: number
  notes?: string | null
  inputs: ProductionInputRequest[]
  cost_lines?: ProductionCostLineRequest[]
}

export interface ProductionRunPatch {
  run_date?: string | null
  output_product_id?: number
  output_quantity?: number
  notes?: string | null
}

export interface ProductionCancelRequest {
  reason: string
}

export interface ProductionRunListResponse {
  data: ProductionRun[]
  pagination: Pagination
  links?: Record<string, string>
}

export interface CostType {
  id: number
  code: string
  name: string
  is_active: boolean
  version: number
}

export interface CostTypeRequest {
  code: string
  name: string
  is_active?: boolean
}

export interface CostTypePatch {
  name?: string
  is_active?: boolean
}

export interface CostTypeListResponse {
  data: CostType[]
  pagination: Pagination
  links?: Record<string, string>
}

export interface ProductionReportRow {
  id: number
  run_date: string
  output_product_id: number
  output_quantity: number
  finished_unit_cost: number
  total_raw_cost: number
  total_overhead_cost: number
  lifecycle_status: ProductionLifecycleStatus
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
}
