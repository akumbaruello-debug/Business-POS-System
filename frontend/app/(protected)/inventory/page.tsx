'use client'

// Release candidate (Option C2): client-side data fetching for the inventory
// list. Endpoints/params mirror the previous SSR implementation exactly
// (GET /inventory with pagination, sort, q, is_active/stock_status filters).
// InventoryTable syncs from `initial*` props; URL changes and the refresh
// callback drive refetches.

import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { api } from '@/lib/api-client'
import type { ApiError } from '@/lib/api-client'
import type { InventorySummary, InventorySummaryStats } from '@/lib/inventory-types'
import { InventoryTable } from './inventory-table-client'

interface InventoryPagination {
  page: number
  per_page: number
  total: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
}

const FALLBACK_PAGINATION: InventoryPagination = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
}

export default function InventoryPage() {
  const searchParams = useSearchParams()
  const [rows, setRows] = useState<InventorySummary[]>([])
  const [pagination, setPagination] = useState<InventoryPagination>(FALLBACK_PAGINATION)
  const [summary, setSummary] = useState<InventorySummaryStats | null>(null)
  const [error, setError] = useState<string | null>(null)

  const page = searchParams.get('page') || '1'
  const perPage = searchParams.get('per_page') || '25'
  const q = searchParams.get('q') || ''
  const sort = searchParams.get('sort') || ''
  const dir = searchParams.get('dir') || ''
  const filterIsActive = searchParams.get('filter[is_active]') || ''
  const filterStockStatus = searchParams.get('filter[stock_status]') || ''

  const requestSeq = useRef(0)

  const fetchInventory = useCallback(async () => {
    const seq = ++requestSeq.current
    setError(null)
    try {
      const response = await api.get<{
        data: InventorySummary[]
        pagination: InventoryPagination
        summary: InventorySummaryStats
      }>('/inventory', {
        params: {
          page: page || '1',
          per_page: perPage || '25',
          sort: dir === 'desc' && sort ? `-${sort}` : sort || 'product_id',
          q: q || '',
          'filter[is_active]': filterIsActive || undefined,
          'filter[stock_status]': filterStockStatus || undefined,
        },
      })
      if (seq !== requestSeq.current) return
      setRows(response.data)
      setPagination(response.pagination)
      setSummary(response.summary)
    } catch (err) {
      if (seq !== requestSeq.current) return
      if (err instanceof Error && (err as ApiError).status === 401) {
        window.location.href = '/login'
        return
      }
      setError(err instanceof Error ? err.message : 'Failed to load inventory')
    }
  }, [page, perPage, q, sort, dir, filterIsActive, filterStockStatus])

  useEffect(() => {
    fetchInventory()
  }, [fetchInventory])

  if (error) {
    return (
      <div className="page">
        <div className="notice error">{error}</div>
      </div>
    )
  }

  // Initial load only: keep the table (and its search input) mounted during
  // subsequent query-triggered fetches so typing does not lose focus.
  if (!summary) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Inventory</div>
            <h1>Inventory</h1>
            <p>Loading…</p>
          </div>
          <div className="heading-actions" />
        </div>
      </div>
    )
  }

  return (
    <InventoryTable
      initialRows={rows}
      initialPagination={pagination}
      initialSummary={summary}
      onRefresh={fetchInventory}
    />
  )
}
