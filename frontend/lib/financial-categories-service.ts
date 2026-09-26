// Fetch helper for financial categories (master data for the manual-entries UI).
//
// Backend: GET /api/v1/financial-categories/
//   Query params: filter[is_active]=<bool>, entry_type=<income|expense>
// Response: MetaEnvelope[FinancialCategoryResponse] -> { data: [...], pagination: {...} }
//
// IMPORTANT: The OpenAPI spec documents `active_only` as the query param,
// but the actual backend route handler uses `filter[is_active]`.
// The code is authoritative (per audit finding #2).

import { api } from '@/lib/api-client'
import type {
  FinancialCategoryResponse,
  FinancialEntryType,
} from '@/lib/finance-types'

interface FinancialCategoryListEnvelope {
  data: FinancialCategoryResponse[]
  pagination: {
    page: number
    per_page: number
    total: number
    total_pages: number
  }
}

// filter[is_active] is the actual implemented param (not active_only).
// entryType is optional — when omitted, all active categories are returned.
export async function fetchFinancialCategories(
  entryType?: FinancialEntryType,
  options?: { signal?: AbortSignal }
): Promise<FinancialCategoryResponse[]> {
  // api.get returns the JSON data directly (not ApiResult wrapper)
  const resp = await api.get<FinancialCategoryListEnvelope>(
    '/financial-categories/',
    {
      // Backend uses filter[is_active] alias, NOT active_only (OpenAPI lag).
      params: {
        'filter[is_active]': 'true',
        ...(entryType ? { entry_type: entryType } : {}),
      },
      signal: options?.signal,
    }
  )
  return resp.data ?? []
}
