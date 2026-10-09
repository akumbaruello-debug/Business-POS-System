'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  AlertTriangle,
  Boxes,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  ClipboardList,
  Package,
  Search,
  Settings2,
  Users,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type {
  InventoryListResponse,
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

const DEFAULT_PAGINATION: Pagination = {
  page: 1,
  per_page: 25,
  total: 0,
  total_pages: 1,
  has_next: false,
  has_prev: false,
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

interface Props {
  initialRows: InventorySummary[]
  initialPagination: Pagination
  initialSummary: InventorySummaryStats
}

export function InventoryTable({ initialRows, initialPagination, initialSummary }: Props) {
  const { t } = useLanguage()
  const router = useRouter()
  const searchParams = useSearchParams()
  const user = useSession()
  const canAdjust = user.capabilities?.includes('inventory.adjust') ?? false

  // Sync local state with server-provided initial data (RSC re-render on URL change)
  const [rows, setRows] = useState<InventorySummary[]>(initialRows)
  const [pagination, setPagination] = useState<Pagination>(initialPagination)
  const [summary, setSummary] = useState<InventorySummaryStats>(initialSummary)
  useEffect(() => {
    setRows(initialRows)
    setPagination(initialPagination)
    setSummary(initialSummary)
  }, [initialRows, initialPagination, initialSummary])

  // Filters
  const query = searchParams.get('q') || ''
  const statusFilter = (searchParams.get('filter[stock_status]') as string) || 'all'
  const page = parseInt(searchParams.get('page') || '1')
  const perPage = parseInt(searchParams.get('per_page') || '25')
  const sortKey = searchParams.get('sort') || 'product_id'
  const sortDir = (searchParams.get('dir') as 'asc' | 'desc') || 'asc'

  const updateUrl = (newParams: Record<string, string>) => {
    const params = new URLSearchParams(searchParams)
    Object.entries(newParams).forEach(([key, value]) => {
      if (value) params.set(key, value)
      else params.delete(key)
    })
    router.replace(`/inventory?${params.toString()}`, { scroll: false })
  }

  const resetUrl = () => {
    router.replace('/inventory', { scroll: false })
  }

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [menuOpen, setMenuOpen] = useState<number | null>(null)
  const [notice, setNotice] = useState('')
  const [adjustOpen, setAdjustOpen] = useState(false)
  const [adjustRow, setAdjustRow] = useState<InventorySummary | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyRow, setHistoryRow] = useState<InventorySummary | null>(null)

  const toast = (msg: string) => {
    setNotice(msg)
    setTimeout(() => setNotice(''), 2400)
  }

  const handleAdjustRow = (row: InventorySummary) => {
    setAdjustRow(row)
    setAdjustOpen(true)
  }

  const handleAdjustDone = () => {
    resetUrl()
  }

  const handleSort = (key: string) => {
    if (sortKey === key) {
      updateUrl({ sort: key, dir: sortDir === 'asc' ? 'desc' : 'asc' })
    } else {
      updateUrl({ sort: key, dir: 'asc', page: '1' })
    }
  }

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    const ids = rows.map(r => r.product_id)
    const allSelected = ids.length > 0 && ids.every(id => selected.has(id))
    setSelected(allSelected ? new Set() : new Set(ids))
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

  const sortedRows = useMemo(() => {
    const rowsCopy = [...filteredRows]
    const key = sortKey as keyof InventorySummary
    rowsCopy.sort((a, b) => {
      const av = a[key]
      const bv = b[key]
      if (av === null && bv === null) return 0
      if (av === null) return 1
      if (bv === null) return -1
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return rowsCopy
  }, [filteredRows, sortKey, sortDir])

  const statusLabel = (s: StockStatus) => {
    if (s === 'In stock') return t('status.inStock')
    if (s === 'Low stock') return t('status.lowStock')
    return t('status.outOfStock')
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
          <div className="metric-value">{summary?.total_units?.toLocaleString('id-ID') ?? '—'}</div>
          <div className="metric-change positive"><span className="change-note">{t('inventory.acrossActiveStockItems')}</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('inventory.lowStock')}</span>
            <span className="metric-icon"><AlertTriangle size={18} /></span>
          </div>
          <div className="metric-value">{t('inventory.items', { count: summary?.low_stock_count ?? 0 })}</div>
          <div className="metric-change positive"><span className="change-note">{t('inventory.needsReplenishment')}</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('inventory.outOfStock')}</span>
            <span className="metric-icon"><Package size={18} /></span>
          </div>
          <div className="metric-value">{t('inventory.items', { count: summary?.out_of_stock_count ?? 0 })}</div>
          <div className="metric-change positive"><span className="change-note">{t('inventory.requiresImmediateAction')}</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('inventory.stockValue')}</span>
            <span className="metric-icon"><Users size={18} /></span>
          </div>
          <div className="metric-value">{formatIDR(summary?.inventory_value ?? 0)}</div>
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
                key={query}
                aria-label={t('inventory.searchPlaceholder') as string}
                defaultValue={query}
                onChange={e => updateUrl({ q: e.target.value, page: '1' })}
                placeholder={t('inventory.searchPlaceholder') as string}
                style={{ width: '100%', height: 36, paddingLeft: 32, paddingRight: 12, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', fontSize: 13 }}
              />
            </div>
            <select
              aria-label="Status filter"
              defaultValue={statusFilter === 'all' ? 'all' : statusFilter}
              onChange={e => {
                const val = e.target.value
                if (val === 'all') updateUrl({ 'filter[stock_status]': '', page: '1' })
                else updateUrl({ 'filter[stock_status]': val, page: '1' })
              }}
              style={{ height: 36, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 10px', fontSize: 13 }}
            >
              <option value="all">{t('inventory.allStatuses')}</option>
              <option value="in">{t('status.inStock')}</option>
              <option value="low">{t('status.lowStock')}</option>
              <option value="out">{t('status.outOfStock')}</option>
            </select>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#4b5c72', height: 36 }}>
              <input
                type="checkbox"
                defaultChecked={true}
                onChange={e => { if (!e.target.checked) updateUrl({ 'filter[is_active]': 'false', page: '1' }); else updateUrl({ 'filter[is_active]': '', page: '1' }) }}
                aria-label="Active products only"
              />
              {t('inventory.activeOnly')}
            </label>
            <Button variant="ghost" size="sm" onClick={() => resetUrl()}>{t('common.clearFilters')}</Button>
          </div>
          {selected.size > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: '#eff6ff', borderRadius: 6, fontSize: 13 }}>
              <strong>{t('common.selected', { count: selected.size })}</strong>
              <Button variant="outline" size="sm" onClick={() => setSelected(new Set())}>{t('common.clear')}</Button>
            </div>
          )}
        </div>

        {sortedRows.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon"><Package size={22} /></div>
            <strong>{t('inventory.noStockItemsFound')}</strong>
            <p>{t('common.tryFilters')}</p>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="outline" onClick={() => resetUrl()}>{t('common.clearFilters')}</Button>
            </div>
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 900, textAlign: 'left', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead style={{ background: '#f8fafc', color: '#718198', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <tr>
                    <th style={{ width: 44, padding: '10px 14px' }}>
                      <input type="checkbox" aria-label="Select all stock items" checked={sortedRows.length > 0 && sortedRows.every((r) => selected.has(r.product_id))} onChange={toggleAll} />
                    </th>
                    {([
                      ['product_id', t('inventory.product')],
                      ['on_hand_quantity', t('common.stock')],
                      ['moving_average_unit_cost', t('inventory.avgUnitCost')],
                      ['inventory_value', t('common.inventoryValue')],
                      ['low_stock', t('common.status')],
                    ] as const).map(([key, label]) => (
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
                  {sortedRows.map((r) => {
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
                            background: s === 'In stock' ? '#ecfdf5' : s === 'Low stock' ? '#fff7od' : '#f1f5f9',
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

            {/* Pagination */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderTop: '1px solid var(--border)', fontSize: 13, color: '#718198', flexWrap: 'wrap', gap: 8 }}>
              <span>{t('common.showing', { start: sortedRows.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0, end: Math.min(pagination.page * pagination.per_page, pagination.total), total: pagination.total })}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <select
                  value={perPage}
                  onChange={e => { updateUrl({ per_page: e.target.value, page: '1' }) }}
                  style={{ height: 32, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 8px', fontSize: 12 }}>
                  <option value={10}>{t('common.perPage', { n: 10 })}</option>
                  <option value={25}>{t('common.perPage', { n: 25 })}</option>
                  <option value={50}>{t('common.perPage', { n: 50 })}</option>
                </select>
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => updateUrl({ page: String(page - 1) })}><ChevronLeft size={14} /></Button>
                <span style={{ fontSize: 12, padding: '0 6px' }}>{t('common.pageOf', { page: pagination.page, total: pagination.total_pages })}</span>
                <Button variant="outline" size="sm" disabled={page >= pagination.total_pages} onClick={() => updateUrl({ page: String(page + 1) })}><ChevronRight size={14} /></Button>
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

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
        @media(max-width:620px) {
          .mobile-inventory-cards { display:flex!important; }
          table { display:none; }
        }
      `}</style>
    </div>
  )
}
