// Fetch helper for payment methods (master data for the manual-entries UI).
//
// Backend: GET /api/v1/payment-methods/  — list all payment methods.
// Response: MetaEnvelope[PaymentMethodResponse] -> { data: [...], pagination: {...} }
//
// Note: The payment-methods route uses `active_only` query param (this is
// correctly documented in OpenAPI unlike financial-categories which uses
// filter[is_active]). We fetch active methods since we only need to resolve
// display names for currently-selected payment methods.

import { api } from '@/lib/api-client'
import type { PaymentMethodResponse } from '@/lib/finance-types'

interface PaymentMethodListEnvelope {
  data: PaymentMethodResponse[]
  pagination: {
    page: number
    per_page: number
    total: number
    total_pages: number
  }
}

export async function fetchPaymentMethods(
  options?: { signal?: AbortSignal }
): Promise<PaymentMethodResponse[]> {
  // api.get returns the JSON data directly (not ApiResult)
  const resp = await api.get<PaymentMethodListEnvelope>(
    '/payment-methods/',
    {
      params: { active_only: true },
      signal: options?.signal,
    }
  )
  return resp.data ?? []
}

export function getPaymentMethodName(
  id: number,
  methods: PaymentMethodResponse[]
): string {
  return methods.find((m) => m.id === id)?.name ?? `#${id}`
}
