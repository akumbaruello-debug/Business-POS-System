'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useLanguage } from '@/lib/i18n'
import { isApiError } from '@/lib/api-client'
import { listRoles, type Role } from '@/lib/roles-service'
import { createUser, type CreateUserBody } from '@/lib/users-service'
import { ArrowLeft, Save, UserPlus } from 'lucide-react'

export default function NewUserPageClient() {
  const { t } = useLanguage()
  const router = useRouter()
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [roles, setRoles] = useState<Role[]>([])

  const [form, setForm] = useState({
    username: '',
    full_name: '',
    email: '',
    password: '',
    role_id: '',
  })

  useEffect(() => {
    listRoles({ page_size: 100 }).then((res) => setRoles(res.items)).catch(() => setRoles([]))
  }, [])

  const update = (field: keyof typeof form, value: string) => {
    setForm((f) => ({ ...f, [field]: value }))
    setErrors((e) => ({ ...e, [field]: '' }))
  }

  const validate = () => {
    const next: Record<string, string> = {}
    if (!form.username.trim()) next.username = t('validation.required')
    if (!form.full_name.trim()) next.full_name = t('validation.required')
    if (!form.password || form.password.length < 8) next.password = t('validation.minLength', { min: 8 })
    if (!form.role_id) next.role_id = t('validation.required')
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return
    setSaving(true)
    setFormError(null)
    try {
      await createUser({
        username: form.username,
        full_name: form.full_name,
        email: form.email || null,
        password: form.password,
        role_id: Number(form.role_id),
      })
      router.push('/users')
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
    <div className="page form-page admin-form-page user-create-page">
      <button className="btn btn-ghost back-link" onClick={() => router.push('/users')}>
        <ArrowLeft size={16} /> {t('common.back')}
      </button>

      <div className="page-header compact">
        <div>
          <div className="eyebrow">{t('users.eyebrow')}</div>
          <h1 className="page-title">{t('users.addUser')}</h1>
          <p className="page-subtitle">{t('users.createSubtitle')}</p>
        </div>
      </div>

      {formError && <div className="notice error">{formError}</div>}

      <form className="card form-card admin-form-card" onSubmit={handleSubmit}>
        <div className="form-section admin-form-section">
          <div className="admin-form-section-heading">
            <span className="admin-form-icon"><UserPlus size={18} /></span>
            <div>
              <h3>{t('users.accountInfo')}</h3>
              <p>{t('users.createIntro')}</p>
            </div>
            <span className="required-note">{t('users.requiredFields')}</span>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="new-user-username">{t('users.username')} <span>*</span></label>
              <input id="new-user-username" autoComplete="username" required aria-invalid={!!errors.username} value={form.username} onChange={(e) => update('username', e.target.value)} />
              {errors.username && <span className="field-error">{errors.username}</span>}
            </div>
            <div className="field">
              <label htmlFor="new-user-full-name">{t('users.fullName')} <span>*</span></label>
              <input id="new-user-full-name" autoComplete="name" required aria-invalid={!!errors.full_name} value={form.full_name} onChange={(e) => update('full_name', e.target.value)} />
              {errors.full_name && <span className="field-error">{errors.full_name}</span>}
            </div>
            <div className="field full">
              <label htmlFor="new-user-email">{t('users.email')} <span className="optional-label">{t('users.optional')}</span></label>
              <input id="new-user-email" type="email" autoComplete="email" value={form.email || ''} onChange={(e) => update('email', e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="new-user-password">{t('users.password')} <span>*</span></label>
              <input id="new-user-password" type="password" autoComplete="new-password" minLength={8} required aria-invalid={!!errors.password} value={form.password} onChange={(e) => update('password', e.target.value)} />
              <small className="field-hint">{t('users.passwordHint')}</small>
              {errors.password && <span className="field-error">{errors.password}</span>}
            </div>
            <div className="field">
              <label htmlFor="new-user-role">{t('users.role')} <span>*</span></label>
              <select id="new-user-role" required aria-invalid={!!errors.role_id} value={String(form.role_id)} onChange={(e) => update('role_id', e.target.value)}>
                <option value="">{t('users.selectRole')}</option>
                {roles.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
              {errors.role_id && <span className="field-error">{errors.role_id}</span>}
            </div>
          </div>
        </div>

        <div className="form-actions admin-form-actions">
          <button type="button" className="btn btn-ghost" onClick={() => router.push('/users')} disabled={saving}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            <Save size={16} /> {saving ? t('common.creating') : t('users.createUser')}
          </button>
        </div>
      </form>
    </div>
  )
}
