import { Suspense } from 'react'
import { redirect } from 'next/navigation'
import { serverApi, isUnauthorizedError } from '@/lib/server-api-client'
import type { Pagination, Purchase, PurchaseListResponse } from '@/lib/purchase-types'
import type { ContactListResponse } from '@/lib/contact-types'
import type { Product } from '@/lib/product-types'
import PurchasesDataTable from './purchases-table-client'
import PurchasesSkeleton from './purchases-skeleton'

export const revalidate = 0
export const dynamic = 'force-dynamic'

const DEFAULT_PAGINATION: Pagination = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
}

// Server component: parallel fetch purchases + supplier/product enrichment.
// Cache key is URL-scoped (route + searchParams) — RSC never shares responses across routes.
async function fetchPurchasesData(search: string) {
  const url = new URLSearchParams(search)

  const params: Record<string, string> = {
    page: url.get('page') || '1',
    per_page: url.get('per_page') || '25',
    sort: url.get('sort') || '-purchase_date',
  }
  if (url.get('q')) params.q = url.get('q')!
  if (url.get('filter[lifecycle_status]')) params['filter[lifecycle_status]'] = url.get('filter[lifecycle_status]')!
  if (url.get('filter[payment_state]')) params['filter[payment_state]'] = url.get('filter[payment_state]')!
  if (url.get('filter[supplier_id]')) params['filter[supplier_id]'] = url.get('filter[supplier_id]')!
  if (url.get('from')) params.from = url.get('from')!
  if (url.get('to')) params.to = url.get('to')!

  const [purchasesRes, suppliersRes, productsRes] = await Promise.all([
    serverApi
      .get<PurchaseListResponse>('/purchases', { params })
      .then((r) => ({ data: r.data, pagination: r.pagination }))
      .catch((error) => {
        if (isUnauthorizedError(error)) throw error
        return { data: [] as Purchase[], pagination: DEFAULT_PAGINATION }
      }),
    serverApi
      .get<ContactListResponse>('/contacts', {
        params: { 'filter[type]': 'supplier', per_page: '500' },
      })
      .then((r) => r.data)
            .catch((error) => {
              if (isUnauthorizedError(error)) throw error
              return [] as ContactListResponse['data']
            }),
    serverApi
      .get<{ data: Product[] }>('/products', {
        params: { 'filter[is_purchasable]': 'true', per_page: '500' },
      })
      .then((r) => r.data)
            .catch((error) => {
              if (isUnauthorizedError(error)) throw error
              return [] as Product[]
            }),
  ])

  return {
    purchases: purchasesRes.data,
    pagination: purchasesRes.pagination,
    suppliers: suppliersRes,
    products: productsRes,
  }
}

export default async function PurchasesPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>
}) {
  const sp = await searchParams
  const search = Object.entries(sp)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(typeof v === 'string' ? v : v![0])}`)
    .join('&')

  try {
    const data = await fetchPurchasesData(search)

    return (
      <Suspense fallback={<PurchasesSkeleton />}>
        <PurchasesDataTable
          initialPurchases={data.purchases}
          initialPagination={data.pagination}
          initialSuppliers={data.suppliers}
          initialProducts={data.products}
        />
      </Suspense>
    )
  } catch (error) {
    if (isUnauthorizedError(error)) redirect('/login')
    throw error
  }
}
