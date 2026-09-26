import { api } from './api-client'
import type { ApiError, ApiResult } from './api-client'

export interface UserListItem {
  id: number
  username: string
  full_name: string
  email?: string | null
  is_active: boolean
  role_id: number
  role_name: string
  last_login_at?: string | null
  failed_login_attempts?: number
  locked_until?: string | null
  created_at?: string
  updated_at?: string
  version?: number
}

export interface UserCapabilities {
  role: string
  role_capabilities: string[]
  overrides: Array<{ capability_code: string; is_granted: boolean; granted_at?: string; granted_by?: number }>
  effective: string[]
}

export interface UserDetail extends UserListItem {
  is_locked?: boolean
  etag?: string
}

export interface CreateUserBody {
  username: string
  full_name: string
  email?: string | null
  password: string
  role_id: number
}

/** @deprecated use CreateUserBody */
export type UserCreateBody = CreateUserBody

export interface UserUpdateBody {
  full_name?: string
  email?: string | null
  role_id?: number | null
  is_active?: boolean
}

export interface UserFilters {
  q?: string
  active_only?: boolean | number
  role_id?: number
  sort?: string
  page?: number
  page_size?: number
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface DeactivateBody {
  reason?: string
}

function unwrapList<T>(res: unknown): T[] {
  if (res && typeof res === 'object' && 'data' in res && Array.isArray((res as { data: unknown }).data)) {
    return (res as { data: T[] }).data
  }
  return res as T[]
}

interface PaginationEnvelope {
  page: number
  per_page: number
  total: number
  total_pages: number
}

function unwrapPagination(res: unknown): PaginationEnvelope | null {
  if (res && typeof res === 'object' && 'pagination' in res) {
    const p = (res as { pagination: unknown }).pagination
    if (p && typeof p === 'object') return p as PaginationEnvelope
  }
  return null
}

/** Canonical lock rule (backend auth/password.py): locked while locked_until is in the future. */
export function isUserLocked(u: { locked_until?: string | null }): boolean {
  if (!u.locked_until) return false
  const ts = new Date(u.locked_until).getTime()
  return Number.isFinite(ts) && ts > Date.now()
}

export async function listUsers(filters?: UserFilters): Promise<Paginated<UserListItem>> {
  // Param names mirror the backend contract exactly: per_page (not page_size)
  // and filter[is_active] / filter[role_id] (not active_only / role_id).
  const params: Record<string, string> = {}
  if (filters?.q) params.q = filters.q
  if (filters?.active_only !== undefined) params['filter[is_active]'] = String(filters.active_only)
  if (filters?.role_id !== undefined) params['filter[role_id]'] = String(filters.role_id)
  if (filters?.sort) params.sort = filters.sort
  if (filters?.page !== undefined) params.page = String(filters.page)
  if (filters?.page_size !== undefined) params.per_page = String(filters.page_size)
  const res = await api.get<unknown>('/users', { params })
  const items = unwrapList<UserListItem>(res)
  const pagination = unwrapPagination(res)
  return {
    items,
    total: pagination?.total ?? items.length,
    page: pagination?.page ?? filters?.page ?? 1,
    page_size: pagination?.per_page ?? items.length,
  }
}

export async function getUser(id: number): Promise<UserDetail> {
  const res = await api.headers.get<UserDetail>(`/users/${id}`)
  const u = res.data
  return {
    ...u,
    etag: res.headers.get('etag') ?? `"${u.updated_at ?? u.created_at ?? ''}"`,
  }
}

export async function createUser(body: CreateUserBody): Promise<UserDetail> {
  return api.post<UserDetail>('/users', body, { idempotencyKey: true })
}

export async function updateUser(
  id: number,
  etag: string,
  body: UserUpdateBody
): Promise<UserDetail> {
  return api.patch<UserDetail>(`/users/${id}`, body, { ifMatch: etag, idempotencyKey: true })
}

export async function deactivateUser(id: number, reason?: string): Promise<UserDetail> {
  return api.post<UserDetail>(`/users/${id}/deactivate`, { reason }, { idempotencyKey: true })
}

export async function activateUser(id: number, etag: string): Promise<UserDetail> {
  return updateUser(id, etag, { is_active: true })
}

export async function unlockUser(id: number): Promise<UserDetail> {
  return api.post<UserDetail>(`/users/${id}/unlock`, {}, { idempotencyKey: true })
}

export async function resetPassword(id: number, newPassword: string): Promise<UserDetail> {
  return api.post<UserDetail>(`/users/${id}/reset-password`, { new_password: newPassword }, { idempotencyKey: true })
}

export async function getUserCapabilities(id: number): Promise<UserCapabilities> {
  const res = await api.get<unknown>(`/users/${id}/capabilities`)
  if (res && typeof res === 'object' && 'data' in res) {
    return (res as { data: UserCapabilities }).data
  }
  return res as UserCapabilities
}

export async function grantCapability(id: number, etag: string, capability: string): Promise<UserDetail> {
  return api.post<UserDetail>(`/users/${id}/capabilities`, { capability_code: capability, is_granted: true }, { ifMatch: etag, idempotencyKey: true })
}

export async function revokeCapability(id: number, capability: string): Promise<void> {
  return api.delete<void>(`/users/${id}/capabilities/${capability}`, { idempotencyKey: true })
}

export { ApiError, type ApiResult }
