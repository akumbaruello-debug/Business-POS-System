'use client'

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Pencil, Plus, Trash2 } from 'lucide-react'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import type { CostType } from '@/lib/production-types'

export default function CostTypesPage() {
  const user = useSession()
  const { t } = useLanguage()
  const canView = user.capabilities.includes('cost_type.view')
  const canManage = user.capabilities.includes('cost_type.manage')

  const [items, setItems] = useState<CostType[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<{ mode: 'add' | 'edit'; item?: CostType } | null>(null)
  const [busy, setBusy] = useState(false)

  const fetchItems = useCallback(async () => {
    if (!canView) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<{ data: CostType[] }>('/cost-types', { params: { per_page: '200' } })
      setItems(res.data)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('costTypes.failedToLoad'))
    } finally {
      setLoading(false)
    }
  }, [canView, t])

  useEffect(() => {
    fetchItems()
  }, [fetchItems])

  const save = async (code: string, name: string) => {
    setBusy(true)
    try {
      if (dialog?.mode === 'edit' && dialog.item) {
        await api.patch(`/cost-types/${dialog.item.id}`, { name: name.trim() }, {
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        })
      } else {
        await api.post('/cost-types', { code: code.trim(), name: name.trim() }, {
          headers: { 'Idempotency-Key': crypto.randomUUID() },
        })
      }
      setDialog(null)
      await fetchItems()
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('costTypes.failedToSave')
      alert(msg)
    } finally {
      setBusy(false)
    }
  }

  const deactivate = async (id: number) => {
    if (!confirm(t('costTypes.confirmDeactivate'))) return
    setBusy(true)
    try {
      await api.post(`/cost-types/${id}/deactivate`, {}, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
      await fetchItems()
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('costTypes.failedToDeactivate')
      alert(msg)
    } finally {
      setBusy(false)
    }
  }

  if (!canView) {
    return (
      <div className="content">
        <div className="page-heading"><h1>{t('costTypes.title')}</h1></div>
        <div className="error-state">
          <div className="error-icon"><AlertTriangle size={32} /></div>
          <strong>{t('errors.forbidden')}</strong>
          <p>{t('errors.lacksCapability', { capability: 'cost_type.view' })}</p>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="content">
        <div className="page-heading"><h1>{t('costTypes.title')}</h1></div>
        <div style={{ padding: 16 }}><div className="skeleton" style={{ height: 120, borderRadius: 8 }} /></div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="content">
        <div className="page-heading"><h1>{t('costTypes.title')}</h1></div>
        <div className="error-state">
          <strong>{t('costTypes.failedToLoad')}</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchItems}>{t('common.retry')}</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <h1>{t('costTypes.title')}</h1>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: '#718198' }}>{t('costTypes.subtitle')}</p>
        </div>
        {canManage && (
          <Button onClick={() => setDialog({ mode: 'add' })} disabled={busy}>
            <Plus size={14} className="mr-1" /> {t('costTypes.add')}
          </Button>
        )}
      </div>

      <div style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ background: '#f8fafc', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
              <th style={{ padding: '10px 16px', textAlign: 'left' }}>{t('costTypes.name')}</th>
              <th style={{ padding: '10px 16px', textAlign: 'left' }}>{t('common.code')}</th>
              <th style={{ padding: '10px 16px', textAlign: 'center' }}>{t('costTypes.status')}</th>
              {canManage && <th style={{ width: 100 }} />}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                <td style={{ padding: '10px 16px', fontWeight: 600 }}>{item.name}</td>
                <td style={{ padding: '10px 16px', color: '#475569', fontFamily: 'monospace', fontSize: 12 }}>{item.code}</td>
                <td style={{ padding: '10px 16px', textAlign: 'center' }}>
                  {item.is_active ? (
                    <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 999, background: '#dcfce7', color: '#166534', fontSize: 11, fontWeight: 700 }}>{t('status.active')}</span>
                  ) : (
                    <span style={{ display: 'inline-block', padding: '2px 8px', borderRadius: 999, background: '#f1f5f9', color: '#475569', fontSize: 11, fontWeight: 700 }}>{t('status.inactive')}</span>
                  )}
                </td>
                {canManage && (
                  <td style={{ padding: '10px 16px', textAlign: 'right' }}>
                    <button
                      onClick={() => setDialog({ mode: 'edit', item })}
                      style={{ border: 0, background: 'none', color: '#2563eb', marginRight: 8 }}
                      aria-label={t('common.edit')}
                      disabled={busy}
                    >
                      <Pencil size={14} />
                    </button>
                    {item.is_active && (
                      <button
                        onClick={() => deactivate(item.id)}
                        style={{ border: 0, background: 'none', color: '#dc2626' }}
                        aria-label={t('costTypes.deactivate')}
                        disabled={busy}
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {items.length === 0 && (
              <tr>
                <td colSpan={canManage ? 4 : 3} style={{ padding: 30, textAlign: 'center', color: '#94a3b8' }}>
                  {t('costTypes.empty')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {dialog && (
        <CostTypeDialog
          mode={dialog.mode}
          item={dialog.item}
          t={t}
          busy={busy}
          onCancel={() => setDialog(null)}
          onSave={save}
        />
      )}
    </div>
  )
}

function CostTypeDialog({
  mode,
  item,
  t,
  busy,
  onCancel,
  onSave,
}: {
  mode: 'add' | 'edit'
  item?: CostType
  t: (key: string, params?: Record<string, string | number>) => string
  busy: boolean
  onCancel: () => void
  onSave: (code: string, name: string) => void
}) {
  const [code, setCode] = useState(item?.code ?? '')
  const [name, setName] = useState(item?.name ?? '')

  const label: React.CSSProperties = {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    color: '#8a98ab',
  }
  const input: React.CSSProperties = {
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
      onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 480,
          background: 'white',
          borderRadius: 14,
          border: '1px solid var(--border)',
          boxShadow: '0 20px 48px rgba(0,0,0,.12)',
          margin: '40px 0',
        }}
      >
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>
            {mode === 'add' ? t('costTypes.add') : t('costTypes.edit')}
          </h2>
        </div>
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={label}>{t('common.code')} *</label>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))}
              maxLength={50}
              disabled={mode === 'edit'}
              style={input}
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={label}>{t('costTypes.name')} *</label>
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} style={input} />
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '14px 20px', borderTop: '1px solid var(--border)' }}>
          <Button variant="outline" onClick={onCancel} disabled={busy}>{t('common.cancel')}</Button>
          <Button
            onClick={() => onSave(code, name)}
            disabled={busy || !code.trim() || !name.trim()}
          >
            {busy ? t('common.saving') : t('common.save')}
          </Button>
        </div>
      </div>
    </div>
  )
}
