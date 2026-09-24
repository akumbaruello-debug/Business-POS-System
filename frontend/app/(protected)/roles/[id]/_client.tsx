'use client'

import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useLanguage } from '@/lib/i18n'
import { useCan } from '@/lib/authz'
import { isApiError } from '@/lib/api-client'
import { getRole, updateRole, replaceRoleCapabilities, isSystemRole, roleCapabilities, type Role } from '@/lib/roles-service'
import { CapabilityPicker } from '@/components/capability-picker'
import { AlertTriangle, ArrowLeft, Lock, Save, Shield } from 'lucide-react'

export default function RoleDetailPageClient({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const roleId = Number(id)
  const { t } = useLanguage()
  const router = useRouter()
  const { can } = useCan()

  const [role, setRole] = useState<Role | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [capabilities, setCapabilities] = useState<string[]>([])

  const canEdit = can('role.manage')
  const canEditCaps = can('role.manage')

  const fetchRole = async () => {
    setLoading(true)
    try {
      const r = await getRole(roleId)
      setRole(r)
      setName(r.name)
      setDescription(r.description || '')
      setCapabilities(roleCapabilities(r))
    } catch (err) {
      setFormError(isApiError(err) ? err.message : t('common.error'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchRole()
  }, [roleId])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!role || role.is_system || !canEdit) return
    setSaving(true)
    setFormError(null)
    try {
      await updateRole(roleId, role.etag ?? role.updated_at ?? role.created_at ?? '', { name, description })
      await fetchRole()
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === 'system_role_immutable') setFormError(t('errors.systemRoleImmutable'))
        else if (err.status === 412) setFormError(t('errors.preconditionFailed'))
        else setFormError(err.message)
      } else {
        setFormError(t('common.error'))
      }
    } finally {
      setSaving(false)
    }
  }

  const handleCapabilitiesSave = async () => {
    if (!role || role.is_system || !canEditCaps) return
    setSaving(true)
    setFormError(null)
    try {
      await replaceRoleCapabilities(roleId, role.etag ?? role.updated_at ?? role.created_at ?? '', capabilities)
      await fetchRole()
    } catch (err) {
      if (isApiError(err)) {
        if (err.code === 'system_role_immutable') setFormError(t('errors.systemRoleImmutable'))
        else if (err.status === 412) setFormError(t('errors.preconditionFailed'))
        else setFormError(err.message)
      } else {
        setFormError(t('common.error'))
      }
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="page">{t('common.loading')}</div>
  if (!role) return <div className="page"><div className="notice error">{formError || t('roles.notFound')}</div></div>

  return (
    <div className="page detail-page">
      <button className="btn btn-ghost back-link" onClick={() => router.push('/roles')}>
        <ArrowLeft size={16} /> {t('common.back')}
      </button>

      <div className="page-header">
        <div>
          <div className="eyebrow">{t('roles.eyebrow')}</div>
          <h1 className="page-title">{role.name}</h1>
          <div className="subtitle">
            {role.is_system ? (
              <><Lock size={14} /> {t('roles.systemRoleReadOnly')}</>
            ) : t('roles.custom')}
          </div>
        </div>
      </div>

      {formError && <div className="notice error">{formError}</div>}

      <form className="card form-card" onSubmit={handleSave}>
        <div className="form-section">
          <h3><Shield size={18} /> {t('roles.roleInfo')}</h3>
          <div className="form-grid">
            <div className="field">
              <label>{t('roles.name')}</label>
              <input value={name} disabled={role.is_system || !canEdit} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field full">
              <label>{t('roles.description')}</label>
              <textarea value={description} disabled={role.is_system || !canEdit} onChange={(e) => setDescription(e.target.value)} />
            </div>
          </div>
        </div>

        {!role.is_system && canEdit && (
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={saving}>
              <Save size={16} /> {saving ? t('common.saving') : t('common.save')}
            </button>
          </div>
        )}
      </form>

      <div className="card form-card">
        <div className="form-section">
          <h3><Shield size={18} /> {t('roles.capabilities')}</h3>
          {role.is_system && (
            <div className="notice warning">
              <AlertTriangle size={16} /> {t('roles.systemRoleCapabilitiesReadOnly')}
            </div>
          )}
          <CapabilityPicker
            selected={capabilities}
            onChange={setCapabilities}
            disabled={role.is_system || !canEditCaps}
            readOnly={role.is_system || !canEditCaps}
          />
        </div>
        {!role.is_system && canEditCaps && (
          <div className="form-actions">
            <button className="btn btn-primary" onClick={handleCapabilitiesSave} disabled={saving}>
              <Save size={16} /> {saving ? t('common.saving') : t('roles.saveCapabilities')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
