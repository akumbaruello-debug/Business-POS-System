/**
 * Supplier detail page API types.
 *
 * Mirrors backend:
 * - SupplierRepayment → app/validation/purchases_schemas.py SupplierRepayment
 * - Purchase          → app/lib/purchase-types.ts Purchase
 * - MetaEnvelope      → shared pagination envelope
 */

import type { Pagination } from './contact-types'

export interface SupplierRepayment {
  id: number
  purchase_id: number
  purchase_return_id: number | null
  amount: number
  received_amount: number
  payment_method_id: number | null
  repayment_date: string
  reason: string | null
  refundable_amount_snapshot: number
  created_at: string
  created_by: number
}

export interface SupplierRepaymentsResponse {
  data: SupplierRepayment[]
  pagination: Pagination
  links?: Record<string, string>
}
