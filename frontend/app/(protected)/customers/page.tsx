'use client'

// Release candidate (Option C2): client-side data fetching for the customers
// list. The table client (CustomersTable) keeps its SSR-era props contract:
// it syncs local state from `initial*` props via useEffect and drives URL
// changes through router.replace; this page refetches whenever the query
// string changes (and via onRefresh when a mutation resets to the base URL).
// Endpoints/params mirror the previous SSR implementation exactly
// (GET /contacts with type=customer filter).

import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { api } from '@/lib/api-client'
import type { ApiError } from '@/lib/api-client'
import type { Contact, ContactListResponse, Pagination } from '@/lib/contact-types'
import { CustomersTable } from './customers-table-client'

const FALLBACK_PAGINATION: Pagination = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
}

export default function CustomersPage() {
  const searchParams = useSearchParams()
  const [customers, setCustomers] = useState<Contact[]>([])
  const [pagination, setPagination] = useState<Pagination>(FALLBACK_PAGINATION)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const page = searchParams.get('page') || '1'
  const q = searchParams.get('q') || ''
  const status = searchParams.get('status') || ''
  const sort = searchParams.get('sort') || ''
  const dir = searchParams.get('dir') || ''

  const requestSeq = useRef(0)

  const fetchCustomers = useCallback(async () => {
    const seq = ++requestSeq.current
    setLoading(true)
    setError(null)
    try {
      const response = await api.get<ContactListResponse>('/contacts', {
        params: {
          page: page || '1',
          per_page: '25',
          sort: (sort && (dir === 'desc' ? `-${sort}` : sort)) || 'name',
          'filter[type]': 'customer',
          q: q || '',
          'filter[is_active]': status === 'active' ? 'true' : status === 'inactive' ? 'false' : undefined,
        },
      })
      if (seq !== requestSeq.current) return
      setCustomers(response.data || [])
      setPagination(response.pagination as Pagination)
    } catch (err) {
      if (seq !== requestSeq.current) return
      if (err instanceof Error && (err as ApiError).status === 401) {
        window.location.href = '/login'
        return
      }
      setError(err instanceof Error ? err.message : 'Failed to load customers')
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }, [page, q, status, sort, dir])

  useEffect(() => {
    fetchCustomers()
  }, [fetchCustomers])

  if (error) {
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    )
  }

  return (
    <CustomersTable
      initialCustomers={customers}
      initialPagination={pagination}
      onRefresh={fetchCustomers}
    />
  )
}
