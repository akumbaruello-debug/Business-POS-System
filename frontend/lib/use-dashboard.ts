'use client'

import { useCallback, useEffect, useState } from 'react'
import { api } from './api-client'
import type { DashboardResponse, InventoryReportResponse } from './dashboard-types'

export interface DashboardState {
  loading: boolean
  error: string | null
  data: DashboardResponse | null
  inventory: InventoryReportResponse | null
  refresh: () => void
}

export function useDashboard(): DashboardState {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<DashboardResponse | null>(null)
  const [inventory, setInventory] = useState<InventoryReportResponse | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Fetch in parallel — both endpoints share auth; one failing
      // shouldn't mask the other.
      const [dash, inv] = await Promise.all([
        api.get<DashboardResponse>('/dashboard'),
        api.get<InventoryReportResponse>('/dashboard/inventory'),
      ])
      setData(dash)
      setInventory(inv)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { loading, error, data, inventory, refresh }
}
