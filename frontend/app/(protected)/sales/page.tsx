'use client'

// Release candidate (Option C2): client-side data fetching for the sales list.
// Mirrors the previous SSR implementation exactly: parallel GET /sales and
// GET /contacts (customer filter), same params, same 401 handling. The table
// client syncs from `initial*` props; URL changes and onRefresh drive refetches.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { api } from '@/lib/api-client'
import type { ApiError } from '@/lib/api-client'
import type { Contact, ContactListResponse } from '@/lib/contact-types'
import type { Sale, SaleListResponse } from '@/lib/sale-types'
import { SalesTable } from './sales-table-client'

const FALLBACK_PAGINATION = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
}

export default function SalesPage() {
  const searchParams = useSearchParams()
  const [sales, setSales] = useState<Sale[]>([])
  const [pagination, setPagination] = useState(FALLBACK_PAGINATION)
  const [customers, setCustomers] = useState<Contact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const page = searchParams.get('page') || '1'
  const perPage = searchParams.get('per_page') || '25'
  const lifecycleStatus = searchParams.get('lifecycle_status') || ''
  const customerId = searchParams.get('customer_id') || ''
  const paymentState = searchParams.get('payment_state') || ''
  const fromIso = searchParams.get('from_iso') || ''
  const toIso = searchParams.get('to_iso') || ''
  const q = searchParams.get('q') || ''

  const requestSeq = useRef(0)

  const fetchSales = useCallback(async () => {
    const seq = ++requestSeq.current
    setLoading(true)
    setError(null)
    try {
      const [salesRes, contactsRes] = await Promise.allSettled([
        api.get<SaleListResponse>('/sales', {
          params: {
            page: page || '1',
            per_page: perPage || '25',
            sort: searchParams.get('sort') || 'id',
            'filter[lifecycle_status]': lifecycleStatus || undefined,
            'filter[customer_id]': customerId || undefined,
            'filter[payment_state]': paymentState || undefined,
            from_iso: fromIso || undefined,
            to_iso: toIso || undefined,
            q: q || '',
          },
        }),
        api.get<ContactListResponse>('/contacts', {
          params: { 'filter[type]': 'customer', per_page: '500', sort: 'name' },
        }),
      ])

      if (seq !== requestSeq.current) return

      const s = salesRes.status === 'fulfilled' ? salesRes.value : null
      setSales(s ? s.data || [] : [])
      setPagination(s ? s.pagination : FALLBACK_PAGINATION)
      setCustomers(contactsRes.status === 'fulfilled' ? contactsRes.value.data || [] : [])

      if (salesRes.status === 'rejected') {
        const reason = salesRes.reason
        if (reason instanceof Error && (reason as ApiError).status === 401) {
          window.location.href = '/login'
          return
        }
        setError(reason instanceof Error ? reason.message : 'Failed to load sales')
        return
      }

      // A rejected contacts request must not degrade into an empty customer
      // dropdown: match the previous unauthorized-redirect contract.
      if (contactsRes.status === 'rejected') {
        const reason = contactsRes.reason
        if (reason instanceof Error && (reason as ApiError).status === 401) {
          window.location.href = '/login'
        }
      }
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }, [page, perPage, lifecycleStatus, customerId, paymentState, fromIso, toIso, q, searchParams])

  useEffect(() => {
    fetchSales()
  }, [fetchSales])

  if (error) {
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    )
  }

  return (
    <SalesTable
      initialSales={sales}
      initialPagination={pagination}
      initialCustomers={customers}
      onRefresh={fetchSales}
    />
  )
}
