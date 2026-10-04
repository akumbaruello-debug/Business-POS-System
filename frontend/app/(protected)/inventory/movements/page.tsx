'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDownLeft, ArrowUpRight, RefreshCw } from 'lucide-react'
import { RouteGuard } from '@/components/route-guard'
import { api } from '@/lib/api-client'
import { formatDate, formatIDR } from '@/lib/format'
import type { StockMovement } from '@/lib/inventory-types'
import type { Product } from '@/lib/product-types'

interface MovementResponse { data: StockMovement[]; pagination: { page: number; per_page: number; total: number; total_pages: number } }

const MOVEMENT_SOURCES = [
  'purchase_receipt', 'sale', 'sales_return', 'purchase_return', 'production_input',
  'production_output', 'stock_adjustment', 'opening_balance', 'sale_reversal',
  'purchase_reversal', 'sales_return_reversal', 'purchase_return_reversal',
  'production_reversal', 'adjustment_reversal', 'value_adjustment',
]

function movementDirection(row: StockMovement): 'incoming' | 'outgoing' | 'neutral' {
  if (row.trigger === 'value_adjustment') return 'neutral'
  const quantityBased = ['stock_adjustment', 'opening_balance', 'sale_reversal', 'purchase_reversal', 'sales_return_reversal', 'purchase_return_reversal', 'production_reversal', 'adjustment_reversal']
  if (quantityBased.includes(row.trigger)) return row.quantity >= 0 ? 'incoming' : 'outgoing'
  return ['purchase_receipt', 'sales_return', 'production_output'].includes(row.trigger) ? 'incoming' : 'outgoing'
}

function formatQuantity(value: number): string {
  return new Intl.NumberFormat('id-ID', { maximumFractionDigits: 3 }).format(value)
}

function StockMovementsPage() {
  const [rows, setRows] = useState<StockMovement[]>([])
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [productId, setProductId] = useState('')
  const [trigger, setTrigger] = useState('')
  const [productNames, setProductNames] = useState<Record<number, string>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const loadVersion = useRef(0)

  const load = useCallback(async () => {
    const version = ++loadVersion.current
    setLoading(true); setError('')
    try {
      const params: Record<string, string> = { page: String(page), per_page: '25', sort: '-id' }
      if (productId.trim()) params['filter[product_id]'] = productId.trim()
      if (trigger) params['filter[trigger]'] = trigger
      const result = await api.get<MovementResponse>('/stock-movements', { params })
      if (version !== loadVersion.current) return
      setRows(result.data ?? []); setTotalPages(Math.max(1, result.pagination?.total_pages ?? 1))
    } catch (e) { if (version === loadVersion.current) setError(e instanceof Error ? e.message : 'Could not load stock movements.') }
    finally { if (version === loadVersion.current) setLoading(false) }
  }, [page, productId, trigger])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    let active = true
    api.get<{ data: Product[] }>('/products', { params: { per_page: '500' } }).then((result) => {
      if (active) setProductNames(Object.fromEntries((result.data ?? []).map((product) => [product.id, product.name])))
    }).catch(() => undefined)
    return () => { active = false }
  }, [])
  return <div className="content">
    <div className="page-heading"><div><div className="eyebrow">INVENTORY / MOVEMENTS</div><h1>Stock movements</h1><p>Trace every stock increase, decrease, and reversal back to its source.</p></div><button className="button button-secondary" onClick={load} disabled={loading}><RefreshCw size={15} /> Refresh</button></div>
    <section className="panel">
      <div className="panel-header"><div><h2>Inventory ledger</h2><p>This append-only ledger records the quantity and cost impact of each stock event.</p></div></div>
      <div className="movement-filters"><label>Product ID<input inputMode="numeric" value={productId} onChange={(e) => { setProductId(e.target.value.replace(/\D/g, '')); setPage(1) }} placeholder="Any product" /></label><label>Movement source<select value={trigger} onChange={(e) => { setTrigger(e.target.value); setPage(1) }}><option value="">All sources</option>{MOVEMENT_SOURCES.map((source) => <option value={source} key={source}>{source.replace(/_/g, ' ')}</option>)}</select></label></div>
      {error && <div className="notice error">{error}</div>}
      <div className="sales-table-wrap"><table className="sales-table"><thead><tr><th>Date</th><th>Product</th><th>Source</th><th className="sales-td-right">Quantity</th><th className="sales-td-right">Unit cost</th><th className="sales-td-right">Total cost</th><th>Reference</th></tr></thead><tbody>
        {loading ? <tr><td colSpan={7} className="empty">Loading stock movements…</td></tr> : rows.length === 0 ? <tr><td colSpan={7} className="empty">No stock movements found.</td></tr> : rows.map((row) => { const direction = movementDirection(row); return <tr key={row.id}><td>{formatDate(row.movement_date)}</td><td>{productNames[row.product_id] ? `${productNames[row.product_id]} · #${row.product_id}` : `Product #${row.product_id}`}</td><td><span className="movement-source">{row.trigger.replace(/_/g, ' ')}</span></td><td className="sales-td-right"><span className={`movement-quantity ${direction}`}>{direction === 'incoming' ? <ArrowDownLeft size={13} /> : direction === 'outgoing' ? <ArrowUpRight size={13} /> : null}{formatQuantity(Math.abs(row.quantity))}{direction === 'neutral' ? ' · value only' : ''}</span></td><td className="sales-td-right sales-num">{row.unit_cost_at_movement == null ? '—' : formatIDR(row.unit_cost_at_movement)}</td><td className="sales-td-right sales-num">{row.total_cost == null ? '—' : formatIDR(row.total_cost)}</td><td>{row.reference_type ? `${row.reference_type.replace(/_/g, ' ')}${row.reference_id ? ` #${row.reference_id}` : ''}` : row.reason || '—'}</td></tr> })}
      </tbody></table></div>
      <div className="sales-pagination"><span>Page {page} of {totalPages}</span><div className="sales-pagination-nav"><button className="sales-pager" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Previous</button><button className="sales-pager" disabled={page >= totalPages || loading} onClick={() => setPage((p) => p + 1)}>Next</button></div></div>
    </section>
  </div>
}

export default function StockMovementsRoute() { return <RouteGuard required="inventory.view"><StockMovementsPage /></RouteGuard> }
