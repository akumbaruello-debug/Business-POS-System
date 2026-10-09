'use client'

import { useState, useEffect, useMemo } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { api } from '@/lib/api-client'
import { useSession } from '@/lib/session'
import type { Contact, ContactListResponse, Pagination } from '@/lib/contact-types'
import { Button } from '@/components/ui/button'
import { useLanguage } from '@/lib/i18n'
import { AlertCircle, Check, ChevronLeft, ChevronRight, ChevronsUpDown, MoreHorizontal, Pencil, Plus, Search, UserCheck, Users, X } from 'lucide-react'
import { formatIDR } from '@/lib/format'
import { CustomersTableInner } from './customers-table-inner'

interface CustomersTableProps {
  initialCustomers: Contact[]
  initialPagination: Pagination
}

type CustomerSummary = {
  total_sales: number
  sales_count: number
  receivable: number
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('id-ID', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export function CustomersTable({
  initialCustomers,
  initialPagination,
}: CustomersTableProps) {
  const { t } = useLanguage()
  const router = useRouter()
  const searchParams = useSearchParams()
  const user = useSession()

  // Compute capabilities from server-provided user data
  const userCapabilities = {
    canCreate: user.has_permission === 'owner' || user.capabilities?.includes('contact.create'),
    canEdit: user.has_permission === 'owner' || user.capabilities?.includes('contact.edit'),
  }

  // Sync local state with server-provided initial data
  const [customers, setCustomers] = useState<Contact[]>(initialCustomers)
  const [pagination, setPagination] = useState<Pagination>(initialPagination)

  // Update local state when server sends new data (after URL navigation)
  useEffect(() => {
    setCustomers(initialCustomers)
    setPagination(initialPagination)
  }, [initialCustomers, initialPagination])

  // Reset selection and menu on page/pagination change
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [menuOpen, setMenuOpen] = useState<number | null>(null)
  const [notice, setNotice] = useState('')
  const [dialog, setDialog] = useState<'view' | 'confirm-deactivate' | 'reactivate' | 'add' | 'edit' | null>(null)
  const [activeCustomer, setActiveCustomer] = useState<Contact | null>(null)
  const [formErrors, setFormErrors] = useState<Record<string, string>>({})
  const [actionLoading, setActionLoading] = useState(false)
  const [summary, setSummary] = useState<CustomerSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)

  const canCreate = userCapabilities.canCreate
  const canEdit = userCapabilities.canEdit

  const query = searchParams.get('q') || ''
  const statusFilter = (searchParams.get('status') as 'all' | 'active' | 'inactive') || 'all'
  const sortKey = searchParams.get('sort') || 'name'
  const sortDir = (searchParams.get('dir') as 'asc' | 'desc') || 'asc'
  const page = parseInt(searchParams.get('page') || '1')
  const perPage = parseInt(searchParams.get('per_page') || '25')

  const activeCount = customers.filter(c => c.is_active).length
  const inactiveCount = customers.length - activeCount

  const updateUrl = (newParams: Record<string, string>) => {
    const params = new URLSearchParams(searchParams)
    Object.entries(newParams).forEach(([key, value]) => {
      if (value) params.set(key, value)
      else params.delete(key)
    })
    router.replace(`/customers?${params.toString()}`, { scroll: false })
  }

  const resetUrl = () => {
    router.replace('/customers', { scroll: false })
  }

  // Reset selection on pagination/page change
  useEffect(() => {
    setSelected(new Set())
    setMenuOpen(null)
  }, [page, perPage])

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    const ids = customers.map(c => c.id)
    const allSelected = ids.length > 0 && ids.every(id => selected.has(id))
    setSelected(allSelected ? new Set() : new Set(ids))
  }

  const handleSort = (key: string) => {
    if (sortKey === key) {
      updateUrl({ sort: key, dir: sortDir === 'asc' ? 'desc' : 'asc' })
    } else {
      updateUrl({ sort: key, dir: 'asc', page: '1' })
    }
  }

  const toast = (msg: string) => {
    setNotice(msg)
    setTimeout(() => setNotice(''), 2400)
  }

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
      // Reset URL to trigger fresh RSC fetch
      resetUrl()
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Deactivate failed')
    } finally {
      setActionLoading(false)
    }
  }

  const handleReactivate = async () => {
    if (!activeCustomer) return
    setActionLoading(true)
    try {
      await api.patch<Contact>(`/contacts/${activeCustomer.id}`, {
        is_active: true,
      }, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          'If-Match': `\"${activeCustomer.updated_at}\"`,
        },
      })
      toast(`${activeCustomer.name} reactivated`)
      setDialog(null)
      setActiveCustomer(null)
      // Trigger server re-fetch
      resetUrl()
    } catch (err: any) {
      if (err?.code === 'version_mismatch') {
        toast(t('customers.staleRetry'))
      } else {
        toast(err instanceof Error ? err.message : 'Reactivate failed')
      }
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

  const openReactivate = (c: Contact) => {
    setActiveCustomer(c)
    setMenuOpen(null)
    setDialog('reactivate')
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

  const fetchSummary = async (id: number) => {
    try {
      const res = await api.get<CustomerSummary>(`/contacts/${id}/summary`)
      setSummary(res)
    } catch (err) {
      toast(err instanceof Error ? err.message : 'Failed to load summary')
    }
  }

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
      toast(t('customers.createdToast'))
      setDialog(null)
      // Trigger server re-fetch
      resetUrl()
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
          'If-Match': `\"${activeCustomer.updated_at}\"`,
        },
      })
      toast(t('customers.updatedToast'))
      setDialog(null)
      setActiveCustomer(null)
      // Trigger server re-fetch
      resetUrl()
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      if (code === 'version_mismatch') {
        toast(t('customers.staleRefresh'))
        resetUrl()
      } else if (code === 'conflict') {
        setFormErrors({ name: 'A contact with this name already exists' })
      } else {
        toast(err instanceof Error ? err.message : 'Update failed')
      }
    } finally {
      setActionLoading(false)
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

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">{t('customers.eyebrow')}</div>
          <h1>{t('customers.title')}</h1>
          <p>{t('customers.subtitle')}</p>
        </div>
        <div className="heading-actions">
          {canCreate && (
            <Button onClick={openAdd}><Plus size={14} /> {t('customers.addCustomer')}</Button>
          )}
        </div>
      </div>

      <section className="metrics">
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('customers.totalCustomers')}</span>
            <span className="metric-icon"><Users size={18} /></span>
          </div>
          <div className="metric-value">{pagination.total}</div>
          <div className="metric-change positive"><span className="change-note">{t('customers.acrossAllSegments')}</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('customers.activeCustomers')}</span>
            <span className="metric-icon"><Check size={18} /></span>
          </div>
          <div className="metric-value">{activeCount}</div>
          <div className="metric-change positive"><span className="change-note">On this page</span></div>
        </article>
        <article className="metric-card">
          <div className="metric-top">
            <span className="metric-label">{t('customers.inactiveCustomers')}</span>
            <span className="metric-icon"><AlertCircle size={18} /></span>
          </div>
          <div className="metric-value">{inactiveCount}</div>
          <div className="metric-change positive"><span className="change-note">On this page</span></div>
        </article>
      </section>

      <section className="panel">
        <div className="panel-header" style={{ flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', width: '100%' }}>
            <div style={{ position: 'relative', minWidth: 220, flex: 1, maxWidth: 360 }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#718198' }} />
              <input
                key={query}
                aria-label={t('customers.searchPlaceholder') as string}
                defaultValue={query}
                onChange={e => {
                  updateUrl({ q: e.target.value, page: '1' })
                }}
                placeholder={t('customers.searchPlaceholder') as string}
                style={{ width: '100%', height: 36, paddingLeft: 32, paddingRight: 12, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', fontSize: 13 }}
              />
            </div>
            <select
              aria-label="Status filter"
              defaultValue={statusFilter}
              onChange={e => {
                updateUrl({ status: e.target.value as 'all' | 'active' | 'inactive', page: '1' })
              }}
              style={{ height: 36, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', padding: '0 10px', fontSize: 13 }}
            >
              <option value="all">{t('customers.allStatus')}</option>
              <option value="active">{t('common.active')}</option>
              <option value="inactive">{t('common.inactive')}</option>
            </select>
            <Button variant="ghost" size="sm" onClick={() => resetUrl()}>{t('common.clearFilters')}</Button>
          </div>
          {selected.size > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: '#eff6ff', borderRadius: 6, fontSize: 13 }}>
              <strong>{selected.size}</strong> selected
              <Button variant="outline" size="sm" onClick={() => setSelected(new Set())}>{t('common.clear')}</Button>
            </div>
          )}
        </div>

        {customers.length === 0 ? (
          <div className="empty-workspace">
            <div className="empty-icon"><Users size={22} /></div>
            <strong>{t('customers.noCustomersFound')}</strong>
            <p>Try adjusting your search or filters.</p>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button variant="outline" onClick={() => resetUrl()}>{t('common.clearFilters')}</Button>
              {canCreate && (
                <Button onClick={openAdd}>{t('customers.addCustomer')}</Button>
              )}
            </div>
          </div>
        ) : (
          <CustomersTableInner
            customers={sortedRows}
            pagination={pagination}
            sortKey={sortKey}
            sortDir={sortDir}
            selected={selected}
            menuOpen={menuOpen}
            canEdit={canEdit}
            activeCount={activeCount}
            inactiveCount={inactiveCount}
            onToggleSelect={toggleSelect}
            onToggleAll={toggleAll}
            onSort={handleSort}
            onMenuOpen={setMenuOpen}
            onOpenView={openView}
            onOpenEdit={openEdit}
            onDeactivate={openDeactivate}
            onReactivate={openReactivate}
            t={t as (key: string, params?: Record<string, unknown>) => string}
          />
        )}
      </section>

      {notice && (
        <div role="status" style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 50, display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 10, background: 'var(--foreground)', color: 'white', fontSize: 13, boxShadow: '0 8px 24px rgba(0,0,0,.15)' }}><Check size={14} />{notice}</div>
      )}

      {/* View dialog */}
      {dialog === 'view' && activeCustomer && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveCustomer(null) } }}>
          <div style={{ width: '100%', maxWidth: 560, maxHeight: '90vh', overflowY: 'auto', background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
              <div>
                <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>{t('customers.customerDetail')}</h2>
                <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>{t('customers.profileAndContact')}</p>
              </div>
              <button onClick={() => { setDialog(null); setActiveCustomer(null) }} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4 }}><X size={18} /></button>
            </div>
            <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr' }}>
              {([
                [t('common.name'), activeCustomer.name],
                ['Type', activeCustomer.type],
                [t('common.phone'), activeCustomer.phone ?? '—'],
                [t('common.email'), activeCustomer.email ?? '—'],
                [t('common.address'), activeCustomer.address ?? '—'],
                [t('common.status'), activeCustomer.is_active ? t('common.active') : t('common.inactive')],
                [t('customers.created'), fmtDate(activeCustomer.created_at)],
                ['Last updated', fmtDate(activeCustomer.updated_at)],
              ] as const).map(([label, value]) => (
                <div key={label} style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 14, fontWeight: 500, textTransform: 'capitalize' }}>{String(value)}</div>
                </div>
              ))}
              {activeCustomer.notes && (
                <div style={{ gridColumn: '1 / -1', background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                  <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{t('common.notes')}</div>
                  <div style={{ fontSize: 13 }}>{activeCustomer.notes}</div>
                </div>
              )}
            </div>
            {/* Customer summary metrics (G1) */}
            <div style={{ marginTop: 20 }}>
              {summaryLoading ? (
                <div>{t('customers.loadingSummary')}</div>
              ) : summary ? (
                <div style={{ display: 'grid', gap: 12, gridTemplateColumns: '1fr 1fr 1fr' }}>
                  <div style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                    <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{t('customers.totalSales')}</div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{formatIDR(summary.total_sales)}</div>
                  </div>
                  <div style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                    <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{t('customers.receivable')}</div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{formatIDR(summary.receivable)}</div>
                  </div>
                  <div style={{ background: '#f8fafc', borderRadius: 8, padding: 12 }}>
                    <div style={{ fontSize: 11, color: '#718198', marginBottom: 4 }}>{t('customers.salesCount')}</div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>{summary.sales_count}</div>
                  </div>
                </div>
              ) : null}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveCustomer(null) }}>{t('common.close')}</Button>
              {canEdit && (
                <Button onClick={() => { const c = activeCustomer; setDialog(null); if (c) openEdit(c) }}>{t('customers.editCustomer')}</Button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Deactivate confirmation dialog */}
      {dialog === 'confirm-deactivate' && activeCustomer && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveCustomer(null) } }}>
          <div style={{ width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px' }}>{t('customers.deactivateCustomer')}</h2>
            <p style={{ fontSize: 13, color: '#718198', margin: '0 0 20px' }}>
              {t('customers.deactivateText', { name: activeCustomer.name })}
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveCustomer(null) }} disabled={actionLoading}>{t('common.cancel')}</Button>
              <Button variant="destructive" onClick={handleDeactivate} disabled={actionLoading}>
                {actionLoading ? t('customers.deactivating') : 'Deactivate'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Reactivate confirmation dialog */}
      {dialog === 'reactivate' && activeCustomer && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }} onMouseDown={(e) => { if (e.target === e.currentTarget) { setDialog(null); setActiveCustomer(null) } }}>
          <div style={{ width: '100%', maxWidth: 420, background: 'white', borderRadius: 14, border: '1px solid var(--border)', padding: 24, boxShadow: '0 20px 48px rgba(0,0,0,.12)' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 8px' }}>{t('customers.reactivateCustomer')}</h2>
            <p style={{ fontSize: 13, color: '#718198', margin: '0 0 20px' }}>
              {t('customers.reactivateText', { name: activeCustomer.name })}
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button variant="outline" onClick={() => { setDialog(null); setActiveCustomer(null) }} disabled={actionLoading}>{t('common.cancel')}</Button>
              <Button onClick={handleReactivate} disabled={actionLoading}>
                {actionLoading ? t('customers.reactivating') : 'Reactivate'}
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
  const { t } = useLanguage()
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
        zIndex: 50,
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
              {mode === 'add' ? t('customers.addCustomerTitle') : t('customers.editCustomerTitle')}
            </h2>
            <p style={{ fontSize: 13, color: '#718198', marginTop: 4 }}>
              {mode === 'add' ? t('customers.createCustomer') : t('customers.updateCustomer')}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4 }}><X size={18} /></button>
        </div>
        <div style={{ display: 'grid', gap: 16 }}>
          <div>
            <label style={labelStyle}>{t('common.name')} *</label>
            <input
              value={name}
              onChange={(e) => { setName(e.target.value); setErrors((prev) => { const n = { ...prev }; delete n.name; return n }) }}
              placeholder={t('customers.customerName')}
              style={{ ...fieldStyle, ...(errors.name ? { borderColor: '#dc2626' } : {}) }}
              aria-label="Customer name"
            />
            {errors.name && <div style={{ fontSize: 11, color: '#dc2626', marginTop: 2 }}>{errors.name}</div>}
          </div>
          <div>
            <label style={labelStyle}>{t('common.phone')}</label>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+62 8xx-xxxx-xxxx" style={fieldStyle} aria-label="Phone" />
          </div>
          <div>
            <label style={labelStyle}>{t('common.email')}</label>
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
            <label style={labelStyle}>{t('common.address')}</label>
            <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Street address" style={fieldStyle} aria-label="Address" />
          </div>
          <div>
            <label style={labelStyle}>{t('common.notes')}</label>
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
          <Button variant="outline" onClick={onClose} disabled={loading}>{t('common.cancel')}</Button>
          <Button onClick={handleSubmit} disabled={loading}>
            {loading ? t('common.saving') : mode === 'add' ? t('customers.addCustomer') : t('common.saveChanges')}
          </Button>
        </div>
      </div>
    </div>
  )
}
