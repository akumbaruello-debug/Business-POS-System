// ----------------------------------------------------------------------------
// Backend contract mirrors for the Manual Finance Entries feature (E.8).
//
// Source of truth = backend/app/api/v1/manual_entries.py + schemas.py
// (NOT the stale openapi.yaml / Database-Design-V1.0 descriptions that
//  were audited and flagged as contradictory).
//
// Field names below match the Pydantic response models exactly:
//   - ManualEntryResponse uses `notes` (not `description`/`reference_number`)
//   - `lifecycle_status` (not `status`)
//   - `amount` is a float on both request & response (Pydantic float, gt=0)
//   - `entry_date` is a datetime (optional on create, defaults to now)
//   - ETag is returned via HTTP `ETag` header on GET/{id}; the same value
//     is also embedded as `etag` in that response body.
// ----------------------------------------------------------------------------

// Mirrors backend FinancialEntryType (enums.py)
export type FinancialEntryType = 'income' | 'expense'

// Lifecycle values — mirrors the DB enum `manual_entry_status`.
// Values: 'posted' | 'cancelled'
export type ManualEntryLifecycleStatus = 'posted' | 'cancelled'

// Mirrors backend ManualEntryRequest (schemas.py)
export interface ManualEntryRequest {
  category_id: number
  entry_type: FinancialEntryType
  amount: number          // float, gt=0, le=10000000000000
  payment_method_id: number
  entry_date?: string     // datetime ISO string — optional, backend defaults to now
  notes?: string | null   // max 2000 chars
}

// Mirrors backend ManualEntryCancelRequest (schemas.py)
export interface ManualEntryCancelRequest {
  reason: string           // min 1, max 1000
}

// Mirrors backend ManualEntryResponse (schemas.py)
export interface ManualEntryResponse {
  id: number
  category_id: number
  category_name?: string | null
  entry_type: FinancialEntryType
  amount: number          // float
  payment_method_id: number
  entry_date: string      // datetime ISO
  notes?: string | null
  lifecycle_status: ManualEntryLifecycleStatus
  cancellation_date?: string | null
  cancellation_reason?: string | null
  cancelled_by?: number | null
  created_at: string      // datetime ISO
  created_by: number
}

// Mirrors backend FinancialCategoryResponse (schemas.py)
export interface FinancialCategoryResponse {
  id: number
  code: string
  name: string
  entry_type: FinancialEntryType
  is_active: boolean
  created_at: string
  updated_at: string
}

// Mirrors backend PaymentMethodResponse (schemas.py)
export interface PaymentMethodResponse {
  id: number
  code: string
  name: string
  is_cash: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

// Mirrors backend MetaEnvelope (validation/pagination.py)
export interface ManualEntryListResponse {
  data: ManualEntryResponse[]
  pagination: {
    page: number
    per_page: number
    total: number
    total_pages: number
  }
}

// Convenient aliases reused by other finance modules
export type ManualEntry = ManualEntryResponse
export type FinancialCategory = FinancialCategoryResponse
export type PaymentMethod = PaymentMethodResponse
