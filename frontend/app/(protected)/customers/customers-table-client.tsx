'use client'

import { useState, useEffect, useMemo } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { api } from '@/lib/api-client'
import { useSession } from '@/lib/session'
import type { Contact, Pagination } from '@/lib/contact-types'
import { Button } from '@/components/ui/button'
import { useLanguage } from '@/lib/i18n'
import { AlertCircle, Check, Plus, Search, Users } from 'lucide-react'
import { CustomersTableInner } from './customers-table-inner'

interface CustomersTableProps {
  initialCustomers: Contact[]
  initialPagination: Pagination
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
    canCreate: user.has_permission === 'owner' || user.capabilities?.includes('contacts.create'),
    canEdit: user.has_permission === 'owner' || user.capabilities?.includes('contacts.edit'),
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

  const handleDeactivate = async () => {
    if (!activeCustomer) return
    try {
      const idemKey = crypto.randomUUID()
      await api.post(`/contacts/${activeCustomer.id}/deactivate`, { reason: 'Deactivated from Customers UI' }, {
        headers: { 'Idempotency-Key': idemKey },
      })
      setNotice(`${activeCustomer.name} deactivated`)
      setTimeout(() => setNotice(''), 2400)
      setDialog(null)
      setActiveCustomer(null)
      // Trigger server re-fetch by updating URL (forces RSC re-render)
      updateUrl({})
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Deactivate failed')
      setTimeout(() => setNotice(''), 2400)
    }
  }

  const handleReactivate = async () => {
    if (!activeCustomer) return
    try {
      await api.patch<Contact>(`/contacts/${activeCustomer.id}`, {
        is_active: true,
      }, {
        headers: {
          'Idempotency-Key': crypto.randomUUID(),
          'If-Match': `"${activeCustomer.updated_at}"`,
        },
      })
      setNotice(`${activeCustomer.name} reactivated`)
      setTimeout(() => setNotice(''), 2400)
      setDialog(null)
      setActiveCustomer(null)
      // Trigger server re-fetch
      updateUrl({})
    } catch (err: any) {
      if (err?.code === 'version_mismatch') {
        setNotice(t('customers.staleRetry'))
      } else {
        setNotice(err instanceof Error ? err.message : 'Reactivate failed')
      }
      setTimeout(() => setNotice(''), 2400)
    }
  }

  const openView = (c: Contact) => {
    setActiveCustomer(c)
    setMenuOpen(null)
    setDialog('view')
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
            <Button variant="ghost" size="sm" onClick={() => updateUrl({})}>{t('common.clearFilters')}</Button>
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
              <Button variant="outline" onClick={() => updateUrl({})}>{t('common.clearFilters')}</Button>
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
        <div role="status" style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 50, display: 'flex', alignItems: 'center', gap: 8, padding: '10px 16px', borderRadius: 10, background: 'var(--foreground)', color: 'white', fontSize: 13, boxShadow: '0 8px 24px rgba(0,0,0,.15)' }}>
          <Check size={14} />{notice}
        </div>
      )}
    </div>
  )
}
