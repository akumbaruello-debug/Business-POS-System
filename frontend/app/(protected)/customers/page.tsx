import { Suspense } from 'react'
import { redirect } from 'next/navigation'
import { serverApi, isUnauthorizedError } from '@/lib/server-api-client'
import type { Contact, ContactListResponse, Pagination } from '@/lib/contact-types'
import { CustomersTable } from './customers-table-client'

export const revalidate = 0
export const dynamic = 'force-dynamic'

interface Props {
  searchParams: Promise<{ page?: string; q?: string; status?: string; sort?: string; dir?: string }>
}

export default function CustomersPage({ searchParams }: Props) {
  // Page renders shell immediately, data fetch suspends in the Suspense boundary
  return (
    <Suspense fallback={<CustomersSkeleton />}>
      <CustomersDataTable searchParams={searchParams} />
    </Suspense>
  )
}

async function CustomersDataTable({ searchParams }: Props) {
  const params = await searchParams
  try {
    const response = await serverApi.get<ContactListResponse>('/contacts', {
    params: {
      page: params.page || '1',
      per_page: '25',
      sort: (params.sort && (params.dir === 'desc' ? `-${params.sort}` : params.sort)) || 'name',
      'filter[type]': 'customer',
      q: params.q || '',
      'filter[is_active]': params.status === 'active' ? 'true' : params.status === 'inactive' ? 'false' : undefined,
    }
  })

  const customers = response.data || []
  const pagination = response.pagination as Pagination

  return (
    <CustomersTable
      initialCustomers={customers}
      initialPagination={pagination}
    />
  )
  } catch (error) {
    if (isUnauthorizedError(error)) redirect('/login')
    throw error
  }
}

function CustomersSkeleton() {
  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Customers</div>
          <h1>Customers</h1>
          <p>Manage your customer relationships</p>
        </div>
        <div className="heading-actions" />
      </div>
      <section className="metrics">
        <article className="metric-card"><div className="metric-value">—</div></article>
        <article className="metric-card"><div className="metric-value">—</div></article>
        <article className="metric-card"><div className="metric-value">—</div></article>
      </section>
      <section className="panel">
        <div className="panel-header" style={{ flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', width: '100%' }}>
            <div style={{ position: 'relative', minWidth: 220, flex: 1, maxWidth: 360 }}>
              <div style={{ height: 36, borderRadius: 6, background: '#f0f4f9' }} />
            </div>
          </div>
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

export type { Contact, ContactListResponse, Pagination }