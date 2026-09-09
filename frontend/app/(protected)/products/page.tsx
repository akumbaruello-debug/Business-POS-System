'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Archive,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Download,
  Eye,
  MoreHorizontal,
  Package,
  Pencil,
  Plus,
  Search,
  Tag,
  Upload,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Category, Pagination, Product, Unit } from '@/lib/product-types'
import { formatIDR } from '@/lib/format'
import { useSession } from '@/lib/session'
import { Button } from '@/components/ui/button'
import ExportProductsDialog from './_export-dialog'
import ImportProductsDialog from './_import-dialog'
import CategoryManagementDialog from './_category-dialog'

// ---------------------------------------------------------------------------
// Backend contract (from backend/app/api/v1/products.py + openapi.yaml §15.7):
//   GET   /products?page=&per_page=&q=&sort=&filter[is_active]=&filter[category_id]=&filter[unit_id]=
//           → { data: ProductResponse[], pagination }   (cap product.view)
//   POST  /products                 body ProductCreateRequest   (Idempotency-Key, product.create)
//   GET   /products/{id}                                    (product.view)
//   PATCH /products/{id}            body ProductPatch, If-Match required   (product.edit)
//   POST  /products/{id}/deactivate body { reason }   (Idempotency-Key, product.deactivate)
//
// Product model (real): id, name, code (immutable, doubles as SKU), category_id,
//   unit_id, purchase_price, selling_price, low_stock_threshold, notes,
//   is_sellable/purchasable/producible, is_active, on_hand_quantity, low_stock.
//   There is NO separate barcode/type/sku — V0's mock `type`/`barcode` fields do
//   not exist in the real schema, so they are omitted (not fabricated).
//   `code` is immutable via PATCH (schema extra=forbid) — only sent on create.
//
// Concurrency: PATCH requires If-Match = `"<updated_at ISO>"`. The list/get
// response returns updated_at serialized in exactly that canonical form, so the
// client sends If-Match: `"${product.updated_at}"`. Verified live: 200 on match,
// 412 on stale. version is derived server-side and not needed client-side.
//
// Capabilities: only Owner holds product.create/edit/deactivate. Staff has
// product.view (+ category.view/unit.view) → read-only UI. Mutation controls are
// gated by the session user's capabilities (no inert buttons that would 403).
//
// Categories/units resolve names from /categories and /units (both currently
// empty in dev DB, so rows show "—").
// Import: no backend endpoint exists → button disabled with reason.
// Categories page route does not exist yet → button disabled with reason.
// Export: backend F.6 /exports endpoint exists → wired to inventory report (async job).
// ---------------------------------------------------------------------------

interface ProductListResponse {
  data: Product[]
  pagination: Pagination
}
interface CategoryListResponse {
  data: Category[]
  pagination: Pagination
}
interface UnitListResponse {
  data: Unit[]
  pagination: Pagination
}

const DEFAULT_PAGINATION: Pagination = {
  page: 1, per_page: 25, total: 0, total_pages: 1, has_next: false, has_prev: false,
}

function statusLabel(p: Product): 'Active' | 'Inactive' {
  return p.is_active ? 'Active' : 'Inactive'
}

type DialogState = 'view' | 'add' | 'edit' | 'confirm-deactivate' | null

// Fields the real model exposes for create/edit (PATCH excludes code — immutable).
interface ProductFormValues {
  name: string
  code: string
  category_id: string
  unit_id: string
  selling_price: string
  purchase_price: string
  low_stock_threshold: string
  notes: string
  is_sellable: boolean
  is_purchasable: boolean
  is_producible: boolean
  is_active: boolean
}

function emptyForm(): ProductFormValues {
  return {
    name: '',
    code: '',
    category_id: '',
    unit_id: '',
    selling_price: '',
    purchase_price: '',
    low_stock_threshold: '',
    notes: '',
    is_sellable: true,
    is_purchasable: true,
    is_producible: false,
    is_active: true,
  }
}

export default function ProductsPage() {
  const user = useSession()
  const canCreate = user.capabilities.includes('product.create')
  const canEdit = user.capabilities.includes('product.edit')
  const canDeactivate = user.capabilities.includes('product.deactivate')

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)
  const [categories, setCategories] = useState<Category[]>([])
  const [units, setUnits] = useState<Unit[]>([])

  // Filters
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [unitFilter, setUnitFilter] = useState('all')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [sortKey, setSortKey] = useState('name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  // Selection + actions
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [menuOpen, setMenuOpen] = useState<number | null>(null)
  const [notice, setNotice] = useState('')
  const [dialog, setDialog] = useState<DialogState>(null)
  const [activeProduct, setActiveProduct] = useState<Product | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [categoryOpen, setCategoryOpen] = useState(false)

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  // Category / unit name maps (resolve FK ids → labels for display + filters).
  const categoryName = useMemo(() => {
    const m = new Map<number, string>()
    categories.forEach((c) => m.set(c.id, c.name))
    return m
  }, [categories])
  const unitName = useMemo(() => {
    const m = new Map<number, string>()
    units.forEach((u) => m.set(u.id, u.name))
    return m
  }, [units])

  const fetchLookups = useCallback(async () => {
    // Best-effort: category/unit lists power name columns + filter dropdowns.
    // Both endpoints use category.view / unit.view which every product-viewer holds.
    try {
      const [catRes, unitRes] = await Promise.all([
        api.get<CategoryListResponse>('/categories?per_page=500'),
        api.get<UnitListResponse>('/units?per_page=200'),
      ])
      setCategories(catRes.data)
      setUnits(unitRes.data)
    } catch {
      // Lookups are enrichment only — never block the product list on them.
    }
  }, [])

  useEffect(() => {
    fetchLookups()
  }, [fetchLookups])

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
      if (categoryFilter !== 'all') params['filter[category_id]'] = categoryFilter
      if (unitFilter !== 'all') params['filter[unit_id]'] = unitFilter

      const res = await api.get<ProductListResponse>('/products', { params })
      setProducts(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load products')
    } finally {
      setLoading(false)
    }
  }, [page, perPage, query, statusFilter, categoryFilter, unitFilter, sortKey, sortDir])

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

  const clearFilters = () => {
    setQuery('')
    setStatusFilter('all')
    setCategoryFilter('all')
    setUnitFilter('all')
    setPage(1)
  }

  // Derived stats from current page (backend does not return catalog aggregates)
  const activeCount = products.filter((p) => p.is_active).length
  const inactiveCount = products.length - activeCount
  const lowStockCount = products.filter((p) => p.low_stock).length

  // ---- Dialogs ----
  const openView = (p: Product) => {
    setActiveProduct(p)
    setMenuOpen(null)
    setDialog('view')
  }
  const openAdd = () => {
    setActiveProduct(null)
    setMenuOpen(null)
    setDialog('add')
  }
  const openEdit = (p: Product) => {
    setActiveProduct(p)
    setMenuOpen(null)
    setDialog('edit')
  }
  const openDeactivate = (p: Product) => {
    setActiveProduct(p)
    setMenuOpen(null)
    setDialog('confirm-deactivate')
  }
  const closeDialog = () => {
    if (actionLoading) return
    setDialog(null)
    setActiveProduct(null)
  }

  // ---- Create (POST /products, Idempotency-Key) ----
  const handleCreate = async (values: ProductFormValues) => {
    setActionLoading(true)
    try {
      const body: Record<string, unknown> = {
        name: values.name.trim(),
        selling_price: Number(values.selling_price || 0),
        purchase_price: Number(values.purchase_price || 0),
        is_sellable: values.is_sellable,
        is_purchasable: values.is_purchasable,
        is_producible: values.is_producible,
        is_active: values.is_active,
      }
      if (values.code.trim()) body.code = values.code.trim()
      if (values.category_id) body.category_id = Number(values.category_id)
      if (values.unit_id) body.unit_id = Number(values.unit_id)
      if (values.low_stock_threshold !== '') body.low_stock_threshold = Number(values.low_stock_threshold)
      if (values.notes.trim()) body.notes = values.notes.trim()

      await api.post('/products', body, {
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      toast('Product added to your catalog')
      setDialog(null)
      setActiveProduct(null)
      setPage(1)
      fetchProducts()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Create failed')
    } finally {
      setActionLoading(false)
    }
  }

  // ---- Edit (PATCH /products/{id}, If-Match) ----
  const handleUpdate = async (values: ProductFormValues) => {
    if (!activeProduct) return
    setActionLoading(true)
    try {
      const body: Record<string, unknown> = {
        name: values.name.trim(),
        selling_price: Number(values.selling_price || 0),
        purchase_price: Number(values.purchase_price || 0),
        is_sellable: values.is_sellable,
        is_purchasable: values.is_purchasable,
        is_producible: values.is_producible,
        is_active: values.is_active,
      }
      if (values.category_id) body.category_id = Number(values.category_id)
      else body.category_id = null
      if (values.unit_id) body.unit_id = Number(values.unit_id)
      else body.unit_id = null
      if (values.low_stock_threshold !== '') body.low_stock_threshold = Number(values.low_stock_threshold)
      else body.low_stock_threshold = null
      if (values.notes.trim()) body.notes = values.notes.trim()
      else body.notes = null

      await api.patch(`/products/${activeProduct.id}`, body, {
        headers: { 'If-Match': `"${activeProduct.updated_at}"` },
      })
      toast('Product updated')
      setDialog(null)
      setActiveProduct(null)
      fetchProducts()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Update failed'
      toast(/stale|412|precondition/i.test(msg) ? 'Product changed elsewhere — refresh and retry' : msg)
    } finally {
      setActionLoading(false)
    }
  }

  // ---- Deactivate (POST /products/{id}/deactivate, Idempotency-Key) ----
  const handleDeactivate = async () => {
    if (!activeProduct) return
    setActionLoading(true)
    try {
      await api.post(`/products/${activeProduct.id}/deactivate`, { reason: 'Deactivated from Products UI' }, {
        headers: { 'Idempotency-Key': crypto.randomUUID() },
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

  const notYet = (what: string) => toast(`${what} not yet available — no backend endpoint`)

  // ---- Dialogs (export/import/category) are full components mounted inline ----

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
          <div className="error-icon"><AlertCircle size={32} /></div>
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
          <Button variant="outline" onClick={() => setImportOpen(true)}><Upload size={14} /> Import</Button>
          <Button variant="outline" onClick={() => setExportOpen(true)}><Download size={14} /> Export</Button>
          <Button variant="outline" onClick={() => setCategoryOpen(true)}><Tag size={14} /> Categories</Button>
          {canCreate && (
            <Button onClick={openAdd}><Plus size={14} /> Add product</Button>
          )}
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
            <span className="metric-icon"><AlertCircle size={18} /></span>
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
                placeholder="Search name or code..."
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
            <select
              aria-label="Category filter"
              value={categoryFilter}
              onChange={(e) => { setCategoryFilter(e.target.value); setPage(1) }}
              style={{ height: 36, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 10px', fontSize: 13 }}
              disabled={categories.length === 0}
              title={categories.length === 0 ? 'No categories yet' : 'Filter by category'}
            >
              <option value="all">All categories</option>
              {categories.map((c) => (
                <option key={c.id} value={String(c.id)}>{c.name}</option>
              ))}
            </select>
            <select
              aria-label="Unit filter"
              value={unitFilter}
              onChange={(e) => { setUnitFilter(e.target.value); setPage(1) }}
              style={{ height: 36, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 10px', fontSize: 13 }}
              disabled={units.length === 0}
              title={units.length === 0 ? 'No units yet' : 'Filter by unit'}
            >
              <option value="all">All units</option>
              {units.map((u) => (
                <option key={u.id} value={String(u.id)}>{u.name}</option>
              ))}
            </select>
            <Button variant="ghost" size="sm" onClick={clearFilters}>Clear filters</Button>
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
              <Button variant="outline" onClick={clearFilters}>Clear filters</Button>
              {canCreate && <Button onClick={openAdd}>Add product</Button>}
            </div>
          </div>
        ) : (
          <>
            {/* Desktop table */}
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 1000, textAlign: 'left', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead style={{ background: '#f8fafc', color: '#718198', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  <tr>
                    <th style={{ width: 44, padding: '10px 14px' }}>
                      <input type="checkbox" aria-label="Select all" checked={products.length > 0 && products.every((p) => selected.has(p.id))} onChange={toggleAll} />
                    </th>
                    {([['name', 'Product'], ['code', 'Code'], ['category_id', 'Category'], ['unit_id', 'Unit'], ['selling_price', 'Sell price'], ['purchase_price', 'Cost'], ['on_hand_quantity', 'Stock'], ['is_active', 'Status']] as const).map(([key, label]) => (
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
                        <button onClick={() => openView(p)} style={{ background: 'none', border: 0, cursor: 'pointer', textAlign: 'left' }}>
                          <div style={{ fontWeight: 600, color: canEdit || canDeactivate ? 'var(--primary)' : 'inherit' }}>{p.name}</div>
                          {p.notes && <div style={{ fontSize: 11, color: '#718198', marginTop: 2 }}>{p.notes.slice(0, 60)}</div>}
                        </button>
                      </td>
                      <td style={{ padding: '12px 14px', fontFamily: 'monospace', fontSize: 12 }}>{p.code ?? '—'}</td>
                      <td style={{ padding: '12px 14px' }}>{p.category_id != null ? (categoryName.get(p.category_id) ?? '—') : '—'}</td>
                      <td style={{ padding: '12px 14px' }}>{p.unit_id != null ? (unitName.get(p.unit_id) ?? '—') : '—'}</td>
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
                          <div style={{ position: 'absolute', right: 14, top: 40, zIndex: 30, width: 190, background: 'white', border: '1px solid var(--border)', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.1)', padding: 4 }}>
                            <button onClick={() => openView(p)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Eye size={14} /> View product</button>
                            {canEdit && (
                              <button onClick={() => openEdit(p)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Pencil size={14} /> Edit product</button>
                            )}
                            {canDeactivate && p.is_active && (
                              <button onClick={() => openDeactivate(p)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#dc2626' }}><Archive size={14} /> Deactivate</button>
                            )}
                            {!canEdit && !canDeactivate && (
                              <div style={{ padding: '8px 10px', fontSize: 12, color: '#9aa7b8' }}>View only</div>
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
                <select value={perPage} onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1) }} style={{ height: 32, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 8px', fontSize: 12 }} aria-label="Rows per page">
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
        <ProductFormDialog
          mode="view"
          product={activeProduct}
          categories={categories}
          units={units}
          categoryName={categoryName}
          unitName={unitName}
          canEdit={canEdit}
          onEdit={() => { setDialog(null); openEdit(activeProduct) }}
          onClose={closeDialog}
        />
      )}

      {/* Add dialog */}
      {dialog === 'add' && (
        <ProductFormDialog
          mode="add"
          categories={categories}
          units={units}
          categoryName={categoryName}
          unitName={unitName}
          canManage
          busy={actionLoading}
          onSave={handleCreate}
          onClose={closeDialog}
        />
      )}

      {/* Edit dialog */}
      {dialog === 'edit' && activeProduct && (
        <ProductFormDialog
          mode="edit"
          product={activeProduct}
          categories={categories}
          units={units}
          categoryName={categoryName}
          unitName={unitName}
          canManage
          busy={actionLoading}
          onSave={handleUpdate}
          onClose={closeDialog}
        />
      )}

      {/* Deactivate confirmation dialog */}
      {dialog === 'confirm-deactivate' && activeProduct && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) closeDialog() }}>
          <div style={{ width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px' }}>Deactivate product?</h2>
            <p style={{ fontSize: 13, color: '#718198', margin: '0 0 20px' }}>
              <strong>{activeProduct.name}</strong> will be marked inactive. Existing transactions and inventory records are preserved.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button variant="outline" onClick={closeDialog} disabled={actionLoading}>Cancel</Button>
              <Button variant="destructive" onClick={handleDeactivate} disabled={actionLoading}>
                {actionLoading ? 'Deactivating...' : 'Deactivate'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Export dialog */}
      <ExportProductsDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        defaultIsActive={statusFilter === 'active' ? 'active' : statusFilter === 'inactive' ? 'inactive' : 'all'}
        defaultCategoryId={categoryFilter === 'all' ? 'all' : Number(categoryFilter)}
        defaultUnitId={unitFilter === 'all' ? 'all' : Number(unitFilter)}
        categoryOptions={categories.map((c) => ({ id: c.id, name: c.name }))}
        unitOptions={units.map((u) => ({ id: u.id, name: u.name }))}
        onNotice={toast}
      />

      {/* Import dialog */}
      <ImportProductsDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onNotice={toast}
        onImported={fetchProducts}
      />

      {/* Category management dialog */}
      <CategoryManagementDialog
        open={categoryOpen}
        onClose={() => setCategoryOpen(false)}
        onNotice={toast}
      />

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

// ---------------------------------------------------------------------------
// View / Create / Edit dialog (one component, three modes)
// ---------------------------------------------------------------------------

interface ProductFormDialogProps {
  mode: 'view' | 'add' | 'edit'
  product?: Product | null
  categories: Category[]
  units: Unit[]
  categoryName: Map<number, string>
  unitName: Map<number, string>
  canEdit?: boolean
  canManage?: boolean
  busy?: boolean
  onEdit?: () => void
  onSave?: (values: ProductFormValues) => void
  onClose: () => void
}

function ProductFormDialog({
  mode,
  product,
  categories,
  units,
  categoryName,
  unitName,
  canEdit,
  canManage,
  busy,
  onEdit,
  onSave,
  onClose,
}: ProductFormDialogProps) {
  const [form, setForm] = useState<ProductFormValues>(() => {
    if (mode === 'add') return emptyForm()
    if (product) {
      return {
        name: product.name,
        code: product.code ?? '',
        category_id: product.category_id != null ? String(product.category_id) : '',
        unit_id: product.unit_id != null ? String(product.unit_id) : '',
        selling_price: String(product.selling_price ?? 0),
        purchase_price: String(product.purchase_price ?? 0),
        low_stock_threshold: product.low_stock_threshold != null ? String(product.low_stock_threshold) : '',
        notes: product.notes ?? '',
        is_sellable: product.is_sellable,
        is_purchasable: product.is_purchasable,
        is_producible: product.is_producible,
        is_active: product.is_active,
      }
    }
    return emptyForm()
  })

  // Keep form synced if the target product changes between renders.
  const [prevProductId, setPrevProductId] = useState<number | null>(product?.id ?? null)
  if (mode !== 'add' && product && product.id !== prevProductId) {
    setPrevProductId(product.id)
    setForm({
      name: product.name,
      code: product.code ?? '',
      category_id: product.category_id != null ? String(product.category_id) : '',
      unit_id: product.unit_id != null ? String(product.unit_id) : '',
      selling_price: String(product.selling_price ?? 0),
      purchase_price: String(product.purchase_price ?? 0),
      low_stock_threshold: product.low_stock_threshold != null ? String(product.low_stock_threshold) : '',
      notes: product.notes ?? '',
      is_sellable: product.is_sellable,
      is_purchasable: product.is_purchasable,
      is_producible: product.is_producible,
      is_active: product.is_active,
    })
  }

  const set = <K extends keyof ProductFormValues>(key: K, value: ProductFormValues[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const isView = mode === 'view'
  const editing = mode === 'edit'
  const readonly = isView || (editing && !canManage)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (readonly || busy || !onSave) return
    onSave(form)
  }

  const inputStyle: React.CSSProperties = { height: 36, width: '100%', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 10px', fontSize: 13, outline: 'none' }
  const labelStyle: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 5, fontSize: 13, fontWeight: 500 }

  if (isView) {
    return (
      <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
        <div style={{ width: '100%', maxWidth: 620, maxHeight: '90vh', overflowY: 'auto', background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
            <div>
              <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Product details</h2>
              <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>Master information and metadata.</p>
            </div>
            <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4 }}><X size={18} /></button>
          </div>
          <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
            {([
              ['Name', product?.name ?? '—'],
              ['Code', product?.code ?? '—'],
              ['Category', product?.category_id != null ? (categoryName.get(product.category_id) ?? '—') : '—'],
              ['Unit', product?.unit_id != null ? (unitName.get(product.unit_id) ?? '—') : '—'],
              ['Sell price', formatIDR(product?.selling_price)],
              ['Purchase price', formatIDR(product?.purchase_price)],
              ['On hand', String(product?.on_hand_quantity ?? 0)],
              ['Low stock threshold', product?.low_stock_threshold != null ? formatIDR(product.low_stock_threshold) : '—'],
              ['Inventory value', formatIDR(product?.inventory_value)],
              ['Avg unit cost', product?.moving_average_unit_cost != null ? formatIDR(product.moving_average_unit_cost) : '—'],
              ['Status', product ? statusLabel(product) : '—'],
              ['Flags', product ? [product.is_sellable && 'Sellable', product.is_purchasable && 'Purchasable', product.is_producible && 'Producible'].filter(Boolean).join(', ') || '—' : '—'],
            ] as const).map(([label, value]) => (
              <div key={label} style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 14, fontWeight: 500 }}>{String(value)}</div>
              </div>
            ))}
            {product?.notes && (
              <div style={{ gridColumn: '1 / -1', background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>Notes</div>
                <div style={{ fontSize: 13 }}>{product.notes}</div>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
            <Button variant="outline" onClick={onClose}>Close</Button>
            {canEdit && onEdit && (
              <Button onClick={onEdit}>Edit product</Button>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <form onSubmit={submit} style={{ width: '100%', maxWidth: 620, maxHeight: '90vh', overflowY: 'auto', background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
          <div>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>{editing ? 'Edit product' : 'Add product'}</h2>
            <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>{editing ? 'Update product master information.' : 'Create a new product in your catalog.'}</p>
          </div>
          <button type="button" onClick={onClose} disabled={busy} aria-label="Close" style={{ background: 'none', border: 0, cursor: busy ? 'not-allowed' : 'pointer', padding: 4 }}><X size={18} /></button>
        </div>

        <div style={{ display: 'grid', gap: 14, gridTemplateColumns: '1fr 1fr' }}>
          <label style={{ ...labelStyle, gridColumn: '1 / -1' }}>
            Product name *
            <input required value={form.name} onChange={(e) => set('name', e.target.value)} style={inputStyle} maxLength={200} />
          </label>

          {mode === 'add' && (
            <label style={labelStyle}>
              Code (SKU)
              <input value={form.code} onChange={(e) => set('code', e.target.value)} style={{ ...inputStyle, fontFamily: 'monospace' }} maxLength={64} placeholder="e.g. FSH-001" title="Alphanumeric, dot, underscore, hyphen. Immutable after creation." />
            </label>
          )}

          <label style={labelStyle}>
            Category
            <select value={form.category_id} onChange={(e) => set('category_id', e.target.value)} style={inputStyle} disabled={categories.length === 0}>
              <option value="">{categories.length === 0 ? 'No categories yet' : 'No category'}</option>
              {categories.map((c) => (
                <option key={c.id} value={String(c.id)}>{c.name}</option>
              ))}
            </select>
          </label>

          <label style={labelStyle}>
            Unit
            <select value={form.unit_id} onChange={(e) => set('unit_id', e.target.value)} style={inputStyle} disabled={units.length === 0}>
              <option value="">{units.length === 0 ? 'No units yet' : 'No unit'}</option>
              {units.map((u) => (
                <option key={u.id} value={String(u.id)}>{u.name}</option>
              ))}
            </select>
          </label>

          <label style={labelStyle}>
            Sell price (Rp)
            <input type="number" min={0} step="any" value={form.selling_price} onChange={(e) => set('selling_price', e.target.value)} style={inputStyle} />
          </label>

          <label style={labelStyle}>
            Cost / purchase price (Rp)
            <input type="number" min={0} step="any" value={form.purchase_price} onChange={(e) => set('purchase_price', e.target.value)} style={inputStyle} />
          </label>

          <label style={{ ...labelStyle, gridColumn: '1 / -1' }}>
            Low stock threshold
            <input type="number" min={0} step="any" value={form.low_stock_threshold} onChange={(e) => set('low_stock_threshold', e.target.value)} style={inputStyle} placeholder="Optional — stock at or below this triggers the LOW badge" />
          </label>

          <label style={{ ...labelStyle, gridColumn: '1 / -1' }}>
            Notes
            <textarea value={form.notes} onChange={(e) => set('notes', e.target.value)} style={{ ...inputStyle, height: 70, paddingTop: 8, resize: 'vertical' }} maxLength={2000} />
          </label>

          <div style={{ gridColumn: '1 / -1', display: 'flex', flexWrap: 'wrap', gap: 16, paddingTop: 2 }}>
            {([['is_sellable', 'Sellable'], ['is_purchasable', 'Purchasable'], ['is_producible', 'Producible'], ['is_active', 'Active']] as const).map(([key, label]) => (
              <label key={key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
                <input type="checkbox" checked={form[key]} onChange={(e) => set(key, e.target.checked)} />
                {label}
              </label>
            ))}
          </div>
        </div>

        {editing && product && (
          <p style={{ fontSize: 12, color: '#9aa7b8', margin: '14px 0 0' }}>
            Code is immutable after creation. Updates use optimistic concurrency (If-Match).
          </p>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button type="submit" disabled={busy}>
            {busy ? (editing ? 'Saving...' : 'Creating...') : (editing ? 'Save changes' : 'Create product')}
          </Button>
        </div>
      </form>
    </div>
  )
}
