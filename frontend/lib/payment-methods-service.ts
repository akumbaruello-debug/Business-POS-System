// Fetch helper for payment methods (master data for the manual-entries UI).
//
// Backend: GET /api/v1/payment-methods/  — list all payment methods.
// Response: MetaEnvelope[PaymentMethodResponse] -> { data: [...], pagination: {...} }
//
// The backend route handler (payment_methods.py line 92) uses the alias
// `filter[is_active]` for the `active_only` query parameter, NOT a bare
// `active_only` param (audit §7.2). We use the actual implemented alias.

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
  const resp = await api.get<PaymentMethodListEnvelope>(
    '/payment-methods/',
    {
      // Backend alias is filter[is_active], not active_only (audit §7.2).
      params: { 'filter[is_active]': 'true', per_page: '200' },
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
