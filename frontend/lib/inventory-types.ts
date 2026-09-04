/**
 * Inventory API types — mirrors openapi.yaml §Inventory.
 */

export interface Pagination {
  page: number
  per_page: number
  total: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
}

/** Shape returned by GET /inventory */
export interface InventorySummary {
  product_id: number
  on_hand_quantity: number
  moving_average_unit_cost: number | null
  inventory_value: number
  low_stock: boolean
  as_of: string
}

export interface InventoryListResponse {
  data: InventorySummary[]
  pagination: Pagination
  links?: Record<string, string>
}

/** Shape returned by GET /inventory/products/{id} */
export interface ProductStock {
  product_id: number
  on_hand_quantity: number
  moving_average_unit_cost: number | null
  inventory_value: number
  low_stock: boolean
  low_stock_threshold: number | null
  as_of: string
}

/** Shape returned by GET /stock-movements */
export interface StockMovement {
  id: number
  product_id: number
  movement_date: string
  trigger: string
  quantity: number
  unit_cost_at_movement: number | null
  total_cost: number | null
  reference_type: string | null
  reference_id: number | null
  reference_line_id: number | null
  reversal_of_movement_id: number | null
  reversed_by_movement_id: number | null
  reason: string | null
  created_at: string
  created_by: number
}

export interface StockMovementListResponse {
  data: StockMovement[]
  pagination: Pagination
  links?: Record<string, string>
}

/** Request body for POST /inventory/adjustments */
export interface StockAdjustmentRequest {
  product_id: number
  quantity: number
  reason: string
}

/** Minimal product identity for lookup (from GET /products) */
export interface ProductIdentity {
  id: number
  name: string
  code: string | null
  category_id: number | null
  unit_id: number | null
  low_stock_threshold: number | null
  is_active: boolean
  updated_at: string
  version: number
}
