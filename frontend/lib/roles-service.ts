import { api } from './api-client'
import type { ApiError, ApiResult } from './api-client'

export interface Role {
  id: number
  name: string
  description?: string | null
  is_system?: boolean
  is_system_role?: boolean
  capabilities?: string[]
  capability_codes?: string[]
  created_at?: string
  updated_at?: string
  etag?: string
}

export function isSystemRole(r: Role): boolean {
  return r.is_system_role ?? r.is_system ?? false
}

export function roleCapabilities(r: Role): string[] {
  return r.capability_codes ?? r.capabilities ?? []
}

export interface CreateRoleBody {
  name: string
  description?: string | null
  capabilities?: string[]
}

/** @deprecated use CreateRoleBody */
export type RoleCreateBody = CreateRoleBody

export interface RoleUpdateBody {
  name?: string
  description?: string | null
}

export interface RoleFilters {
  q?: string
  page?: number
  page_size?: number
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

function unwrapList<T>(res: unknown): T[] {
  if (res && typeof res === 'object' && 'data' in res && Array.isArray((res as { data: unknown }).data)) {
    return (res as { data: T[] }).data
  }
  return res as T[]
}

export async function listRoles(filters?: RoleFilters): Promise<Paginated<Role>> {
  const params: Record<string, string> = {}
  if (filters?.q) params.q = filters.q
  if (filters?.page !== undefined) params.page = String(filters.page)
  if (filters?.page_size !== undefined) params.page_size = String(filters.page_size)
  const res = await api.get<unknown>('/roles', { params })
  const items = unwrapList<Role>(res)
  return { items, total: items.length, page: filters?.page ?? 1, page_size: items.length }
}

export async function getRole(id: number): Promise<Role> {
  const res = await api.headers.get<Role>(`/roles/${id}`)
  const r = res.data
  return {
    ...r,
    etag: res.headers.get('etag') ?? `"${r.updated_at ?? r.created_at ?? ''}"`,
  }
}

export async function createRole(body: CreateRoleBody): Promise<Role> {
  return api.post<Role>('/roles', body, { idempotencyKey: true })
}

export async function updateRole(id: number, etag: string, body: RoleUpdateBody): Promise<Role> {
  return api.patch<Role>(`/roles/${id}`, body, { ifMatch: etag, idempotencyKey: true })
}

export async function replaceRoleCapabilities(id: number, etag: string, capability_codes: string[]): Promise<Role> {
  return api.put<Role>(`/roles/${id}/capabilities`, { capability_codes }, { ifMatch: etag, idempotencyKey: true })
}

export async function deleteRole(id: number, etag: string): Promise<void> {
  return api.delete<void>(`/roles/${id}`, { ifMatch: etag, idempotencyKey: true })
}

export { ApiError, type ApiResult }
