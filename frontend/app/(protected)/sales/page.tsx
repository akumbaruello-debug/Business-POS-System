import { Suspense } from 'react'
import { redirect } from 'next/navigation'
import { serverApi, isUnauthorizedError } from '@/lib/server-api-client'
import type { Contact, ContactListResponse } from '@/lib/contact-types'
import type { Sale, SaleListResponse } from '@/lib/sale-types'
import { SalesTable } from './sales-table-client'

export const revalidate = 0
export const dynamic = 'force-dynamic'

interface Props {
  searchParams: Promise<{
    page?: string
    per_page?: string
    q?: string
    sort?: string
    dir?: string
    lifecycle_status?: string
    customer_id?: string
    payment_state?: string
    from_iso?: string
    to_iso?: string
  }>
}

export default function SalesPage({ searchParams }: Props) {
  return (
    <Suspense fallback={<SalesSkeleton />}>
      <SalesDataTable searchParams={searchParams} />
    </Suspense>
  )
}

async function SalesDataTable({ searchParams }: Props) {
  const params = await searchParams
  try {
    const [salesRes, contactsRes] = await Promise.allSettled([
      serverApi.get<SaleListResponse>('/sales', {
        params: {
          page: params.page || '1',
          per_page: params.per_page || '25',
          sort: params.sort || 'id',
          'filter[lifecycle_status]': params.lifecycle_status || undefined,
          'filter[customer_id]': params.customer_id || undefined,
          'filter[payment_state]': params.payment_state || undefined,
          from_iso: params.from_iso || undefined,
          to_iso: params.to_iso || undefined,
          q: params.q || '',
        },
      }),
      serverApi.get<ContactListResponse>('/contacts', {
        params: { 'filter[type]': 'customer', per_page: '500', sort: 'name' },
      }),
    ])

    const sales: Sale[] = salesRes.status === 'fulfilled' ? (salesRes.value.data || []) : []
    const pagination = salesRes.status === 'fulfilled' ? salesRes.value.pagination : {
      page: 1, per_page: 25, total: 0, total_pages: 1, has_next: false, has_prev: false,
    }
    const customers: Contact[] = contactsRes.status === 'fulfilled' ? (contactsRes.value.data || []) : []

    if (salesRes.status === 'rejected' && isUnauthorizedError(salesRes.reason)) redirect('/login')
    if (contactsRes.status === 'rejected' && isUnauthorizedError(contactsRes.reason)) redirect('/login')

    return <SalesTable initialSales={sales} initialPagination={pagination} initialCustomers={customers} />
  } catch (error) {
    if (isUnauthorizedError(error)) redirect('/login')
    throw error
  }
}

function SalesSkeleton() {
  return (
    <div className="content">
      <div className="page-heading"><div><div className="eyebrow">Sales</div><h1>Sales</h1><p>Record and track all sales transactions</p></div><div className="heading-actions" /></div>
      <section className="metrics">{Array.from({ length: 4 }).map((_, i) => <article key={i} className="metric-card"><div className="metric-value">—</div></article>)}</section>
      <div className="sales-toolbar"><div className="sales-toolbar-row"><div className="sales-toolbar-fields"><div className="sales-field"><div style={{ height: 20, borderRadius: 4, background: '#f0f4f9', width: 80 }} /><div style={{ height: 36, borderRadius: 6, background: '#f0f4f9', marginTop: 4 }} /></div></div></div></div>
      <section className="sales-card"><div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>{Array.from({ length: 6 }).map((_, i) => <div key={i} style={{ height: 46, borderRadius: 6, background: '#f0f4f9' }} />)}</div></section>
    </div>
  )
}
