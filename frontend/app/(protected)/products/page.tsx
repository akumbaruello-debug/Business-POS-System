'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Archive,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Download,
  Eye,
  MoreHorizontal,
  Package,
  Plus,
  Search,
  Tag,
  Upload,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Product, Pagination } from '@/lib/product-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'

// ---------------------------------------------------------------------------
// Backend contract notes (from openapi.yaml + products.py):
//   GET /products?page=&per_page=&q=&sort=&filter[is_active]=&filter[category_id]=
//     → { data: ProductResponse[], pagination: Pagination }
//   POST /products/{id}/deactivate  (Idempotency-Key required)
//   Capability: product.view for list; product.deactivate for deactivate.
// V0 uses string IDs and local mock categories/units/types.
// Backend uses integer IDs, category_id/unit_id FKs, no "type" field.
// Mapping: show category_id/unit_id as integers until Categories/Units pages
// are integrated. Status maps is_active → Active/Inactive.
// ponytail: category/unit name resolution needs GET /categories + /units; add when those pages land.
// ---------------------------------------------------------------------------

interface ListResponse {
  data: Product[]
  pagination: Pagination
}

function statusLabel(p: Product): 'Active' | 'Inactive' {
  return p.is_active ? 'Active' : 'Inactive'
}

export default function ProductsPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [pagination, setPagination] = useState<Pagination>({ page: 1, per_page: 25, total: 0, total_pages: 1, has_next: false, has_prev: false })

  // Filters
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [sortKey, setSortKey] = useState('name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  // Selection + actions
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [menuOpen, setMenuOpen] = useState<number | null>(null)
  const [notice, setNotice] = useState('')
  const [dialog, setDialog] = useState<'view' | 'confirm-deactivate' | null>(null)
  const [activeProduct, setActiveProduct] = useState<Product | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const fetchProducts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        per_page: String(perPage),
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
      }
      if (query.trim()) params.q = query.trim()
      if (statusFilter === 'active') params['filter[is_active]'] = 'true'
      if (statusFilter === 'inactive') params['filter[is_active]'] = 'false'

      const res = await api.get<ListResponse>('/products', { params })
      setProducts(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load products')
    } finally {
      setLoading(false)
    }
  }, [page, perPage, query, statusFilter, sortKey, sortDir])

  useEffect(() => {
    fetchProducts()
  }, [fetchProducts])

  // Reset selection on page change
  useEffect(() => {
    setSelected(new Set())
    setMenuOpen(null)
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
    const ids = products.map((p) => p.id)
    const allSelected = ids.every((id) => selected.has(id))
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

  const handleDeactivate = async () => {
    if (!activeProduct) return
    setActionLoading(true)
    try {
      const idemKey = crypto.randomUUID()
      await api.post(`/products/${activeProduct.id}/deactivate`, { reason: 'Deactivated from Products UI' }, {
        headers: { 'Idempotency-Key': idemKey },
      })
      toast(`${activeProduct.name} deactivated`)
      setDialog(null)
      setActiveProduct(null)
      fetchProducts()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Deactivate failed')
    } finally {
      setActionLoading(false)
    }
  }

  const openView = (p: Product) => {
    setActiveProduct(p)
    setMenuOpen(null)
    setDialog('view')
  }

  const openDeactivate = (p: Product) => {
    setActiveProduct(p)
    setMenuOpen(null)
    setDialog('confirm-deactivate')
  }

  // Derived stats from current page (not full dataset — backend doesn't return aggregates)
  const activeCount = products.filter((p) => p.is_active).length
  const inactiveCount = products.length - activeCount
  const lowStockCount = products.filter((p) => p.low_stock).length

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Catalog / Products</div>
            <h1>Products</h1>
            <p>Manage product identity, pricing, and catalog status.</p>
          </div>
        </div>
        <div className="error-state">
          <strong>Failed to load products</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchProducts}>Retry</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Page heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">Catalog / Products</div>
          <h1>Products</h1>
          <p>Manage product identity, pricing, and catalog status.</p>
        </div>
        <div className="heading-actions">
          <Button variant="outline"><Upload size={14} /> Import</Button>
          <Button variant="outline"><Download size={14} /> Export</Button>
          <Button variant="outline"><Tag size={14} /> Categories</Button>
          <Button><Plus size={14} /> Add product</Button>
        </div>
      </div>

      {/* Summary cards */}
      <section className="metrics">
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Total products</span>
            <span className="metric-icon"><Package size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : pagination.total}</div>
          <div className="metric-change positive"><span className="change-note">Across all categories</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Active products</span>
            <span className="metric-icon"><Check size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : activeCount}</div>
          <div className="metric-change positive"><span className="change-note">Available for sale</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Inactive products</span>
            <span className="metric-icon"><Archive size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : inactiveCount}</div>
          <div className="metric-change positive"><span className="change-note">Not available for sale</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Low stock</span>
            <span className="metric-icon"><Tag size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : lowStockCount}</div>
          <div className="metric-change positive"><span className="change-note">At or below threshold</span></div>
        </article>
      </section>

      {/* Filters + table */}
      <section className="panel">
        <div className="panel-header" style={{ flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', width: '100%' }}>
            <div style={{ position: 'relative', minWidth: 220, flex: 1, maxWidth: 360 }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#718198' }} />
              <input
                aria-label="Search products"
                value={query}
                onChange={(e) => { setQuery(e.target.value); setPage(1) }}
                placeholder="Search name, code..."
                style={{ width: '100%', height: 36, paddingLeft: 32, paddingRight: 12, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', fontSize: 13, outline: 'none' }}
              />
            </div>
            <select
              aria-label="Status filter"
              value={statusFilter}
              onChange={(e) => { setStatusFilter(e.target.value as typeof statusFilter); setPage(1) }}
              style={{ height: 36, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 10px', fontSize: 13 }}
            >
              <option value="all">All status</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
            <Button variant="ghost" size="sm" onClick={() => { setQuery(''); setStatusFilter('all'); setPage(1) }}>Clear filters</Button>
          </div>
          {selected.size > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: '#eff6ff', borderRadius: 6, fontSize: 13 }}>
              <strong>{selected.size}</strong> selected
              <Button variant="outline" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
            </div>
          )}
        </div>

        {loading ? (
          <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} style={{ height: 44, borderRadius: 6, background: '#f0f4f9', animation: 'pulse 1.5s infinite' }} />
            ))}
          </div>
        ) : products.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon"><Package size={22} /></div>
            <strong>No products found</strong>
            <p>Try adjusting your search or filters.</p>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="outline" onClick={() => { setQuery(''); setStatusFilter('all'); setPage(1) }}>Clear filters</Button>
              <Button>Add product</Button>
            </div>
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 960, textAlign: 'left', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead style={{ background: '#f8fafc', color: '#718198', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <tr>
                    <th style={{ width: 44, padding: '10px 14px' }}>
                      <input type="checkbox" aria-label="Select all" checked={products.length > 0 && products.every((p) => selected.has(p.id))} onChange={toggleAll} />
                    </th>
                    {([['name', 'Product'], ['code', 'Code'], ['selling_price', 'Sell price'], ['purchase_price', 'Cost'], ['on_hand_quantity', 'Stock'], ['is_active', 'Status']] as const).map(([key, label]) => (
                      <th key={key} style={{ padding: '10px 14px' }}>
                        <button onClick={() => handleSort(key)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontWeight: 600, background: 'none', border: 0, cursor: 'pointer', color: sortKey === key ? 'var(--primary)' : 'inherit' }}>
                          {label}<ChevronsUpDown size={12} />
                        </button>
                      </th>
                    ))}
                    <th style={{ padding: '10px 14px' }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {products.map((p) => (
                    <tr key={p.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '12px 14px' }}>
                        <input type="checkbox" aria-label={`Select ${p.name}`} checked={selected.has(p.id)} onChange={() => toggleSelect(p.id)} />
                      </td>
                      <td style={{ padding: '12px 14px' }}>
                        <div style={{ fontWeight: 600 }}>{p.name}</div>
                        {p.notes && <div style={{ fontSize: 11, color: '#718198', marginTop: 2 }}>{p.notes.slice(0, 60)}</div>}
                      </td>
                      <td style={{ padding: '12px 14px', fontFamily: 'monospace', fontSize: 12 }}>{p.code ?? '—'}</td>
                      <td style={{ padding: '12px 14px', fontWeight: 600 }}>{formatIDR(p.selling_price)}</td>
                      <td style={{ padding: '12px 14px' }}>{formatIDR(p.purchase_price)}</td>
                      <td style={{ padding: '12px 14px' }}>
                        <span style={{ color: p.low_stock ? '#dc2626' : 'inherit' }}>{p.on_hand_quantity}</span>
                        {p.low_stock && <span style={{ marginLeft: 6, fontSize: 10, color: '#dc2626', fontWeight: 600 }}>LOW</span>}
                      </td>
                      <td style={{ padding: '12px 14px' }}>
                        <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: p.is_active ? '#ecfdf5' : '#f1f5f9', color: p.is_active ? '#059669' : '#64748b' }}>
                          {statusLabel(p)}
                        </span>
                      </td>
                      <td style={{ padding: '12px 14px', position: 'relative' }}>
                        <button onClick={() => setMenuOpen(menuOpen === p.id ? null : p.id)} style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, borderRadius: 4, color: '#6b7a90' }} aria-label={`Actions for ${p.name}`}>
                          <MoreHorizontal size={16} />
                        </button>
                        {menuOpen === p.id && (
                          <div style={{ position: 'absolute', right: 14, top: 40, zIndex: 30, width: 180, background: 'white', border: '1px solid var(--border)', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.1)', padding: 4 }}>
                            <button onClick={() => openView(p)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Eye size={14} /> View</button>
                            <button onClick={() => { setMenuOpen(null); toast('Edit not yet wired') }} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Eye size={14} /> Edit</button>
                            {p.is_active && (
                              <button onClick={() => openDeactivate(p)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#dc2626' }}><Archive size={14} /> Deactivate</button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile cards */}
            <div style={{ display: 'none', padding: 14, gap: 10, flexDirection: 'column' }} className="mobile-product-cards">
              {products.map((p) => (
                <article key={p.id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div>
                      <div style={{ fontWeight: 600 }}>{p.name}</div>
                      <div style={{ fontSize: 11, color: '#718198', fontFamily: 'monospace' }}>{p.code ?? '—'}</div>
                    </div>
                    <span style={{ padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: p.is_active ? '#ecfdf5' : '#f1f5f9', color: p.is_active ? '#059669' : '#64748b' }}>
                      {statusLabel(p)}
                    </span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12, fontSize: 13 }}>
                    <div><span style={{ fontSize: 11, color: '#718198' }}>Sell price</span><div style={{ fontWeight: 600 }}>{formatIDR(p.selling_price)}</div></div>
                    <div><span style={{ fontSize: 11, color: '#718198' }}>Stock</span><div>{p.on_hand_quantity}{p.low_stock && ' ⚠'}</div></div>
                  </div>
                  <Button variant="outline" size="sm" style={{ width: '100%', marginTop: 12 }} onClick={() => openView(p)}>View product</Button>
                </article>
              ))}
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderTop: '1px solid var(--border)', fontSize: 13, color: '#718198', flexWrap: 'wrap', gap: 8 }}>
              <span>Showing {products.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0}–{Math.min(pagination.page * pagination.per_page, pagination.total)} of {pagination.total}</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <select value={perPage} onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1) }} style={{ height: 32, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 8px', fontSize: 12 }}>
                  <option value={10}>10 / page</option>
                  <option value={25}>25 / page</option>
                  <option value={50}>50 / page</option>
                </select>
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}><ChevronLeft size={14} /></Button>
                <span style={{ fontSize: 12, padding: '0 6px' }}>Page {pagination.page} of {pagination.total_pages}</span>
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

      {/* View dialog */}
      {dialog === 'view' && activeProduct && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveProduct(null) } }}>
          <div style={{ width: '100%', maxWidth: 600, maxHeight: '90vh', overflowY: 'auto', background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
              <div>
                <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Product details</h2>
                <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>Master information and metadata.</p>
              </div>
              <button onClick={() => { setDialog(null); setActiveProduct(null) }} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4 }}><X size={18} /></button>
            </div>
            <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
              {([
                ['Name', activeProduct.name],
                ['Code', activeProduct.code ?? '—'],
                ['Category ID', activeProduct.category_id ?? '—'],
                ['Unit ID', activeProduct.unit_id ?? '—'],
                ['Sell price', formatIDR(activeProduct.selling_price)],
                ['Purchase price', formatIDR(activeProduct.purchase_price)],
                ['On hand', String(activeProduct.on_hand_quantity)],
                ['Low stock threshold', activeProduct.low_stock_threshold ?? '—'],
                ['Inventory value', formatIDR(activeProduct.inventory_value)],
                ['Avg unit cost', activeProduct.moving_average_unit_cost != null ? formatIDR(activeProduct.moving_average_unit_cost) : '—'],
                ['Status', statusLabel(activeProduct)],
                ['Sellable', activeProduct.is_sellable ? 'Yes' : 'No'],
                ['Purchasable', activeProduct.is_purchasable ? 'Yes' : 'No'],
                ['Producible', activeProduct.is_producible ? 'Yes' : 'No'],
              ] as const).map(([label, value]) => (
                <div key={label} style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{String(value)}</div>
                </div>
              ))}
              {activeProduct.notes && (
                <div style={{ gridColumn: '1 / -1', background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>Notes</div>
                  <div style={{ fontSize: 13 }}>{activeProduct.notes}</div>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveProduct(null) }}>Close</Button>
              <Button onClick={() => toast('Edit not yet wired')}>Edit product</Button>
            </div>
          </div>
        </div>
      )}

      {/* Deactivate confirmation dialog */}
      {dialog === 'confirm-deactivate' && activeProduct && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveProduct(null) } }}>
          <div style={{ width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px' }}>Deactivate product?</h2>
            <p style={{ fontSize: 13, color: '#718198', margin: '0 0 20px' }}>
              <strong>{activeProduct.name}</strong> will be marked inactive. Existing transactions and inventory records are preserved.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveProduct(null) }} disabled={actionLoading}>Cancel</Button>
              <Button variant="destructive" onClick={handleDeactivate} disabled={actionLoading}>
                {actionLoading ? 'Deactivating...' : 'Deactivate'}
              </Button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
        @media(max-width:620px) {
          .mobile-product-cards { display:flex!important; }
          table { display:none; }
        }
      `}</style>
    </div>
  )
}

