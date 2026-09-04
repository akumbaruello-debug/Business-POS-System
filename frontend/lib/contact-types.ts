/**
 * Contacts API types — mirrors openapi.yaml §Contacts.
 */

export interface Pagination {
  page: number
  per_page: number
  total: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
}

export interface Contact {
  id: number
  type: 'customer' | 'supplier' | 'both'
  name: string
  phone: string | null
  email: string | null
  address: string | null
  notes: string | null
  is_active: boolean
  created_at: string
  updated_at: string
  version: number
}

export interface ContactListResponse {
  data: Contact[]
  pagination: Pagination
  links?: Record<string, string>
}
