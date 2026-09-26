// Fetch helper for the Notifications feature (F.8).
//
// Backend: GET /notifications  — listNotifications (paginated)
// Backend: POST /notifications/{id}/mark-read — markNotificationRead (204)
// Backend: POST /notifications/mark-all-read — markAllNotificationsRead (200, { updated })
//
// Query key MUST be exactly `filter[is_read]`. The `api` client's `params`
// serializer passes the key through verbatim (no bracket escaping).

import { api } from '@/lib/api-client'
import type {
  Notification,
  NotificationFilters,
  NotificationListResponse,
} from '@/lib/notification-types'

/** Unwrap the `data` envelope from a paginated response. */
function unwrapData<T>(res: unknown): T[] {
  if (res && typeof res === 'object' && 'data' in res && Array.isArray((res as { data: unknown }).data)) {
    return (res as { data: T[] }).data
  }
  return res as T[]
}

export async function listNotifications(
  filters?: NotificationFilters,
): Promise<NotificationListResponse> {
  const params: Record<string, string> = {}
  if (filters?.page !== undefined) params.page = String(filters.page)
  if (filters?.per_page !== undefined) params.per_page = String(filters.per_page)
  if (filters?.sort) params.sort = filters.sort
  if (filters?.q) params.q = filters.q
  if (filters?.from) params.from = filters.from
  if (filters?.to) params.to = filters.to
  if (filters?.is_read !== undefined && filters.is_read !== null) {
    params['filter[is_read]'] = String(filters.is_read)
  }
  return api.get<NotificationListResponse>('/notifications', { params })
}

export async function markNotificationRead(id: number): Promise<void> {
  // Backend returns 204 No Content — api.post resolves to undefined.
  await api.post<void>(`/notifications/${id}/mark-read`)
}

export interface MarkAllReadResponse {
  updated: number
}

export async function markAllNotificationsRead(): Promise<MarkAllReadResponse> {
  return api.post<MarkAllReadResponse>('/notifications/mark-all-read')
}

// Re-export Notification type for convenience at call sites.
export type { Notification }
