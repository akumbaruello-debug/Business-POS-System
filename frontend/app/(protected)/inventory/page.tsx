'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Boxes,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  CircleDollarSign,
  ClipboardList,
  Download,
  Package,
  Search,
  Settings2,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type {
  InventoryListResponse,
  InventoryListResponseWithSummary,
  InventorySummary,
  InventorySummaryStats,
  Pagination,
  StockAdjustmentRequest,
} from '@/lib/inventory-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import StockAdjustmentDialog from './_adjust-dialog'
import StockHistoryDialog from './_stock-history-dialog'

// ---------------------------------------------------------------------------
// Backend contract notes (from openapi.yaml):
//   GET /inventory?page=&per_page=&q=&sort=&filter[category_id]=&filter[is_active]=&filter[stock_status]=
//     → { data: InventorySummary[], pagination: Pagination, summary: InventorySummaryStats }  (capability inventory.view)
//   InventorySummary = { product_id, product_name, product_code, on_hand_quantity,
//                        moving_average_unit_cost, inventory_value, low_stock, updated_at, as_of }
//   GET /products/{id}/stock-movements   — per-product movement ledger (inventory.view);
//                                          history dialog only — V0 exposes no global
//                                          movements screen or nav entry.
//   POST /inventory/adjustments          — signed-qty stock adjustment (inventory.adjust,
//                                          requires Idempotency-Key + If-Match)
// ---------------------------------------------------------------------------

const DEFAULT_PAGINATION: Pagination = {
  page: 1, per_page: 25, total: 0, total_pages: 1, has_next: false, has_prev: false,
}

type StockStatus = 'In stock' | 'Low stock' | 'Out of stock'

function stockStatus(row: InventorySummary): StockStatus {
  if (row.on_hand_quantity <= 0) return 'Out of stock'
  if (row.low_stock) return 'Low stock'
  return 'In stock'
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('id-ID', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export default function InventoryPage() {
  const { t } = useLanguage()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [rows, setRows] = useState<InventorySummary[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)

  // Filters
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'in' | 'low' | 'out'>('all')
  const [activeOnly, setActiveOnly] = useState(true)
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [sortKey, setSortKey] = useState('product_id')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [summary, setSummary] = useState<InventorySummaryStats | null>(null)

  // Selection + actions
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [notice, setNotice] = useState('')
  const [adjustOpen, setAdjustOpen] = useState(false)
  const [adjustRow, setAdjustRow] = useState<InventorySummary | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyRow, setHistoryRow] = useState<InventorySummary | null>(null)
  const user = useSession()
  const canAdjust = user.capabilities?.includes('inventory.adjust') ?? false

  const handleAdjustRow = (row: InventorySummary) => {
    setAdjustRow(row)
    setAdjustOpen(true)
  }

  const handleAdjustDone = () => {
    fetchInventory()
  }

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const fetchInventory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        per_page: String(perPage),
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
      }
      if (query.trim()) params.q = query.trim()
      if (activeOnly) params['filter[is_active]'] = 'true'
      if (statusFilter !== 'all') params['filter[stock_status]'] = statusFilter

      const res = await api.get<InventoryListResponseWithSummary>('/inventory', { params })
      setRows(res.data)
      setPagination(res.pagination)
      setSummary(res.summary ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load inventory')
    } finally {
      setLoading(false)
    }
  }, [page, perPage, query, activeOnly, sortKey, sortDir, statusFilter])

  useEffect(() => {
    fetchInventory()
  }, [fetchInventory])

  // Reset selection on page change
  useEffect(() => {
    setSelected(new Set())
  }, [page, perPage])

  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    const ids = rows.map((r) => r.product_id)
    const allSelected = ids.length > 0 && ids.every((id) => selected.has(id))
    setSelected(allSelected ? new Set() : new Set(ids))
  }

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
    setPage(1)
  }

  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      const s = stockStatus(r)
      if (statusFilter === 'in' && s !== 'In stock') return false
      if (statusFilter === 'low' && s !== 'Low stock') return false
      if (statusFilter === 'out' && s !== 'Out of stock') return false
      return true
    })
  }, [rows, statusFilter])

  // Derived stats from server-side summary (Phase 3A)
  const lowStockCount = summary?.low_stock_count ?? 0
  const outOfStockCount = summary?.out_of_stock_count ?? 0
  const stockValue = summary?.inventory_value ?? 0
  const totalUnits = summary?.total_units ?? 0

  const statusLabel = (s: StockStatus) => {
    if (s === 'In stock') return t('status.inStock')
    if (s === 'Low stock') return t('status.lowStock')
    return t('status.outOfStock')
  }

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">{t('inventory.eyebrow')}</div>
            <h1>{t('inventory.title')}</h1>
            <p>{t('inventory.subtitle')}</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon"><AlertTriangle size={32} /></div>
          <strong>{t('common.failedToLoad', { resource: t('inventory.title') })}</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchInventory}>{t('common.retry')}</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Page heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('inventory.eyebrow')}</div>
          <h1>{t('inventory.title')}</h1>
          <p>{t('inventory.subtitle')}</p>
        </div>
        <div className="heading-actions">
          {canAdjust && (
            <Button variant="outline" size="sm" onClick={() => { setAdjustOpen(true); setAdjustRow(null) }}>
              <Settings2 size={14} className="mr-1" />
              {t('inventory.stockAdjustment')}
            </Button>
          )}
        </div>
      </div>

      {/* Summary cards */}
      <section className="metrics">
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('inventory.totalStockUnits')}</span>
            <span className="metric-icon"><Boxes size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : totalUnits.toLocaleString('id-ID')}</div>
          <div className="metric-change positive"><span className="change-note">{t('inventory.acrossActiveStockItems')}</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('inventory.lowStock')}</span>
            <span className="metric-icon"><AlertTriangle size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : t('inventory.items', { count: lowStockCount })}</div>
          <div className="metric-change positive"><span className="change-note">{t('inventory.needsReplenishment')}</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('inventory.outOfStock')}</span>
            <span className="metric-icon"><Package size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : t('inventory.items', { count: outOfStockCount })}</div>
          <div className="metric-change positive"><span className="change-note">{t('inventory.requiresImmediateAction')}</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('inventory.stockValue')}</span>
            <span className="metric-icon"><CircleDollarSign size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : formatIDR(stockValue)}</div>
          <div className="metric-change positive"><span className="change-note">{t('inventory.currentFilteredInventory')}</span></div>
        </article>
      </section>

      {/* Filters + table */}
      <section className="panel">
        <div className="panel-header" style={{ flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', width: '100%' }}>
            <div style={{ position: 'relative', minWidth: 220, flex: 1, maxWidth: 360 }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#718198' }} />
              <input
                aria-label="Search inventory"
                value={query}
                onChange={(e) => { setQuery(e.target.value); setPage(1) }}
                placeholder={t('inventory.searchPlaceholder')}
                style={{ width: '100%', height: 36, paddingLeft: 32, paddingRight: 12, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', fontSize: 13, outline: 'none' }}
              />
            </div>
            <select
              aria-label="Status filter"
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value as typeof statusFilter); setPage(1) }}
              style={{ height: 36, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 10px', fontSize: 13 }}
            >
              <option value="all">{t('inventory.allStatuses')}</option>
              <option value="in">{t('status.inStock')}</option>
              <option value="low">{t('status.lowStock')}</option>
              <option value="out">{t('status.outOfStock')}</option>
            </select>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#4b5c72', height: 36 }}>
              <input type="checkbox" checked={activeOnly} onChange={(e) => { setActiveOnly(e.target.checked); setPage(1) }} aria-label="Active products only" />
              {t('inventory.activeOnly')}
            </label>
            <Button variant="ghost" size="sm" onClick={() => { setQuery(''); setStatusFilter('all'); setActiveOnly(true); setPage(1) }}>{t('common.clearFilters')}</Button>
          </div>
          {selected.size > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: '#eff6ff', borderRadius: 6, fontSize: 13 }}>
              <strong>{t('common.selected', { count: selected.size })}</strong>
              <Button variant="outline" size="sm" onClick={() => setSelected(new Set())}>{t('common.clearFilters')}</Button>
            </div>
          )}
        </div>

        {loading ? (
          <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} style={{ height: 44, borderRadius: 6, background: '#f0f4f9', animation: 'pulse 1.5s infinite' }} />
            ))}
          </div>
        ) : filteredRows.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon"><Package size={22} /></div>
            <strong>{t('inventory.noStockItemsFound')}</strong>
            <p>{t('common.tryFilters')}</p>
            <Button variant="outline" onClick={() => { setQuery(''); setStatusFilter('all'); setActiveOnly(true); setPage(1) }}>{t('common.clearFilters')}</Button>
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 900, textAlign: 'left', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead style={{ background: '#f8fafc', color: '#718198', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <tr>
                    <th style={{ width: 44, padding: '10px 14px' }}>
                      <input type="checkbox" aria-label="Select all stock items" checked={filteredRows.length > 0 && filteredRows.every((r) => selected.has(r.product_id))} onChange={toggleAll} />
                    </th>
                    {([['product_id', t('inventory.product')], ['on_hand_quantity', t('common.stock')], ['moving_average_unit_cost', t('inventory.avgUnitCost')], ['inventory_value', t('common.inventoryValue')], ['low_stock', t('common.status')]] as const).map(([key, label]) => (
                      <th key={key} style={{ padding: '10px 14px' }}>
                        <button onClick={() => handleSort(key)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontWeight: 600, background: 'none', border: 0, cursor: 'pointer', color: sortKey === key ? 'var(--primary)' : 'inherit' }}>
                          {label}<ChevronsUpDown size={12} />
                        </button>
                      </th>
                    ))}
                    <th style={{ padding: '10px 14px' }}>{t('inventory.asOf')}</th>
                    <th style={{ padding: '10px 14px', textAlign: 'right' }}>{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((r) => {
                    const s = stockStatus(r)
                    return (
                      <tr key={r.product_id} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '12px 14px' }}>
                          <input type="checkbox" aria-label={`Select product ${r.product_id}`} checked={selected.has(r.product_id)} onChange={() => toggleSelect(r.product_id)} />
                        </td>
                        <td style={{ padding: '12px 14px' }}>
                          <div style={{ fontWeight: 600 }}>{r.product_name || t('inventory.productNumber', { id: r.product_id })}</div>
                          <div style={{ fontSize: 11, color: '#718198', marginTop: 3 }}>
                            {r.product_code ? `${r.product_code} · ` : ''}{t('inventory.productNumber', { id: r.product_id })}
                          </div>
                        </td>
                        <td style={{ padding: '12px 14px', fontWeight: 600 }}>{Number(r.on_hand_quantity).toLocaleString('id-ID')}</td>
                        <td style={{ padding: '12px 14px' }}>{r.moving_average_unit_cost != null ? formatIDR(r.moving_average_unit_cost) : '—'}</td>
                        <td style={{ padding: '12px 14px', fontWeight: 600 }}>{formatIDR(r.inventory_value)}</td>
                        <td style={{ padding: '12px 14px' }}>
                          <span style={{
                            display: 'inline-block', padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600,
                            background: s === 'In stock' ? '#ecfdf5' : s === 'Low stock' ? '#fff7ed' : '#f1f5f9',
                            color: s === 'In stock' ? '#059669' : s === 'Low stock' ? '#d97706' : '#64748b',
                          }}>
                            {statusLabel(s)}
                          </span>
                        </td>
                        <td style={{ padding: '12px 14px' }}>{fmtDate(r.as_of)}</td>
                        <td style={{ padding: '12px 14px', textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: 4, justifyContent: 'flex-end' }}>
                          <Button variant="ghost" size="sm" onClick={() => { setHistoryRow(r); setHistoryOpen(true) }} aria-label={`History ${r.product_name || r.product_id}`}>
                            <ClipboardList size={13} />
                          </Button>
                          {canAdjust && (
                            <Button variant="ghost" size="sm" onClick={() => handleAdjustRow(r)} aria-label={`Adjust ${r.product_name || r.product_id}`}>
                              <Settings2 size={13} />
                            </Button>
                          )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Mobile cards */}
            <div style={{ display: 'none', padding: 14, gap: 10, flexDirection: 'column' }} className="mobile-inventory-cards">
              {filteredRows.map((r) => {
                const s = stockStatus(r)
                return (
                  <article key={r.product_id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ fontWeight: 600 }}>{r.product_name || t('inventory.productNumber', { id: r.product_id })}</div>
                      <span style={{ padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: s === 'In stock' ? '#ecfdf5' : s === 'Low stock' ? '#fff7ed' : '#f1f5f9', color: s === 'In stock' ? '#059669' : s === 'Low stock' ? '#d97706' : '#64748b' }}>
                        {statusLabel(s)}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: '#718198', marginTop: 4 }}>
                      {r.product_code ? `${r.product_code} · ` : ''}#{r.product_id}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12, fontSize: 13 }}>
                      <div><span style={{ fontSize: 11, color: '#718198' }}>{t('common.stock')}</span><div style={{ fontWeight: 600 }}>{Number(r.on_hand_quantity).toLocaleString('id-ID')}</div></div>
                      <div><span style={{ fontSize: 11, color: '#718198' }}>{t('common.value')}</span><div style={{ fontWeight: 600 }}>{formatIDR(r.inventory_value)}</div></div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                      <Button variant="outline" size="sm" style={{ flex: 1 }} onClick={() => { setHistoryRow(r); setHistoryOpen(true) }}>{t('inventory.viewHistory')}</Button>
                      {canAdjust && <Button size="sm" style={{ flex: 1 }} onClick={() => handleAdjustRow(r)}>{t('inventory.adjustStock')}</Button>}
                    </div>
                  </article>
                )
              })}
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderTop: '1px solid var(--border)', fontSize: 13, color: '#718198', flexWrap: 'wrap', gap: 8 }}>
              <span>{t('common.showing', { start: filteredRows.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0, end: Math.min(pagination.page * pagination.per_page, pagination.total), total: pagination.total })}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <select value={perPage} onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1) }} style={{ height: 32, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 8px', fontSize: 12 }}>
                  <option value={10}>{t('common.perPage', { n: 10 })}</option>
                  <option value={25}>{t('common.perPage', { n: 25 })}</option>
                  <option value={50}>{t('common.perPage', { n: 50 })}</option>
                </select>
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}><ChevronLeft size={14} /></Button>
                <span style={{ fontSize: 12, padding: '0 6px' }}>{t('common.pageOf', { page: pagination.page, total: pagination.total_pages })}</span>
                <Button variant="outline" size="sm" disabled={page >= pagination.total_pages} onClick={() => setPage((p) => p + 1)}><ChevronRight size={14} /></Button>
              </div>
            </div>
          </>
        )}
      </section>

      {/* Toast */}
      {notice && (
        <div role="status" style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 50, display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 10, background: 'var(--foreground)', color: 'white', fontSize: 13, boxShadow: '0 8px 24px rgba(0,0,0,.15)' }}>
          <Check size={14} />{notice}
        </div>
      )}

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
        @media(max-width:620px) {
          .mobile-inventory-cards { display:flex!important; }
          table { display:none; }
        }
      `}</style>
      <StockAdjustmentDialog
        open={adjustOpen}
        onClose={() => { setAdjustOpen(false); setAdjustRow(null) }}
        onNotice={toast}
        onDone={handleAdjustDone}
        products={rows}
      />
      <StockHistoryDialog
        key={historyRow?.product_id ?? 'none'}
        open={historyOpen}
        onClose={() => { setHistoryOpen(false); setHistoryRow(null) }}
        productId={historyRow?.product_id ?? null}
        productName={historyRow?.product_name || t('inventory.productNumber', { id: historyRow?.product_id ?? '' })}
      />
    </div>
  )
}
