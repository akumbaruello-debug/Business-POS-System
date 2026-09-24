'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useLanguage } from '@/lib/i18n'
import { useCan } from '@/lib/authz'
import { isApiError } from '@/lib/api-client'
import { listRoles, deleteRole, isSystemRole, roleCapabilities, type Role } from '@/lib/roles-service'
import { AlertTriangle, ChevronLeft, ChevronRight, Eye, Lock, Pencil, Plus, Shield, Trash2, X } from 'lucide-react'

export default function RolesPageClient() {
  const { t } = useLanguage()
  const router = useRouter()
  const { can } = useCan()

  const [roles, setRoles] = useState<Role[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [total, setTotal] = useState(0)
  const [deleteTarget, setDeleteTarget] = useState<Role | null>(null)
  const [deleting, setDeleting] = useState(false)

  const canCreate = can('role.manage')
  const canEdit = can('role.manage')
  const canDelete = can('role.manage')

  const fetchRoles = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listRoles({ page, page_size: pageSize })
      setRoles(res.items)
      setTotal(res.total)
    } catch (err) {
      setError(isApiError(err) ? err.message : t('common.error'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRoles()
  }, [page, pageSize])

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [total, pageSize])

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      await deleteRole(deleteTarget.id, deleteTarget.etag ?? deleteTarget.updated_at ?? deleteTarget.created_at ?? '')
      setDeleteTarget(null)
      await fetchRoles()
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === 'system_role_immutable') setError(t('errors.systemRoleImmutable'))
        else if (err.code === 'referenced_by_history') setError(t('errors.referencedByHistory'))
        else if (err.status === 412) setError(t('errors.preconditionFailed'))
        else setError(err.message)
      } else {
        setError(t('common.error'))
      }
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div className="eyebrow">{t('roles.eyebrow')}</div>
          <h1 className="page-title">{t('roles.title')}</h1>
          <p className="page-subtitle">{t('roles.subtitle')}</p>
        </div>
        {canCreate && (
          <button className="btn btn-primary" onClick={() => router.push('/roles/new')}>
            <Plus size={16} /> {t('roles.addRole')}
          </button>
        )}
      </div>

      {error && <div className="notice error">{error}</div>}

      <div className="card data-table">
        <table>
          <thead>
            <tr>
              <th>{t('roles.name')}</th>
              <th>{t('roles.description')}</th>
              <th>{t('roles.capabilities')}</th>
              <th>{t('common.type')}</th>
              <th>{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="empty">{t('common.loading')}</td></tr>
            ) : roles.length === 0 ? (
              <tr><td colSpan={5} className="empty">{t('common.noResults')}</td></tr>
            ) : (
              roles.map((r) => (
                <tr key={r.id} className={isSystemRole(r) ? 'muted' : undefined}>
                  <td><div className="cell-title">{r.name}</div></td>
                  <td>{r.description || '—'}</td>
                  <td>{roleCapabilities(r).length}</td>
                  <td>
                    {isSystemRole(r) ? (
                      <span className="badge neutral"><Lock size={12} /> {t('roles.system')}</span>
                    ) : (
                      <span className="badge success"><Shield size={12} /> {t('roles.custom')}</span>
                    )}
                  </td>
                  <td className="actions">
                    <button className="icon-btn" title={t('common.view')} onClick={() => router.push(`/roles/${r.id}`)}>
                      <Eye size={16} />
                    </button>
                    {canEdit && !isSystemRole(r) && (
                      <button className="icon-btn" title={t('common.edit')} onClick={() => router.push(`/roles/${r.id}`)}>
                        <Pencil size={16} />
                      </button>
                    )}
                    {canDelete && !isSystemRole(r) && (
                      <button className="icon-btn danger" title={t('common.delete')} onClick={() => setDeleteTarget(r)}>
                        <Trash2 size={16} />
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        <div className="pagination">
          <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1) }}>
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
          </select>
          <button className="icon-btn" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}><ChevronLeft size={16} /></button>
          <span>{t('common.pageOf', { page, total: totalPages })}</span>
          <button className="icon-btn" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}><ChevronRight size={16} /></button>
        </div>
      </div>

      {deleteTarget && (
        <div className="modal-overlay" onClick={() => setDeleteTarget(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{t('roles.confirmDelete', { name: deleteTarget.name })}</h3>
            <p className="modal-note"><AlertTriangle size={16} /> {t('roles.deleteWarning')}</p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setDeleteTarget(null)}>{t('common.cancel')}</button>
              <button className="btn btn-danger" onClick={handleDelete} disabled={deleting}>
                {deleting ? t('common.processing') : t('common.delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
