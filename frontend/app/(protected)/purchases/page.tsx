'use client'

// Release candidate (Option C2): client-side data fetching for the purchases
// list. Mirrors the previous SSR implementation exactly: parallel
// GET /purchases + supplier/product enrichment, same params and defaults
// (sort -purchase_date), same graceful per-request degradation, same 401
// handling. PurchasesDataTable syncs from `initial*` props; URL changes and
// onRefresh drive refetches.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { api } from '@/lib/api-client'
import type { ApiError } from '@/lib/api-client'
import type { Pagination, Purchase, PurchaseListResponse } from '@/lib/purchase-types'
import type { ContactListResponse, Contact } from '@/lib/contact-types'
import type { Product } from '@/lib/product-types'
import PurchasesDataTable from './purchases-table-client'

const DEFAULT_PAGINATION: Pagination = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
}

export default function PurchasesPage() {
  const searchParams = useSearchParams()
  const [purchases, setPurchases] = useState<Purchase[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)
  const [suppliers, setSuppliers] = useState<Contact[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const page = searchParams.get('page') || '1'
  const perPage = searchParams.get('per_page') || '25'
  const sort = searchParams.get('sort') || '-purchase_date'
  const q = searchParams.get('q') || ''
  const filterLifecycleStatus = searchParams.get('filter[lifecycle_status]') || ''
  const filterPaymentState = searchParams.get('filter[payment_state]') || ''
  const filterSupplierId = searchParams.get('filter[supplier_id]') || ''
  const from = searchParams.get('from') || ''
  const to = searchParams.get('to') || ''

  const requestSeq = useRef(0)

  const fetchPurchases = useCallback(async () => {
    const seq = ++requestSeq.current
    setLoading(true)
    setError(null)
    try {
      const [purchasesRes, suppliersRes, productsRes] = await Promise.all([
        api
          .get<PurchaseListResponse>('/purchases', {
            params: {
              page: page || '1',
              per_page: perPage || '25',
              sort,
              q: q || undefined,
              'filter[lifecycle_status]': filterLifecycleStatus || undefined,
              'filter[payment_state]': filterPaymentState || undefined,
              'filter[supplier_id]': filterSupplierId || undefined,
              from: from || undefined,
              to: to || undefined,
            },
          })
          .then((r) => ({ data: r.data, pagination: r.pagination })),
        api
          .get<ContactListResponse>('/contacts', {
            params: { 'filter[type]': 'supplier', per_page: '500' },
          })
          .then((r) => r.data)
          .catch((err: unknown) => {
            if (err instanceof Error && (err as ApiError).status === 401) throw err
            return [] as ContactListResponse['data']
          }),
        api
          .get<{ data: Product[] }>('/products', {
            params: { 'filter[is_purchasable]': 'true', per_page: '500' },
          })
          .then((r) => r.data)
          .catch((err: unknown) => {
            if (err instanceof Error && (err as ApiError).status === 401) throw err
            return [] as Product[]
          }),
      ])

      if (seq !== requestSeq.current) return
      setPurchases(purchasesRes.data)
      setPagination(purchasesRes.pagination)
      setSuppliers(suppliersRes)
      setProducts(productsRes)
    } catch (err) {
      if (seq !== requestSeq.current) return
      if (err instanceof Error && (err as ApiError).status === 401) {
        window.location.href = '/login'
        return
      }
      setError(err instanceof Error ? err.message : 'Failed to load purchases')
    } finally {
      if (seq === requestSeq.current) setLoading(false)
    }
  }, [page, perPage, sort, q, filterLifecycleStatus, filterPaymentState, filterSupplierId, from, to])

  useEffect(() => {
    fetchPurchases()
  }, [fetchPurchases])

  if (error) {
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    )
  }

  return (
    <PurchasesDataTable
      initialPurchases={purchases}
      initialPagination={pagination}
      initialSuppliers={suppliers}
      initialProducts={products}
      onRefresh={fetchPurchases}
    />
  )
}
