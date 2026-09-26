// ----------------------------------------------------------------------------
// Backend contract mirrors for the Notifications feature (F.8).
//
// Source of truth = backend/app/api/v1/notifications.py +
// backend/app/validation/notifications_schemas.py + openapi.yaml (§15.19).
//
// The frontend receives `message` already composed by the backend from the
// DB `title` + `body` (space-joined). Do not reconstruct title/body here.
// ----------------------------------------------------------------------------

/** Mirrors openapi.yaml Notification.type enum (line 9258). */
export type NotificationType =
  | 'low_stock'
  | 'out_of_stock'
  | 'below_cost_sale'
  | 'system'
  | 'action_confirmation'

/** Mirrors openapi.yaml Notification schema (line 9244). */
export interface Notification {
  id: number
  type: NotificationType
  message: string
  related_entity_type: string | null
  related_entity_id: number | null
  is_read: boolean
  created_at: string
}

/** Mirrors openapi.yaml Pagination (line 6413). */
export interface NotificationPagination {
  page: number
  per_page: number
  total: number
  total_pages: number
}

/** Mirrors openapi.yaml listNotifications response envelope. */
export interface NotificationListResponse {
  data: Notification[]
  pagination: NotificationPagination
  links?: {
    self?: string | null
    next?: string | null
    prev?: string | null
  }
}

/** Filter shape the service passes to the backend. */
export interface NotificationFilters {
  page?: number
  per_page?: number
  sort?: string
  q?: string
  from?: string
  to?: string
  is_read?: boolean | null
}
