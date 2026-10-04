'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useLanguage } from '@/lib/i18n'
import { isApiError } from '@/lib/api-client'
import { createRole, replaceRoleCapabilities, type CreateRoleBody } from '@/lib/roles-service'
import { CapabilityPicker } from '@/components/capability-picker'
import { ArrowLeft, Save, ShieldPlus } from 'lucide-react'

export default function NewRolePageClient() {
  const { t } = useLanguage()
  const router = useRouter()
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const [form, setForm] = useState<CreateRoleBody>({ name: '', description: '' })
  const [capabilities, setCapabilities] = useState<string[]>([])

  const update = (field: keyof CreateRoleBody, value: string) => {
    setForm((f) => ({ ...f, [field]: value }))
    setErrors((e) => ({ ...e, [field]: '' }))
  }

  const validate = () => {
    const next: Record<string, string> = {}
    if (!form.name.trim()) next.name = t('validation.required')
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return
    setSaving(true)
    setFormError(null)
    try {
      // POST /roles accepts name/description only (RoleCreateRequest,
      // extra=forbid) — capabilities are applied right after via the
      // existing PUT /roles/{id}/capabilities replace endpoint.
      const created = await createRole({
        name: form.name.trim(),
        description: form.description?.trim() ? form.description.trim() : null,
      })
      if (capabilities.length > 0) {
        await replaceRoleCapabilities(created.id, '', capabilities)
      }
      router.push('/roles')
    } catch (err) {
      if (isApiError(err)) {
        if (err.status === 412) setFormError(t('errors.preconditionFailed'))
        else if (err.code === 'idempotency_violation') setFormError(t('errors.idempotencyViolation'))
        else setFormError(err.message)
      } else {
        setFormError(t('common.error'))
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page form-page admin-form-page role-create-page">
      <button className="btn btn-ghost back-link" onClick={() => router.push('/roles')}>
        <ArrowLeft size={16} /> {t('common.back')}
      </button>

      <div className="page-header compact">
        <div>
          <div className="eyebrow">{t('roles.eyebrow')}</div>
          <h1 className="page-title">{t('roles.addRole')}</h1>
          <p className="page-subtitle">{t('roles.createSubtitle')}</p>
        </div>
      </div>

      {formError && <div className="notice error">{formError}</div>}

      <form className="card form-card admin-form-card" onSubmit={handleSubmit}>
        <div className="form-section admin-form-section">
          <div className="admin-form-section-heading">
            <span className="admin-form-icon"><ShieldPlus size={18} /></span>
            <div>
              <h3>{t('roles.roleInfo')}</h3>
              <p>{t('roles.createIntro')}</p>
            </div>
            <span className="required-note">{t('users.requiredFields')}</span>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="new-role-name">{t('roles.name')} <span>*</span></label>
              <input id="new-role-name" required aria-invalid={!!errors.name} value={form.name} onChange={(e) => update('name', e.target.value)} />
              {errors.name && <span className="field-error">{errors.name}</span>}
            </div>
            <div className="field full">
              <label htmlFor="new-role-description">{t('roles.description')} <span className="optional-label">{t('users.optional')}</span></label>
              <textarea id="new-role-description" rows={3} value={form.description || ''} onChange={(e) => update('description', e.target.value)} />
            </div>
          </div>
        </div>

        <div className="form-section admin-form-section capability-form-section">
          <div className="admin-form-section-heading capability-form-heading">
            <span className="admin-form-icon"><ShieldPlus size={18} /></span>
            <div>
              <h3>{t('roles.capabilities')}</h3>
              <p>{t('roles.createCapabilitiesHelp')}</p>
            </div>
            <span className="optional-badge">{capabilities.length} {t('roles.selectedCount')}</span>
          </div>
          <CapabilityPicker selected={capabilities} onChange={setCapabilities} />
        </div>

        <div className="form-actions admin-form-actions">
          <button type="button" className="btn btn-ghost" onClick={() => router.push('/roles')} disabled={saving}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            <Save size={16} /> {saving ? t('common.creating') : t('roles.createRole')}
          </button>
        </div>
      </form>
    </div>
  )
}
