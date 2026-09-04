/**
 * Product API types — mirrors openapi.yaml §15.7 and backend validation schemas.
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

export interface Category {
  id: number
  name: string
  is_active: boolean
  version: number
  parent_id: number | null
  created_at: string
  updated_at: string
}

export interface Unit {
  id: number
  code: string
  name: string
  is_active: boolean
  version: number
  created_at: string
  updated_at: string
}

export interface Product {
  id: number
  name: string
  code: string | null
  category_id: number | null
  unit_id: number | null
  purchase_price: number
  selling_price: number
  low_stock_threshold: number | null
  allow_negative_stock: boolean | null
  notes: string | null
  is_sellable: boolean
  is_purchasable: boolean
  is_producible: boolean
  is_active: boolean
  created_at: string
  updated_at: string
  version: number
  on_hand_quantity: number
  moving_average_unit_cost: number | null
  inventory_value: number
  low_stock: boolean
  negative_stock_fallback_supported: boolean
  created_by: number | null
  updated_by: number | null
}

export interface ProductCreateRequest {
  name: string
  code?: string | null
  category_id?: number | null
  unit_id?: number | null
  purchase_price?: number
  selling_price?: number
  low_stock_threshold?: number | null
  allow_negative_stock?: boolean | null
  notes?: string | null
  is_sellable?: boolean
  is_purchasable?: boolean
  is_producible?: boolean
  is_active?: boolean
}

export type ProductPatch = Partial<ProductCreateRequest>

export interface DeactivateRequest {
  reason?: string | null
}
