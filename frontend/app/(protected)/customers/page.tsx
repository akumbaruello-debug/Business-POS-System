'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSession } from '@/lib/session'
import {
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Eye,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  UserCheck,
  Users,
  X,
} from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Contact, ContactListResponse, Pagination } from '@/lib/contact-types'
import { formatIDR } from '@/lib/format'
import { Button } from '@/components/ui/button'

// ---------------------------------------------------------------------------
// Backend contract notes (from openapi.yaml):
//   GET /contacts?filter[type]=customer&page=&per_page=&q=&sort=&filter[is_active]=
//     → { data: Contact[], pagination: Pagination }   (capability contact.view)
//   POST /contacts/{id}/deactivate   (Idempotency-Key, capability contact.manage)
//   DELETE /contacts/{id}            (Idempotency-Key, capability contact.manage)
//   PATCH /contacts/{id}             (If-Match, capability contact.edit)
//   POST /contacts                   (Idempotency-Key, capability contact.create)
// The backend models customers + suppliers as a single `contacts` record whose
// `type` is `customer` | `supplier` | `both`. There is no separate Customer
// object — we request `filter[type]=customer` and map the Contact into the UI.
// Backend Contact has NO company / orders / spent / first-order / city fields;
// V0's purchase-history summary is not part of the contact contract, so those
// columns are omitted (not fabricated). Import / Export have no backend
// endpoints → buttons marked not-yet-wired.
// ponytail: when supplier/customer-detail endpoints (sales history by contact)
// land, surface per-customer orders/spent again. Add when that API exists.
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

/** Compute the If-Match header value from a contact's updated_at.
 * Matches backend format_etag() — quoted ISO-8601. */
function etagHeader(c: Contact): Record<string, string> {
  return { 'If-Match': `"${c.updated_at}"` }
}


/** GET /contacts/{id}/summary */
type CustomerSummary = {
  total_sales: number
  sales_count: number
  receivable: number
}

export default function CustomersPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [customers, setCustomers] = useState<Contact[]>([])
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
  const [dialog, setDialog] = useState<'view' | 'confirm-deactivate' | 'add' | 'edit' | null>(null)
  const [activeCustomer, setActiveCustomer] = useState<Contact | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [summary, setSummary] = useState<CustomerSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [formErrors, setFormErrors] = useState<Record<string, string>>({})

  // Permissions — gated via capabilities, never via role name checks.
  const user = useSession()
  const canCreate = user.capabilities.includes('contact.create')
  const canEdit = user.capabilities.includes('contact.edit')


  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const fetchCustomers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        per_page: String(perPage),
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
        'filter[type]': 'customer',
      }
      if (query.trim()) params.q = query.trim()
      if (statusFilter === 'active') params['filter[is_active]'] = 'true'
      if (statusFilter === 'inactive') params['filter[is_active]'] = 'false'

      const res = await api.get<ContactListResponse>('/contacts', { params })
      setCustomers(res.data)
      setPagination(res.pagination)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load customers')
    } finally {
      setLoading(false)
    }
  }, [page, perPage, query, statusFilter, sortKey, sortDir])

  useEffect(() => {
    fetchCustomers()
  }, [fetchCustomers])

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
    const ids = customers.map((c) => c.id)
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

  // Derived stats from current page (backend doesn't return customer aggregates)
  const activeCount = customers.filter((c) => c.is_active).length
  const inactiveCount = customers.length - activeCount

  const handleDeactivate = async () => {
    if (!activeCustomer) return
    setActionLoading(true)
    try {
      const idemKey = crypto.randomUUID()
      await api.post(`/contacts/${activeCustomer.id}/deactivate`, { reason: 'Deactivated from Customers UI' }, {
        headers: { 'Idempotency-Key': idemKey },
      })
      toast(`${activeCustomer.name} deactivated`)
      setDialog(null)
      setActiveCustomer(null)
      fetchCustomers()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Deactivate failed')
    } finally {
      setActionLoading(false)
    }
  }

  const openView = (c: Contact) => {
    setActiveCustomer(c)
    setMenuOpen(null)
    setDialog('view')
    setSummary(null)
    setSummaryLoading(true)
    fetchSummary(c.id).finally(() => setSummaryLoading(false))
  }

  const openDeactivate = (c: Contact) => {
    setActiveCustomer(c)
    setMenuOpen(null)
    setDialog('confirm-deactivate')
  }

  const openAdd = () => {
    setActiveCustomer(null)
    setFormErrors({})
    setDialog('add')
  }

  const openEdit = (c: Contact) => {
    setActiveCustomer(c)
    setMenuOpen(null)
    setFormErrors({})
    setDialog('edit')
  }

  // --- Mutations ------------------------------------------------------------

  async function handleCreateCustomer(form: {
    name: string
    phone: string
    email: string
    address: string
    notes: string
  }) {
    setActionLoading(true)
    setFormErrors({})
    try {
      await api.post<Contact>('/contacts', {
        type: 'customer',
        name: form.name.trim(),
        phone: form.phone.trim() || undefined,
        email: form.email.trim() || undefined,
        address: form.address.trim() || undefined,
        notes: form.notes.trim() || undefined,
        is_active: true,
      }, {
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      toast('Customer created')
      setDialog(null)
      fetchCustomers()
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      if (code === 'conflict') {
        setFormErrors({ name: 'A contact with this name already exists' })
      } else {
        toast(err instanceof Error ? err.message : 'Create failed')
      }
    } finally {
      setActionLoading(false)
    }
  }

  async function handleEditCustomer(form: {
    name: string
    phone: string
    email: string
    address: string
    notes: string
  }) {
    if (!activeCustomer) return
    setActionLoading(true)
    setFormErrors({})
    try {
      await api.patch<Contact>(`/contacts/${activeCustomer.id}`, {
        name: form.name.trim() || undefined,
        phone: form.phone.trim() || undefined,
        email: form.email.trim() || undefined,
        address: form.address.trim() || undefined,
        notes: form.notes.trim() || undefined,
      }, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          ...etagHeader(activeCustomer),
        },
      })
      toast('Customer updated')
      setDialog(null)
      setActiveCustomer(null)
      fetchCustomers()
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      if (code === 'version_mismatch') {
        toast('Customer was modified by another user. Please refresh.')
        fetchCustomers()
      } else if (code === 'conflict') {
        setFormErrors({ name: 'A contact with this name already exists' })
      } else {
        toast(err instanceof Error ? err.message : 'Update failed')
      }
    } finally {
      setActionLoading(false)
    }
  }

  const fetchSummary = async (id: number) => {
    try {
      const res = await api.get<CustomerSummary>(`/contacts/${id}/summary`)
      setSummary(res)
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to load summary')
    }
  }

  const sortedRows = useMemo(() => {
    const rows = [...customers]
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
  }, [customers, sortKey, sortDir])

  if (error && !loading) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">CRM / Customers</div>
            <h1>Customers</h1>
            <p>Manage customer relationships and purchase history.</p>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon"><AlertCircle size={32} /></div>
          <strong>Failed to load customers</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchCustomers}>Retry</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Page heading */}
      <div className="page-heading">
        <div>
          <div className="eyebrow">CRM / Customers</div>
          <h1>Customers</h1>
          <p>Manage customer relationships and purchase history.</p>
        </div>
        <div className="heading-actions">

          {canCreate && (
            <Button onClick={openAdd}><Plus size={14} /> Add customer</Button>
          )}
        </div>
      </div>

      {/* Summary cards */}
      <section className="metrics">
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Total customers</span>
            <span className="metric-icon"><Users size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : pagination.total}</div>
          <div className="metric-change positive"><span className="change-note">Across all segments</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Active customers</span>
            <span className="metric-icon"><Check size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : activeCount}</div>
          <div className="metric-change positive"><span className="change-note">On this page</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">Inactive customers</span>
            <span className="metric-icon"><AlertCircle size={18} /></span>
          </div>
          <div className="metric-value">{loading ? '—' : inactiveCount}</div>
          <div className="metric-change positive"><span className="change-note">On this page</span></div>
        </article>
      </section>

      {/* Filters + table */}
      <section className="panel">
        <div className="panel-header" style={{ flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', width: '100%' }}>
            <div style={{ position: 'relative', minWidth: 220, flex: 1, maxWidth: 360 }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#718198' }} />
              <input
                aria-label="Search customers"
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
        ) : customers.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon"><Users size={22} /></div>
            <strong>No customers found</strong>
            <p>Try adjusting your search or filters.</p>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="outline" onClick={() => { setQuery(''); setStatusFilter('all'); setPage(1) }}>Clear filters</Button>
              {canCreate && (
                <Button onClick={openAdd}>Add customer</Button>
              )}
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
                      <input type="checkbox" aria-label="Select all customers" checked={customers.length > 0 && customers.every((c) => selected.has(c.id))} onChange={toggleAll} />
                    </th>
                    {([['name', 'Customer'], ['phone', 'Phone'], ['email', 'Email'], ['type', 'Type'], ['created_at', 'Created'], ['is_active', 'Status']] as const).map(([key, label]) => (
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
                  {sortedRows.map((c) => (
                    <tr key={c.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '12px 14px' }}>
                        <input type="checkbox" aria-label={`Select ${c.name}`} checked={selected.has(c.id)} onChange={() => toggleSelect(c.id)} />
                      </td>
                      <td style={{ padding: '12px 14px' }}>
                        <button onClick={() => openView(c)} style={{ background: 'none', border: 0, cursor: 'pointer', textAlign: 'left' }}>
                          <div style={{ fontWeight: 600, color: 'var(--primary)' }}>{c.name}</div>
                          {c.address && <div style={{ fontSize: 11, color: '#718198', marginTop: 2 }}>{c.address}</div>}
                        </button>
                      </td>
                      <td style={{ padding: '12px 14px' }}>{c.phone ?? '—'}</td>
                      <td style={{ padding: '12px 14px' }}>{c.email ?? '—'}</td>
                      <td style={{ padding: '12px 14px', textTransform: 'capitalize' }}>{c.type}</td>
                      <td style={{ padding: '12px 14px' }}>{fmtDate(c.created_at)}</td>
                      <td style={{ padding: '12px 14px' }}>
                        <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: c.is_active ? '#ecfdf5' : '#f1f5f9', color: c.is_active ? '#059669' : '#64748b' }}>
                          {statusLabel(c)}
                        </span>
                      </td>
                      <td style={{ padding: '12px 14px', position: 'relative' }}>
                        <button onClick={() => setMenuOpen(menuOpen === c.id ? null : c.id)} style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, borderRadius: 4, color: '#6b7a90' }} aria-label={`Actions for ${c.name}`}>
                          <MoreHorizontal size={16} />
                        </button>
                        {menuOpen === c.id && (
                          <div style={{ position: 'absolute', right: 14, top: 40, zIndex: 30, width: 200, background: 'white', border: '1px solid var(--border)', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.1)', padding: 4 }}>
                            <button onClick={() => openView(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Eye size={14} /> View customer</button>
                            {canEdit && (
                              <button onClick={() => openEdit(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Pencil size={14} /> Edit customer</button>
                            )}

                            {c.is_active && (
                              <button onClick={() => openDeactivate(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#dc2626' }}><UserCheck size={14} /> Deactivate</button>
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
            <div style={{ display: 'none', padding: 14, gap: 10, flexDirection: 'column' }} className="mobile-customer-cards">
              {sortedRows.map((c) => (
                <article key={c.id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div>
                      <button onClick={() => openView(c)} style={{ background: 'none', border: 0, cursor: 'pointer', fontWeight: 600, color: 'var(--primary)', padding: 0 }}>{c.name}</button>
                      <div style={{ fontSize: 11, color: '#718198' }}>{c.type}</div>
                    </div>
                    <span style={{ padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: c.is_active ? '#ecfdf5' : '#f1f5f9', color: c.is_active ? '#059669' : '#64748b' }}>
                      {statusLabel(c)}
                    </span>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 12, fontSize: 13 }}>
                    <div><span style={{ fontSize: 11, color: '#718198' }}>Phone</span><div>{c.phone ?? '—'}</div></div>
                    <div><span style={{ fontSize: 11, color: '#718198' }}>Email</span><div>{c.email ?? '—'}</div></div>
                  </div>
                  <Button variant="outline" size="sm" style={{ width: '100%', marginTop: 12 }} onClick={() => openView(c)}>View customer</Button>
                </article>
              ))}
            </div>

            {/* Pagination */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 14px', borderTop: '1px solid var(--border)', fontSize: 13, color: '#718198', flexWrap: 'wrap', gap: 8 }}>
              <span>Showing {customers.length > 0 ? (pagination.page - 1) * pagination.per_page + 1 : 0}–{Math.min(pagination.page * pagination.per_page, pagination.total)} of {pagination.total}</span>
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
      {dialog === 'view' && activeCustomer && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveCustomer(null) } }}>
          <div style={{ width: '100%', maxWidth: 560, maxHeight: '90vh', overflowY: 'auto', background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
              <div>
                <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Customer detail</h2>
                <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>Profile and contact information.</p>
              </div>
              <button onClick={() => { setDialog(null); setActiveCustomer(null) }} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4 }}><X size={18} /></button>
            </div>
            <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
              {([
                ['Name', activeCustomer.name],
                ['Type', activeCustomer.type],
                ['Phone', activeCustomer.phone ?? '—'],
                ['Email', activeCustomer.email ?? '—'],
                ['Address', activeCustomer.address ?? '—'],
                ['Status', statusLabel(activeCustomer)],
                ['Created', fmtDate(activeCustomer.created_at)],
                ['Last updated', fmtDate(activeCustomer.updated_at)],
              ] as const).map(([label, value]) => (
                <div key={label} style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 14, fontWeight: 500, textTransform: 'capitalize' }}>{String(value)}</div>
                </div>
              ))}
              {activeCustomer.notes && (
                <div style={{ gridColumn: '1 / -1', background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>Notes</div>
                  <div style={{ fontSize: 13 }}>{activeCustomer.notes}</div>
                </div>
              )}
            </div>
            {/* Customer summary metrics (G1) */}
            <div style={{ marginTop: 20 }}>
              {summaryLoading ? (
                <div>Loading summary…</div>
              ) : summary ? (
                <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                    <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>Total sales</div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{formatIDR(summary.total_sales)}</div>
                  </div>
                  <div style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                    <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>Receivable</div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{formatIDR(summary.receivable)}</div>
                  </div>
                  <div style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                    <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>Sales count</div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{summary.sales_count}</div>
                  </div>
                </div>
              ) : null}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveCustomer(null) }}>Close</Button>
              {canEdit && (
                <Button onClick={() => { const c = activeCustomer; setDialog(null); if (c) openEdit(c) }}>Edit customer</Button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Deactivate confirmation dialog */}
      {dialog === 'confirm-deactivate' && activeCustomer && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveCustomer(null) } }}>
          <div style={{ width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px' }}>Deactivate customer?</h2>
            <p style={{ fontSize: 13, color: '#718198', margin: '0 0 20px' }}>
              <strong>{activeCustomer.name}</strong> will be marked inactive. Existing transactions and records are preserved.
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveCustomer(null) }} disabled={actionLoading}>Cancel</Button>
              <Button variant="destructive" onClick={handleDeactivate} disabled={actionLoading}>
                {actionLoading ? 'Deactivating...' : 'Deactivate'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Customer Form Dialog (Add / Edit) */}
      {(dialog === 'add' || (dialog === 'edit' && activeCustomer)) && (
        <CustomerFormDialog
          mode={dialog === 'add' ? 'add' : 'edit'}
          customer={dialog === 'edit' ? activeCustomer : null}
          onClose={() => {
            setDialog(null)
            setActiveCustomer(null)
            setFormErrors({})
          }}
          onSubmit={dialog === 'add' ? handleCreateCustomer : handleEditCustomer}
          loading={actionLoading}
          serverErrors={formErrors}
        />
      )}

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
        @media(max-width:620px) {
          .mobile-customer-cards { display:flex!important; }
          table { display:none; }
        }
      `}</style>
    </div>
  )
}

// --- CustomerFormDialog component ------------------------------------------

function CustomerFormDialog({
  mode,
  customer,
  onClose,
  onSubmit,
  loading,
  serverErrors,
}: {
  mode: 'add' | 'edit'
  customer: Contact | null
  onClose: () => void
  onSubmit: (form: { name: string; phone: string; email: string; address: string; notes: string }) => void
  loading: boolean
  serverErrors?: Record<string, string>
}) {
  const [name, setName] = useState(customer?.name ?? '')
  const [phone, setPhone] = useState(customer?.phone ?? '')
  const [email, setEmail] = useState(customer?.email ?? '')
  const [address, setAddress] = useState(customer?.address ?? '')
  const [notes, setNotes] = useState(customer?.notes ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})

  // Surface server-side errors (e.g. 409 conflict on name) as inline field errors.
  useEffect(() => {
    if (serverErrors && Object.keys(serverErrors).length > 0) {
      setErrors((prev) => ({ ...prev, ...serverErrors }))
    }
  }, [serverErrors])

  const validate = () => {
    const next: Record<string, string> = { ...errors }
    if (!name.trim()) next.name = 'Name is required'
    else delete next.name
    if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) next.email = 'Enter a valid email address'
    else delete next.email
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const handleSubmit = () => {
    if (!validate()) return
    onSubmit({
      name,
      phone: phone || '',
      email: email || '',
      address: address || '',
      notes: notes || '',
    })
  }

  const labelStyle: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    color: '#8a98ab',
    marginBottom: 4,
  }
  const fieldStyle: React.CSSProperties = {
    height: 34,
    borderRadius: 8,
    border: '1px solid var(--border)',
    background: 'white',
    padding: '0 10px',
    fontSize: 13,
    width: '100%',
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 40,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        background: 'rgba(15,23,42,.35)',
        padding: 24,
        overflowY: 'auto',
      }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{ width: '100%', maxWidth: 560, background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)', margin: '24px 0' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
          <div>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0, color: 'var(--foreground)' }}>
              {mode === 'add' ? 'Add customer' : 'Edit customer'}
            </h2>
            <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>
              {mode === 'add' ? 'Create a new customer record.' : 'Update customer contact information.'}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4 }}><X size={18} /></button>
        </div>
        <div style={{ display: 'grid', gap: 16 }}>
          <div>
            <label style={labelStyle}>Name *</label>
            <input
              value={name}
              onChange={(e) => { setName(e.target.value); setErrors((prev) => { const n = { ...prev }; delete n.name; return n }) }}
              placeholder="Customer name"
              style={{ ...fieldStyle, ...(errors.name ? { borderColor: '#dc2626' } : {}) }}
              aria-label="Customer name"
            />
            {errors.name && <div style={{ fontSize: 11, color: '#dc2626', marginTop: 2 }}>{errors.name}</div>}
          </div>
          <div>
            <label style={labelStyle}>Phone</label>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+62 8xx-xxxx-xxxx" style={fieldStyle} aria-label="Phone" />
          </div>
          <div>
            <label style={labelStyle}>Email</label>
            <input
              value={email}
              onChange={(e) => { setEmail(e.target.value); setErrors((prev) => { const n = { ...prev }; delete n.email; return n }) }}
              placeholder="customer@example.com"
              style={{ ...fieldStyle, ...(errors.email ? { borderColor: '#dc2626' } : {}) }}
              aria-label="Email"
            />
            {errors.email && <div style={{ fontSize: 11, color: '#dc2626', marginTop: 2 }}>{errors.email}</div>}
          </div>
          <div>
            <label style={labelStyle}>Address</label>
            <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Street address" style={fieldStyle} aria-label="Address" />
          </div>
          <div>
            <label style={labelStyle}>Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Additional notes..."
              style={{ ...fieldStyle, minHeight: 80, resize: 'vertical', padding: '8px 10px' }}
              aria-label="Notes"
            />
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
          <Button variant="outline" onClick={onClose} disabled={loading}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={loading}>
            {loading ? (mode === 'add' ? 'Creating...' : 'Saving...') : mode === 'add' ? 'Add customer' : 'Save changes'}
          </Button>
        </div>
      </div>
    </div>
  )
}
