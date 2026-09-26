'use client'

import { use, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useLanguage } from '@/lib/i18n'
import { useCan } from '@/lib/authz'
import { isApiError } from '@/lib/api-client'
import { listRoles, type Role } from '@/lib/roles-service'
import { getUser, getUserCapabilities, updateUser, deactivateUser, activateUser, unlockUser, resetPassword, grantCapability, revokeCapability, isUserLocked, type UserDetail, type UserCapabilities } from '@/lib/users-service'
import { CapabilityPicker } from '@/components/capability-picker'
import { AlertTriangle, ArrowLeft, Check, KeyRound, Lock, LockOpen, Save, Shield, Trash2, UserCog, X } from 'lucide-react'

interface Params {
  id: string
}

export default function UserDetailPageClient({ params }: { params: Promise<Params> }) {
  const { id } = use(params)
  const userId = Number(id)
  const { t } = useLanguage()
  const router = useRouter()
  const { can } = useCan()

  const [user, setUser] = useState<UserDetail | null>(null)
  const [caps, setCaps] = useState<UserCapabilities | null>(null)
  const [roles, setRoles] = useState<Role[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState<false | 'deactivate' | 'reset' | 'unlock'>(false)
  const [reason, setReason] = useState('')
  const [overrideCaps, setOverrideCaps] = useState<string[]>([])

  const [form, setForm] = useState({
    username: '',
    full_name: '',
    email: '',
    role_id: '',
  })

  const canEdit = can('user.manage')
  const canDeactivate = can('user.manage')
  const canActivate = can('user.manage')
  const canUnlock = can('user.manage')
  const canReset = can('auth.password_reset_others')
  // Grant and revoke are independent backend capabilities — never AND them,
  // or a user holding only one loses both actions.
  const canGrant = can('user.grant_capability')
  const canRevoke = can('user.revoke_capability')
  const canOverride = canGrant || canRevoke

  const fetchData = async () => {
    setLoading(true)
    try {
      const [u, c] = await Promise.all([getUser(userId), getUserCapabilities(userId)])
      setUser(u)
      setCaps(c)
      setForm({
        username: u.username,
        full_name: u.full_name,
        email: u.email || '',
        role_id: String(u.role_id || ''),
      })
      const granted = (c.overrides || []).filter((o) => o.is_granted).map((o) => o.capability_code)
      setOverrideCaps(granted)
    } catch (err) {
      setFormError(isApiError(err) ? err.message : t('common.error'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    listRoles({ page_size: 100 }).then((res) => setRoles(res.items)).catch(() => setRoles([]))
  }, [userId])

  const effectiveCapabilities = useMemo<string[]>(() => {
    return caps?.effective || []
  }, [caps])

  // Canonical If-Match source: the ETag captured on fetch, refreshed after
  // every mutation via fetchData — never a stale copy.
  const currentEtag = () => user?.etag ?? `"${user?.updated_at ?? user?.created_at ?? ''}"`

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!user || !canEdit) return
    setSaving(true)
    setFormError(null)
    try {
      await updateUser(userId, currentEtag(), {
        full_name: form.full_name,
        email: form.email || null,
        role_id: form.role_id ? Number(form.role_id) : null,
      })
      await fetchData()
    } catch (err) {
      if (isApiError(err)) {
        if (err.status === 412) setFormError(t('errors.preconditionFailed'))
        else setFormError(err.message)
      } else {
        setFormError(t('common.error'))
      }
    } finally {
      setSaving(false)
    }
  }

  const generatePassword = () => {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*'
    let pw = ''
    for (let i = 0; i < 12; i++) pw += chars[Math.floor(Math.random() * chars.length)]
    return pw
  }

  const handleToggleActive = async () => {
    if (!user) return
    setSaving(true)
    setFormError(null)
    try {
      if (user.is_active) {
        await deactivateUser(userId, reason || undefined)
      } else {
        await activateUser(userId, currentEtag())
      }
      setConfirmOpen(false)
      setReason('')
      await fetchData()
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === 'last_active_owner_protected') setFormError(t('errors.lastActiveOwnerProtected'))
        else if (err.status === 412) setFormError(t('errors.preconditionFailed'))
        else setFormError(err.message)
      } else {
        setFormError(t('common.error'))
      }
    } finally {
      setSaving(false)
    }
  }

  const handleUnlock = async () => {
    if (!user) return
    setSaving(true)
    setFormError(null)
    try {
      await unlockUser(userId)
      setConfirmOpen(false)
      await fetchData()
    } catch (err) {
      setFormError(isApiError(err) ? err.message : t('common.error'))
    } finally {
      setSaving(false)
    }
  }

  const handleResetPassword = async () => {
    if (!user) return
    setSaving(true)
    setFormError(null)
    try {
      const pw = generatePassword()
      await resetPassword(userId, pw)
      setConfirmOpen(false)
      setFormError(t('users.passwordResetSuccess') + ' ' + pw)
    } catch (err) {
      setFormError(isApiError(err) ? (err as { message: string }).message : t('common.error'))
    } finally {
      setSaving(false)
    }
  }

  const handleOverrideSave = async () => {
    if (!user || !canOverride) return
    setSaving(true)
    setFormError(null)
    try {
      const etag = currentEtag()
      const currentGranted = (caps?.overrides || []).filter((o) => o.is_granted).map((o) => o.capability_code)
      // Each direction requires its own capability; attempting an
      // unauthorized direction would fail server-side, so skip it locally.
      const toAdd = canGrant ? overrideCaps.filter((c) => !currentGranted.includes(c)) : []
      const toRemove = canRevoke ? currentGranted.filter((c) => !overrideCaps.includes(c)) : []
      await Promise.all([
        ...toAdd.map((code) => grantCapability(userId, etag, code)),
        ...toRemove.map((code) => revokeCapability(userId, code)),
      ])
      await fetchData()
    } catch (err) {
      setFormError(isApiError(err) ? (err as { message: string }).message : t('common.error'))
    } finally {
      setSaving(false)
    }
  }

  const runConfirmed = () => {
    if (confirmOpen === 'deactivate' || confirmOpen === 'unlock') {
      handleToggleActive()
    } else if (confirmOpen === 'reset') {
      handleResetPassword()
    }
  }

  if (loading) return <div className="page">{t('common.loading')}</div>
  if (!user) return <div className="page"><div className="notice error">{formError || t('users.notFound')}</div></div>

  return (
    <div className="page detail-page">
      <button className="btn btn-ghost back-link" onClick={() => router.push('/users')}>
        <ArrowLeft size={16} /> {t('common.back')}
      </button>

      <div className="page-header">
        <div>
          <div className="eyebrow">{t('users.eyebrow')}</div>
          <h1 className="page-title">{user.full_name}</h1>
          <div className="subtitle">@{user.username} • {user.role_name || '—'}</div>
        </div>
        <div className="header-actions">
          {!user.is_active ? (
            canActivate && (
              <button className="btn btn-success" onClick={() => setConfirmOpen('deactivate')} disabled={saving}>
                <Check size={16} /> {t('users.activate')}
              </button>
            )
          ) : (
            canDeactivate && (
              <button className="btn btn-warning" onClick={() => setConfirmOpen('deactivate')} disabled={saving}>
                <X size={16} /> {t('users.deactivate')}
              </button>
            )
          )}
          {isUserLocked(user) && canUnlock && (
            <button className="btn btn-ghost" onClick={() => setConfirmOpen('unlock')} disabled={saving}>
              <LockOpen size={16} /> {t('users.unlock')}
            </button>
          )}
          {canReset && (
            <button className="btn btn-ghost" onClick={() => setConfirmOpen('reset')} disabled={saving}>
              <KeyRound size={16} /> {t('users.resetPassword')}
            </button>
          )}
        </div>
      </div>

      {formError && <div className="notice error">{formError}</div>}

      <form className="card form-card" onSubmit={handleSave}>
        <div className="form-section">
          <h3><UserCog size={18} /> {t('users.accountInfo')}</h3>
          <div className="form-grid">
            <div className="field">
              <label>{t('users.username')}</label>
              {/* Username is immutable via PATCH /users/{id} (UserUpdateRequest
                  has no username field) — always read-only, never submitted. */}
              <input value={form.username} disabled readOnly />
            </div>
            <div className="field">
              <label>{t('users.fullName')}</label>
              <input value={form.full_name} disabled={!canEdit} onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))} />
            </div>
            <div className="field full">
              <label>{t('users.email')}</label>
              <input type="email" value={form.email} disabled={!canEdit} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
            </div>
            <div className="field">
              <label>{t('users.role')}</label>
              <select value={form.role_id} disabled={!canEdit} onChange={(e) => setForm((f) => ({ ...f, role_id: e.target.value }))}>
                <option value="">{t('users.selectRole')}</option>
                {roles.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {canEdit && (
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              <Save size={16} /> {saving ? t('common.saving') : t('common.save')}
            </button>
          </div>
        )}
      </form>

      <div className="card form-card">
        <div className="form-section">
          <h3><Shield size={18} /> {t('users.capabilities')}</h3>
          <div className="capability-section">
            <div>
              <h4>{t('users.roleCapabilities')}</h4>
              <div className="cap-tags">
                {(caps?.role_capabilities || []).map((c) => (
                  <span key={c} className="badge neutral">{c}</span>
                ))}
              </div>
            </div>
            <div>
              <h4>{t('users.effectiveCapabilities')}</h4>
              <div className="cap-tags">
                {effectiveCapabilities.map((c) => (
                  <span key={c} className="badge success">{c}</span>
                ))}
              </div>
            </div>
          </div>

          {canOverride && (
            <>
              <h4>{t('users.overrideCapabilities')}</h4>
              <CapabilityPicker selected={overrideCaps} onChange={setOverrideCaps} />
              <div className="form-actions">
                <button className="btn btn-primary" onClick={handleOverrideSave} disabled={saving}>
                  <Save size={16} /> {t('users.saveOverrides')}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {confirmOpen && (
        <div className="modal-overlay" onClick={() => setConfirmOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>
              {confirmOpen === 'deactivate' && (user.is_active ? t('users.confirmDeactivate') : t('users.confirmActivate'))}
              {confirmOpen === 'reset' && t('users.confirmResetPassword')}
              {confirmOpen === 'unlock' && t('users.confirmUnlock')}
            </h3>
            {confirmOpen === 'deactivate' && user.is_active && (
              <div className="field">
                <label>{t('users.deactivateReason')}</label>
                <textarea value={reason} onChange={(e) => setReason(e.target.value)} />
              </div>
            )}
            <p className="modal-note"><AlertTriangle size={16} /> {t('users.actionCannotBeUndone')}</p>
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setConfirmOpen(false)}>{t('common.cancel')}</button>
              <button className="btn btn-danger" onClick={runConfirmed} disabled={saving}>
                {saving ? t('common.processing') : t('common.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
