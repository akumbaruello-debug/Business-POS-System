'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Building2,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Download,
  Eye,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Truck,
  Upload,
  UserCheck,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Contact, ContactListResponse, Pagination } from '@/lib/contact-types'
import { Button } from '@/components/ui/button'

// ---------------------------------------------------------------------------
// Backend contract notes (from openapi.yaml):
//   GET /contacts?filter[type]=supplier&page=&per_page=&q=&sort=&filter[is_active]=
//     → { data: Contact[], pagination: Pagination }   (capability contact.view)
//   POST /contacts/{id}/deactivate   (Idempotency-Key, capability contact.manage)
//   PATCH /contacts/{id}             (If-Match, capability contact.edit)
//   POST /contacts                   (Idempotency-Key, capability contact.create)
//   DELETE /contacts/{id}            (Idempotency-Key, capability contact.manage)
// Suppliers share the Contact model with customers — type field distinguishes them.
// Backend Contact has NO contact-person / city / terms / purchase-aggregate fields;
// V0's purchase history and sub-type columns are omitted (not fabricated).
// Import / Export have no backend endpoints → marked not-yet-wired.
// ponytail: when supplier-purchase-aggregate endpoints land, surface per-supplier
// orders/total columns again. Add when that API exists.
// ---------------------------------------------------------------------------

const DEFAULT_PAGINATION: Pagination = {
  page: 1, per_page: 25, total: 0, total_pages: 1, has_next: false, has_prev: false,
}

function statusLabel(c: Contact): 'Active' | 'Inactive' {
  return c.is_active ? 'Active' : 'Inactive'
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('id-ID', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export default function SuppliersPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [suppliers, setSuppliers] = useState<Contact[]>([])
  const [pagination, setPagination] = useState<Pagination>(DEFAULT_PAGINATION)

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
  const [activeSupplier, setActiveSupplier] = useState<Contact | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const fetchSuppliers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        per_page: String(perPage),
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
        'filter[type]': 'supplier',
      }
      if (query.trim()) params.q = query.trim()
      if (statusFilter === 'active') params['filter[is_active]'] = 'true'
      if (statusFilter === 'inactive') params['filter[is_active]'] = 'false'

      const res = await api.get<ContactListResponse>('/contacts', { params })
      setSuppliers(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load suppliers')
    } finally {
      setLoading(false)
    }
  }, [page, perPage, query, statusFilter, sortKey, sortDir])

  useEffect(() => {
    fetchSuppliers()
  }, [fetchSuppliers])

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
    const ids = suppliers.map((s) => s.id)
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

  const activeCount = suppliers.filter((s) => s.is_active).length
  const inactiveCount = suppliers.length - activeCount

  const handleDeactivate = async () => {
    if (!activeSupplier) return
    setActionLoading(true)
    try {
      const idemKey = crypto.randomUUID()
      await api.post(`/contacts/${activeSupplier.id}/deactivate`, { reason: 'Deactivated from Suppliers UI' }, {
        headers: { 'Idempotency-Key': idemKey },
      })
      toast(`${activeSupplier.name} deactivated`)
      setDialog(null)
      setActiveSupplier(null)
      fetchSuppliers()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Deactivate failed')
    } finally {
      setActionLoading(false)
    }
  }

  const openView = (s: Contact) => {
    setActiveSupplier(s)
    setMenuOpen(null)
    setDialog('view')
  }

  const openDeactivate = (s: Contact) => {
    setActiveSupplier(s)
    setMenuOpen(null)
    setDialog('confirm-deactivate')
  }

  const sortedRows = useMemo(() => {
    const rows = [...suppliers]
    const key = sortKey as keyof Contact
    rows.sort((a, b) => {
      const av = a[key]
      const bv = b[key]
      if (av === null && bv === null) return 0
      if (av === null) return 1
      if (bv === null) return -1
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return rows
  }, [suppliers, sortKey, sortDir])

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">Purchasing / Suppliers</div>
            <h1>Suppliers</h1>
            <p>Manage supplier relationships and purchasing partners.</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon"><AlertCircle size={32} /></div>
          <strong>Failed to load suppliers</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchSuppliers}>Retry</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Page heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">Purchasing / Suppliers</div>
          <h1>Suppliers</h1>
          <p>Manage supplier relationships and purchasing partners.</p>
        </div>
        <div className="heading-actions">
          <Button variant="outline" onClick={() => toast('Import not yet wired — no backend endpoint')}><Upload size={14} /> Import</Button>
          <Button variant="outline" onClick={() => toast('Export not yet wired — no backend endpoint')}><Download size={14} /> Export</Button>
          <Button><Plus size={14} /> Add supplier</Button>
        </div>
      </div>

      {/* Summary cards */}
      <section className="metrics">
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Total suppliers</span>
            <span className="metric-icon"><Building2 size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : pagination.total}</div>
          <div className="metric-change positive"><span className="change-note">All registered suppliers</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Active suppliers</span>
            <span className="metric-icon"><Truck size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : activeCount}</div>
          <div className="metric-change positive"><span className="change-note">On this page</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Inactive suppliers</span>
            <span className="metric-icon"><AlertCircle size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : inactiveCount}</div>
          <div className="metric-change positive"><span className="change-note">On this page</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Purchase volume</span>
            <span className="metric-icon"><Check size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : '—'}</div>
          <div className="metric-change positive"><span className="change-note">Not yet tracked</span></div>
        </article>
      </section>

      {/* Filters + table */}
      <section className="panel">
        <div className="panel-header" style={{ flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', width: '100%' }}>
            <div style={{ position: 'relative', minWidth: 220, flex: 1, maxWidth: 360 }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#718198' }} />
              <input
                aria-label="Search suppliers"
                value={query}
                onChange={(e) => { setQuery(e.target.value); setPage(1) }}
                placeholder="Search name, phone, email..."
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
        ) : suppliers.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon"><Truck size={22} /></div>
            <strong>No suppliers found</strong>
            <p>Try adjusting your search or filters.</p>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="outline" onClick={() => { setQuery(''); setStatusFilter('all'); setPage(1) }}>Clear filters</Button>
              <Button>Add supplier</Button>
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
                      <input type="checkbox" aria-label="Select all suppliers" checked={suppliers.length > 0 && suppliers.every((s) => selected.has(s.id))} onChange={toggleAll} />
                    </th>
                    {([['name', 'Supplier'], ['phone', 'Phone'], ['email', 'Email'], ['type', 'Type'], ['created_at', 'Created'], ['is_active', 'Status']] as const).map(([key, label]) => (
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
                  {sortedRows.map((s) => (
                    <tr key={s.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '12px 14px' }}>
                        <input type="checkbox" aria-label={`Select ${s.name}`} checked={selected.has(s.id)} onChange={() => toggleSelect(s.id)} />
                      </td>
                      <td style={{ padding: '12px 14px' }}>
                        <button onClick={() => openView(s)} style={{ background: 'none', border: 0, cursor: 'pointer', textAlign: 'left' }}>
                          <div style={{ fontWeight: 600, color: 'var(--primary)' }}>{s.name}</div>
                          {s.address && <div style={{ fontSize: 11, color: '#718198', marginTop: 2 }}>{s.address}</div>}
                        </button>
                      </td>
                      <td style={{ padding: '12px 14px' }}>{s.phone ?? '—'}</td>
                      <td style={{ padding: '12px 14px' }}>{s.email ?? '—'}</td>
                      <td style={{ padding: '12px 14px', textTransform: 'capitalize' }}>{s.type}</td>
                      <td style={{ padding: '12px 14px' }}>{fmtDate(s.created_at)}</td>
                      <td style={{ padding: '12px 14px' }}>
                        <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: s.is_active ? '#ecfdf5' : '#f1f5f9', color: s.is_active ? '#059669' : '#64748b' }}>
                          {statusLabel(s)}
                        </span>
                      </td>
                      <td style={{ padding: '12px 14px', position: 'relative' }}>
                        <button onClick={() => setMenuOpen(menuOpen === s.id ? null : s.id)} style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, borderRadius: 4, color: '#6b7a90' }} aria-label={`Actions for ${s.name}`}>
                          <MoreHorizontal size={16} />
                        </button>
                        {menuOpen === s.id && (
                          <div style={{ position: 'absolute', right: 14, top: 40, zIndex: 30, width: 200, background: 'white', border: '1px solid var(--border)', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.1)', padding: 4 }}>
                            <button onClick={() => openView(s)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Eye size={14} /> View supplier</button>
                            <button onClick={() => { setMenuOpen(null); toast('Edit not yet wired in this UI') }} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Pencil size={14} /> Edit supplier</button>
                            <button onClick={() => toast('Change status not yet wired')} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><UserCheck size={14} /> Change status</button>
                            {s.is_active && (
                              <button onClick={() => openDeactivate(s)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#dc2626' }}><UserCheck size={14} /> Deactivate</button>
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
            <div style={{ display: 'none', padding: 14, gap: 10, flexDirection: 'column' }} className="mobile-supplier-cards">
              {sortedRows.map((s) => (
                <article key={s.id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div>
                      <button onClick={() => openView(s)} style={{ background: 'none', border: 0, cursor: 'pointer', fontWeight: 600, color: 'var(--primary)', padding: 0 }}>{s.name}</button>
                      <div style={{ fontSize: 11, color: '#718198' }}>{s.type}</div>
                    </div>
                    <span style={{ padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: s.is_active ? '#ecfdf5' : '#f1f5f9', color: s.is_active ? '#059669' : '#64748b' }}>
                      {statusLabel(s)}
                    </span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12, fontSize: 13 }}>
                    <div><span style={{ fontSize: 11, color: '#718198' }}>Phone</span><div>{s.phone ?? '—'}</div></div>
                    <div><span style={{ fontSize: 11, color: '#718198' }}>Email</span><div>{s.email ?? '—'}</div></div>
                  </div>
                  <Button variant="outline" size="sm" style={{ width: '100%', marginTop: 12 }} onClick={() => openView(s)}>View supplier</Button>
                </article>
              ))}
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderTop: '1px solid var(--border)', fontSize: 13, color: '#718198', flexWrap: 'wrap', gap: 8 }}>
              <span>Showing {suppliers.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0}–{Math.min(pagination.page * pagination.per_page, pagination.total)} of {pagination.total}</span>
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
      {dialog === 'view' && activeSupplier && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveSupplier(null) } }}>
          <div style={{ width: '100%', maxWidth: 560, maxHeight: '90vh', overflowY: 'auto', background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
              <div>
                <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Supplier detail</h2>
                <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>Profile and contact information.</p>
              </div>
              <button onClick={() => { setDialog(null); setActiveSupplier(null) }} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4 }}><X size={18} /></button>
            </div>
            <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
              {([
                ['Name', activeSupplier.name],
                ['Type', activeSupplier.type],
                ['Phone', activeSupplier.phone ?? '—'],
                ['Email', activeSupplier.email ?? '—'],
                ['Address', activeSupplier.address ?? '—'],
                ['Status', statusLabel(activeSupplier)],
                ['Created', fmtDate(activeSupplier.created_at)],
                ['Last updated', fmtDate(activeSupplier.updated_at)],
              ] as const).map(([label, value]) => (
                <div key={label} style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 14, fontWeight: 500, textTransform: 'capitalize' }}>{String(value)}</div>
                </div>
              ))}
              {activeSupplier.notes && (
                <div style={{ gridColumn: '1 / -1', background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>Notes</div>
                  <div style={{ fontSize: 13 }}>{activeSupplier.notes}</div>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveSupplier(null) }}>Close</Button>
              <Button onClick={() => toast('Edit not yet wired in this UI')}>Edit supplier</Button>
            </div>
          </div>
        </div>
      )}

      {/* Deactivate confirmation dialog */}
      {dialog === 'confirm-deactivate' && activeSupplier && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveSupplier(null) } }}>
          <div style={{ width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px' }}>Deactivate supplier?</h2>
            <p style={{ fontSize: 13, color: '#718198', margin: '0 0 20px' }}>
              <strong>{activeSupplier.name}</strong> will be marked inactive. Existing transactions and records are preserved.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveSupplier(null) }} disabled={actionLoading}>Cancel</Button>
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
          .mobile-supplier-cards { display:flex!important; }
          table { display:none; }
        }
      `}</style>
    </div>
  )
}
