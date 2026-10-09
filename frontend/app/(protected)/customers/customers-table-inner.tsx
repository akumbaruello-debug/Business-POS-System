import { ChevronsUpDown, MoreHorizontal, Eye, Pencil, UserCheck } from 'lucide-react'
import type { Contact } from '@/lib/contact-types'
import type { Pagination } from '@/lib/contact-types'

interface CustomersTableInnerProps {
  customers: Contact[]
  pagination: Pagination
  sortKey: string
  sortDir: 'asc' | 'desc'
  selected: Set<number>
  menuOpen: number | null
  canEdit: boolean
  activeCount: number
  inactiveCount: number
  onToggleSelect: (id: number) => void
  onToggleAll: () => void
  onSort: (key: string) => void
  onMenuOpen: (id: number | null) => void
  onOpenView: (c: Contact) => void
  onOpenEdit: (c: Contact) => void
  onDeactivate: (c: Contact) => void
  onReactivate: (c: Contact) => void
  t: (key: string) => string
}

export function CustomersTableInner({
  customers,
  sortKey,
  sortDir,
  selected,
  menuOpen,
  canEdit,
  activeCount,
  inactiveCount,
  onToggleSelect,
  onToggleAll,
  onSort,
  onMenuOpen,
  onOpenView,
  onOpenEdit,
  onDeactivate,
  onReactivate,
  t,
}: CustomersTableInnerProps) {
  const allSelected = customers.length > 0 && customers.every(c => selected.has(c.id))

  const columns = [
    ['name', t('customers.customer')],
    ['phone', t('common.phone')],
    ['email', t('common.email')],
    ['type', 'Type'],
    ['created_at', t('customers.created')],
    ['is_active', t('common.status')],
  ] as const

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', minWidth: 960, textAlign: 'left', fontSize: 13, borderCollapse: 'collapse' }}>
        <thead style={{ background: '#f8fafc', color: '#718198', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          <tr>
            <th style={{ width: 44, padding: '10px 14px' }}>
              <input type="checkbox" aria-label="Select all customers" checked={allSelected} onChange={() => onToggleAll()} />
            </th>
            {columns.map(([key, label]) => (
              <th key={key} style={{ padding: '10px 14px' }}>
                <button onClick={() => onSort(key)} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontWeight: 600, background: 'none', border: 0, cursor: 'pointer', color: sortKey === key ? 'var(--primary)' : 'inherit' }}>
                  {label}<ChevronsUpDown size={12} />
                </button>
              </th>
            ))}
            <th style={{ padding: '10px 14px' }}>{t('common.actions')}</th>
          </tr>
        </thead>
        <tbody>
          {customers.map(c => (
            <tr key={c.id} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: '12px 14px' }}>
                <input type="checkbox" aria-label={`Select ${c.name}`} checked={selected.has(c.id)} onChange={() => onToggleSelect(c.id)} />
              </td>
              <td style={{ padding: '12px 14px' }}>
                <button onClick={() => onOpenView(c)} style={{ background: 'none', border: 0, cursor: 'pointer', textAlign: 'left' }}>
                  <div style={{ fontWeight: 600, color: 'var(--primary)' }}>{c.name}</div>
                  {c.address && <div style={{ fontSize: 11, color: '#718198', marginTop: 2 }}>{c.address}</div>}
                </button>
              </td>
              <td style={{ padding: '12px 14px' }}>{c.phone ?? '—'}</td>
              <td style={{ padding: '12px 14px' }}>{c.email ?? '—'}</td>
              <td style={{ padding: '12px 14px', textTransform: 'capitalize' }}>{c.type}</td>
              <td style={{ padding: '12px 14px' }}>
                {new Date(c.created_at).toLocaleDateString('id-ID', { year: 'numeric', month: '2-digit', day: '2-digit' })}
              </td>
              <td style={{ padding: '12px 14px' }}>
                <span style={{ display: 'inline-block', padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, background: c.is_active ? '#ecfdf5' : '#f1f5f9', color: c.is_active ? '#059669' : '#64748b' }}>
                  {c.is_active ? t('common.active') : t('common.inactive')}
                </span>
              </td>
              <td style={{ padding: '12px 14px', position: 'relative' }}>
                <button onClick={() => onMenuOpen(menuOpen === c.id ? null : c.id)} style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, borderRadius: 4, color: '#6b7a90' }} aria-label={`Actions for ${c.name}`}>
                  <MoreHorizontal size={16} />
                </button>
                {menuOpen === c.id && (
                  <div style={{ position: 'absolute', right: 14, top: 40, zIndex: 30, width: 200, background: 'white', border: '1px solid var(--border)', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.1)', padding: 4 }}>
                    <button onClick={() => onOpenView(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Eye size={14} /> {t('customers.viewCustomer')}</button>
                    {canEdit && (
                      <button onClick={() => onOpenEdit(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#4b5c72' }}><Pencil size={14} /> {t('customers.editCustomer')}</button>
                    )}
                    {c.is_active ? (
                      <button onClick={() => onDeactivate(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#dc2626' }}><UserCheck size={14} /> Deactivate</button>
                    ) : (
                      canEdit && (
                        <button onClick={() => onReactivate(c)} style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'none', border: 0, cursor: 'pointer', borderRadius: 6, fontSize: 13, color: '#059669' }}><UserCheck size={14} /> Reactivate</button>
                      )
                    )}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}