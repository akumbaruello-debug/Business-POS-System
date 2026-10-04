'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import { useCan } from '@/lib/authz'
import { isApiError } from '@/lib/api-client'
import {
  listUsers,
  getUser,
  deactivateUser,
  activateUser,
  unlockUser,
  resetPassword,
  isUserLocked,
  type UserListItem,
  type UserFilters,
} from '@/lib/users-service'
import { listRoles, type Role } from '@/lib/roles-service'
import {
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Eye,
  Lock,
  LockOpen,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  Shield,
  UserCheck,
  UserCog,
  UserX,
  X,
} from 'lucide-react'

const PAGE_SIZE = 25

function fmtDate(iso?: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString('id-ID', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export default function UsersPage() {
  const { t } = useLanguage()
  const router = useRouter()
  const user = useSession()
  const { can } = useCan()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [users, setUsers] = useState<UserListItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [roleFilter, setRoleFilter] = useState<number | 'all'>('all')
  const [sortKey, setSortKey] = useState('full_name')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const [roles, setRoles] = useState<Role[]>([])

  const [menuOpen, setMenuOpen] = useState<number | null>(null)
  const [notice, setNotice] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [dialog, setDialog] = useState<'deactivate' | 'activate' | 'unlock' | 'reset-password' | null>(null)
  const [activeUser, setActiveUser] = useState<UserListItem | null>(null)
  const [deactivateReason, setDeactivateReason] = useState('')
  const [resetResult, setResetResult] = useState<string | null>(null)

  const canCreate = can('user.manage')
  const canEdit = can('user.manage')
  const canManage = can('user.manage')
  const canResetPassword = can('auth.password_reset_others')

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 3000)
  }

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const filters: UserFilters = {
        page,
        page_size: PAGE_SIZE,
        sort: sortDir === 'desc' ? `-${sortKey}` : sortKey,
      }
      if (query.trim()) filters.q = query.trim()
      if (statusFilter === 'active') filters.active_only = true
      if (statusFilter === 'inactive') filters.active_only = false
      if (roleFilter !== 'all') filters.role_id = roleFilter
      const res = await listUsers(filters)
      setUsers(res.items)
      setTotal(res.total)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }, [page, query, statusFilter, roleFilter, sortKey, sortDir])

  const fetchRoles = useCallback(async () => {
    try {
      const res = await listRoles({ page_size: 100 })
      setRoles(res.items)
    } catch {
      // non-fatal
    }
  }, [])

  useEffect(() => {
    fetchUsers()
    fetchRoles()
  }, [fetchUsers, fetchRoles])

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total])

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
    setPage(1)
  }

  const openDialog = (u: UserListItem, d: typeof dialog) => {
    setActiveUser(u)
    setMenuOpen(null)
    setDialog(d)
    setDeactivateReason('')
    setResetResult(null)
  }

  const withEtag = async (id: number) => {
    const detail = await getUser(id)
    return detail.etag ?? `"${detail.updated_at ?? detail.created_at ?? ''}"`
  }

  const generatePassword = () => {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*'
    let pw = ''
    for (let i = 0; i < 12; i++) pw += chars[Math.floor(Math.random() * chars.length)]
    return pw
  }

  const handleDeactivate = async () => {
    if (!activeUser) return
    if (!deactivateReason.trim()) return
    setActionLoading(true)
    try {
      await deactivateUser(activeUser.id, deactivateReason.trim())
      toast(t('users.deactivated', { name: activeUser.full_name || activeUser.username }))
      setDialog(null)
      fetchUsers()
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === 'last_active_owner_protected') toast(t('errors.lastActiveOwnerProtected'))
        else if (err.code === 'idempotency_violation') toast(t('errors.idempotencyViolation'))
        else toast(err.message)
      } else {
        toast(t('users.deactivateFailed'))
      }
    } finally {
      setActionLoading(false)
    }
  }

  const handleActivate = async () => {
    if (!activeUser) return
    setActionLoading(true)
    try {
      const etag = await withEtag(activeUser.id)
      await activateUser(activeUser.id, etag)
      toast(t('users.activated', { name: activeUser.full_name || activeUser.username }))
      setDialog(null)
      fetchUsers()
    } catch (err) {
      if (isApiError(err)) {
        if (err.status === 412) toast(t('errors.preconditionFailed'))
        else if (err.code === 'idempotency_violation') toast(t('errors.idempotencyViolation'))
        else toast(err.message)
      } else {
        toast(t('users.activateFailed'))
      }
    } finally {
      setActionLoading(false)
    }
  }

  const handleUnlock = async () => {
    if (!activeUser) return
    setActionLoading(true)
    try {
      await unlockUser(activeUser.id)
      toast(t('users.unlocked', { name: activeUser.full_name || activeUser.username }))
      setDialog(null)
      fetchUsers()
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === 'idempotency_violation') toast(t('errors.idempotencyViolation'))
        else toast(err.message)
      } else {
        toast(t('users.unlockFailed'))
      }
    } finally {
      setActionLoading(false)
    }
  }

  const handleResetPassword = async () => {
    if (!activeUser) return
    setActionLoading(true)
    try {
      const pw = generatePassword()
      await resetPassword(activeUser.id, pw)
      setResetResult(pw)
      if (activeUser.id === user?.id) {
        toast(t('users.sessionRevokedRelogin'))
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        window.location.href = '/login'
      } else {
        toast(t('users.passwordReset'))
      }
      fetchUsers()
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === 'idempotency_violation') toast(t('errors.idempotencyViolation'))
        else toast(err.message)
      } else {
        toast(t('users.resetPasswordFailed'))
      }
    } finally {
      setActionLoading(false)
    }
  }

  return (
    <div className="page users-page">
      {notice && (
        <div className="notice success">
          <Check size={14} /> {notice}
        </div>
      )}

      <div className="page-header">
        <div>
          <div className="eyebrow">{t('users.eyebrow')}</div>
          <h1 className="page-title">{t('users.title')}</h1>
          <p className="page-subtitle">{t('users.subtitle')}</p>
        </div>
        {canCreate && (
          <button className="btn btn-primary" onClick={() => router.push('/users/new')}>
            <Plus size={16} /> {t('users.addUser')}
          </button>
        )}
      </div>

      <div className="filters">
        <div className="search-input">
          <Search size={16} />
          <input
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1) }}
            placeholder={t('users.searchPlaceholder')}
          />
        </div>
        <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value as any); setPage(1) }}>
          <option value="all">{t('users.allStatuses')}</option>
          <option value="active">{t('users.activeOnly')}</option>
          <option value="inactive">{t('users.inactiveOnly')}</option>
        </select>
        <select value={roleFilter} onChange={(e) => { setRoleFilter(e.target.value === 'all' ? 'all' : Number(e.target.value)); setPage(1) }}>
          <option value="all">{t('users.allRoles')}</option>
          {roles.map((r) => (
            <option key={r.id} value={r.id}>{r.name}</option>
          ))}
        </select>
        {(query || statusFilter !== 'all' || roleFilter !== 'all') && (
          <button className="btn btn-ghost" onClick={() => { setQuery(''); setStatusFilter('all'); setRoleFilter('all'); setPage(1) }}>
            <X size={14} /> {t('common.clearFilters')}
          </button>
        )}
      </div>

      {error && (
        <div className="notice error">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      <div className="table-wrap">
        <table className="data-table">
          <colgroup>
            <col className="user-col-name" />
            <col className="user-col-username" />
            <col className="user-col-email" />
            <col className="user-col-role" />
            <col className="user-col-status" />
            <col className="user-col-created" />
            <col className="user-col-actions" />
          </colgroup>
          <thead>
            <tr>
              <th className="sortable" onClick={() => handleSort('full_name')}>
                {t('users.fullName')} {sortKey === 'full_name' && <ChevronsUpDown size={14} className={sortDir} />}
              </th>
              <th className="sortable" onClick={() => handleSort('username')}>
                {t('users.username')} {sortKey === 'username' && <ChevronsUpDown size={14} className={sortDir} />}
              </th>
              <th>{t('common.email')}</th>
              <th>{t('users.role')}</th>
              <th>{t('common.status')}</th>
              <th className="sortable" onClick={() => handleSort('created_at')}>
                {t('users.createdAt')} {sortKey === 'created_at' && <ChevronsUpDown size={14} className={sortDir} />}
              </th>
              <th className="actions">{t('common.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {loading && users.length === 0 && (
              <tr><td colSpan={7} className="empty">{t('common.loading')}</td></tr>
            )}
            {!loading && users.length === 0 && (
              <tr><td colSpan={7} className="empty">{t('users.noUsersFound')}</td></tr>
            )}
            {users.map((u) => (
              <tr key={u.id}>
                <td title={u.full_name}><strong>{u.full_name}</strong></td>
                <td title={u.username}>{u.username}</td>
                <td title={u.email || '-'}>{u.email || '-'}</td>
                <td title={u.role_name}>{u.role_name}</td>
                <td>
                  <span className={`badge ${u.is_active ? 'active' : 'inactive'}`}>
                    {u.is_active ? t('status.active') : t('status.inactive')}
                  </span>
                </td>
                <td>{fmtDate(u.created_at)}</td>
                <td className="actions">
                  <div className="row-actions">
                    <button className="icon-btn" title={t('common.view')} onClick={() => router.push(`/users/${u.id}`)}>
                      <Eye size={16} />
                    </button>
                    {canEdit && (
                      <button className="icon-btn" title={t('common.edit')} onClick={() => router.push(`/users/${u.id}`)}>
                        <Pencil size={16} />
                      </button>
                    )}
                    <button className="icon-btn" onClick={() => setMenuOpen(menuOpen === u.id ? null : u.id)}>
                      <MoreHorizontal size={16} />
                    </button>
                    {menuOpen === u.id && (
                      <div className="dropdown">
                        {canManage && u.is_active && (
                          <button onClick={() => openDialog(u, 'deactivate')}><UserX size={14} /> {t('users.deactivate')}</button>
                        )}
                        {canManage && !u.is_active && (
                          <button onClick={() => openDialog(u, 'activate')}><UserCheck size={14} /> {t('users.activate')}</button>
                        )}
                        {canManage && isUserLocked(u) && (
                          <button onClick={() => openDialog(u, 'unlock')}><LockOpen size={14} /> {t('users.unlock')}</button>
                        )}
                        {canResetPassword && (
                          <button onClick={() => openDialog(u, 'reset-password')}><Lock size={14} /> {t('users.resetPassword')}</button>
                        )}
                      </div>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="pagination">
          <button disabled={page === 1} onClick={() => setPage(page - 1)}><ChevronLeft size={16} /></button>
          <span>{t('common.pageOf', { page, total: totalPages })}</span>
          <button disabled={page >= totalPages} onClick={() => setPage(page + 1)}><ChevronRight size={16} /></button>
        </div>
      )}

      {dialog === 'deactivate' && activeUser && (
        <div className="modal">
          <div className="modal-card">
            <h3>{t('users.deactivateTitle')}</h3>
            <p>{t('users.deactivateText', { name: activeUser.full_name || activeUser.username })}</p>
            <label>{t('users.deactivateReason')}</label>
            <textarea value={deactivateReason} onChange={(e) => setDeactivateReason(e.target.value)} />
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setDialog(null)}>{t('common.cancel')}</button>
              <button className="btn btn-danger" onClick={handleDeactivate} disabled={!deactivateReason.trim() || actionLoading}>
                {actionLoading ? t('common.saving') : t('users.deactivate')}
              </button>
            </div>
          </div>
        </div>
      )}

      {dialog === 'activate' && activeUser && (
        <div className="modal">
          <div className="modal-card">
            <h3>{t('users.activateTitle')}</h3>
            <p>{t('users.activateText', { name: activeUser.full_name || activeUser.username })}</p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setDialog(null)}>{t('common.cancel')}</button>
              <button className="btn btn-primary" onClick={handleActivate} disabled={actionLoading}>
                {actionLoading ? t('common.saving') : t('users.activate')}
              </button>
            </div>
          </div>
        </div>
      )}

      {dialog === 'unlock' && activeUser && (
        <div className="modal">
          <div className="modal-card">
            <h3>{t('users.unlockTitle')}</h3>
            <p>{t('users.unlockText', { name: activeUser.full_name || activeUser.username })}</p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setDialog(null)}>{t('common.cancel')}</button>
              <button className="btn btn-primary" onClick={handleUnlock} disabled={actionLoading}>
                {actionLoading ? t('common.saving') : t('users.unlock')}
              </button>
            </div>
          </div>
        </div>
      )}

      {dialog === 'reset-password' && activeUser && (
        <div className="modal">
          <div className="modal-card">
            <h3>{t('users.resetPasswordTitle')}</h3>
            <p>{t('users.resetPasswordText', { name: activeUser.full_name || activeUser.username })}</p>
            {resetResult && (
              <div className="notice">
                {t('users.temporaryPassword')}: <code>{resetResult}</code>
              </div>
            )}
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setDialog(null)}>{t('common.close')}</button>
              <button className="btn btn-primary" onClick={handleResetPassword} disabled={actionLoading || !!resetResult}>
                {actionLoading ? t('common.saving') : t('users.resetPassword')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
