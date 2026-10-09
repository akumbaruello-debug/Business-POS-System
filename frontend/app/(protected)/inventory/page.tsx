import { Suspense } from 'react'
import { redirect } from 'next/navigation'
import { serverApi, isUnauthorizedError } from '@/lib/server-api-client'
import type { InventorySummary, InventorySummaryStats } from '@/lib/inventory-types'
import { InventoryTable } from './inventory-table-client'

export const revalidate = 0
export const dynamic = 'force-dynamic'

interface Props {
  searchParams: Promise<{
    page?: string
    per_page?: string
    q?: string
    sort?: string
    dir?: string
    'filter[is_active]'?: string
    'filter[stock_status]'?: string
  }>
}

export default function InventoryPage({ searchParams }: Props) {
  return (
    <Suspense fallback={<InventorySkeleton />}>
      <InventoryDataTable searchParams={searchParams} />
    </Suspense>
  )
}

async function InventoryDataTable({ searchParams }: Props) {
  const params = await searchParams
  try {
    const response = await serverApi.get<{
    data: InventorySummary[]
    pagination: { page: number; per_page: number; total: number; total_pages: number; has_next: boolean; has_prev: boolean }
    summary: InventorySummaryStats
  }>('/inventory', {
    params: {
      page: params.page || '1',
      per_page: params.per_page || '25',
      sort: params.dir === 'desc' && params.sort ? `-${params.sort}` : params.sort || 'product_id',
      q: params.q || '',
      'filter[is_active]': params['filter[is_active]'] || undefined,
      'filter[stock_status]': params['filter[stock_status]'] || undefined,
    },
  })

  return (
    <InventoryTable
      initialRows={response.data}
      initialPagination={response.pagination}
      initialSummary={response.summary}
    />
  )
  } catch (error) {
    if (isUnauthorizedError(error)) redirect('/login')
    throw error
  }
}

function InventorySkeleton() {
  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Inventory</div>
          <h1>Inventory</h1>
          <p>Track stock levels and product availability</p>
        </div>
        <div className="heading-actions" />
      </div>
      <section className="metrics">
        {Array.from({ length: 4 }).map((_, i) => (
          <article key={i} className="metric-card">
            <div className="metric-value">—</div>
          </article>
        ))}
      </section>
      <section className="panel">
        <div className="panel-header">
          <div style={{ height: 36, borderRadius: 6, background: '#f0f4f9' }} />
        </div>
        <div style={{ padding: 20 }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} style={{ height: 44, borderRadius: 6, background: '#f0f4f9', marginBottom: 8 }} />
          ))}
        </div>
      </section>
    </div>
  )
}
