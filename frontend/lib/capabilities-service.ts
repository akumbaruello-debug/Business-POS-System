import { api } from './api-client'

export interface Capability {
  code: string
  name: string
  description?: string | null
  domain?: string | null
  category?: string | null
}

export interface CapabilityFilters {
  q?: string
  domain?: string
}

function unwrapList<T>(res: unknown): T[] {
  if (res && typeof res === 'object' && 'data' in res && Array.isArray((res as { data: unknown }).data)) {
    return (res as { data: T[] }).data
  }
  return res as T[]
}

export async function listCapabilities(filters?: CapabilityFilters): Promise<Capability[]> {
  const params: Record<string, string> = {}
  if (filters?.q) params.q = filters.q
  if (filters?.domain) params.domain = filters.domain
  const res = await api.get<unknown>('/capabilities', { params })
  return unwrapList<Capability>(res)
}
