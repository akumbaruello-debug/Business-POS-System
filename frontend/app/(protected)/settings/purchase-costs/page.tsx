'use client'

import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Pencil, Plus } from 'lucide-react'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'

type CostCategory = { id: number; name: string; capitalizable: boolean; financial_category_id: number | null; is_active: boolean }
type FinancialCategory = { id: number; name: string; entry_type: string; is_active: boolean }
type SettingsEnvelope = { settings: Record<string, unknown> }

export default function PurchaseCostsSettingsPage() {
  const user = useSession()
  const { t } = useLanguage()
  const canView = user.capabilities.includes('settings.view')
  const canManage = user.capabilities.includes('settings.manage')
  const [items, setItems] = useState<CostCategory[]>([])
  const [expenses, setExpenses] = useState<FinancialCategory[]>([])
  const [name, setName] = useState('')
  const [capitalizable, setCapitalizable] = useState(true)
  const [financialCategoryId, setFinancialCategoryId] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [allocation, setAllocation] = useState('line_value')
  const [etag, setEtag] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!canView) { setLoading(false); return }
    setLoading(true)
    setError('')
    try {
      const [categories, finance, settings] = await Promise.all([
        api.get<{ data: CostCategory[] }>('/purchase-cost-categories'),
        api.get<{ data: FinancialCategory[] }>('/financial-categories/', { params: { per_page: 200, 'filter[is_active]': true } }),
        api.headers.get<SettingsEnvelope>('/settings'),
      ])
      setItems(categories.data)
      setExpenses(finance.data.filter((item) => item.entry_type === 'expense' && item.is_active))
      setAllocation(String(settings.data.settings.default_purchase_cost_allocation ?? 'line_value'))
      setEtag(settings.headers.get('ETag') ?? '')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('purchaseCosts.loadFailed'))
    } finally { setLoading(false) }
  }, [canView, t])

  useEffect(() => { void load() }, [load])

  const saveDefault = async () => {
    setBusy(true)
    try {
      const result = await api.headers.patch<SettingsEnvelope>('/settings', { default_purchase_cost_allocation: allocation }, {
        headers: { 'If-Match': etag, 'Idempotency-Key': crypto.randomUUID() },
      })
      setEtag(result.headers.get('ETag') ?? etag)
    } catch (err) { setError(err instanceof Error ? err.message : t('purchaseCosts.saveFailed')) }
    finally { setBusy(false) }
  }

  const saveCategory = async () => {
    setBusy(true)
    try {
      const payload = {
        name: name.trim(), capitalizable,
        financial_category_id: capitalizable ? null : Number(financialCategoryId),
      }
      if (editingId === null) {
        await api.post('/purchase-cost-categories', payload, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
      } else {
        await api.patch(`/purchase-cost-categories/${editingId}`, payload)
      }
      setName('')
      setEditingId(null)
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : t('purchaseCosts.saveFailed')) }
    finally { setBusy(false) }
  }

  if (!canView) return <div className="content"><div className="error-state"><AlertTriangle size={28} /><strong>{t('errors.forbidden')}</strong></div></div>
  return <main className="content">
    <div className="page-heading"><div><h1>{t('purchaseCosts.title')}</h1><p style={{ margin: '6px 0 0', fontSize: 13, color: '#718198' }}>{t('purchaseCosts.description')}</p></div></div>
    {error && <div className="error-state" style={{ marginBottom: 16 }}><strong>{error}</strong><Button variant="outline" onClick={() => void load()}>{t('common.retry')}</Button></div>}
    {loading ? <div className="skeleton" style={{ height: 120, borderRadius: 8 }} /> : <>
      <section style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, margin: '0 0 8px' }}>{t('purchaseCosts.defaultAllocation')}</h2>
        <p style={{ fontSize: 12, color: '#718198' }}>{t('purchaseCosts.defaultAllocationHint')}</p>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <select value={allocation} onChange={(event) => setAllocation(event.target.value)} disabled={!canManage || busy} style={{ minWidth: 220, padding: 9, border: '1px solid var(--border)', borderRadius: 8 }}>
            <option value="line_value">{t('purchaseCosts.lineValue')}</option><option value="weight">{t('purchaseCosts.weight')}</option><option value="manual">{t('purchaseCosts.manual')}</option>
          </select>
          {canManage && <Button onClick={() => void saveDefault()} disabled={busy || !etag}>{t('common.save')}</Button>}
        </div>
      </section>
      {canManage && <section style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, margin: '0 0 12px' }}>{editingId === null ? t('purchaseCosts.addCategory') : t('purchaseCosts.editCategory')}</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px,1fr) minmax(180px,1fr) auto', gap: 10, alignItems: 'end' }}>
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>{t('purchaseCosts.categoryName')}<input value={name} onChange={(event) => setName(event.target.value)} maxLength={100} style={{ padding: 9, border: '1px solid var(--border)', borderRadius: 8 }} /></label>
          <label style={{ display: 'grid', gap: 5, fontSize: 12 }}>{t('purchaseCosts.treatment')}<select value={capitalizable ? 'capitalizable' : 'expense'} onChange={(event) => setCapitalizable(event.target.value === 'capitalizable')} style={{ padding: 9, border: '1px solid var(--border)', borderRadius: 8 }}><option value="capitalizable">{t('purchaseCosts.capitalizable')}</option><option value="expense">{t('purchaseCosts.operatingExpense')}</option></select></label>
          <div style={{ display: 'flex', gap: 8 }}><Button onClick={() => void saveCategory()} disabled={busy || !name.trim() || (!capitalizable && !financialCategoryId)}>{editingId === null && <Plus size={14} className="mr-1" />}{editingId === null ? t('purchaseCosts.addCategory') : t('common.save')}</Button>{editingId !== null && <Button variant="outline" onClick={() => { setEditingId(null); setName(''); setCapitalizable(true); setFinancialCategoryId('') }} disabled={busy}>{t('common.cancel')}</Button>}</div>
          {!capitalizable && <label style={{ gridColumn: '1 / 3', display: 'grid', gap: 5, fontSize: 12 }}>{t('purchaseCosts.expenseMapping')}<select value={financialCategoryId} onChange={(event) => setFinancialCategoryId(event.target.value)} style={{ padding: 9, border: '1px solid var(--border)', borderRadius: 8 }}><option value="">{t('purchaseCosts.selectExpense')}</option>{expenses.map((expense) => <option key={expense.id} value={expense.id}>{expense.name}</option>)}</select></label>}
        </div>
      </section>}
      <section style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}><thead><tr style={{ background: '#f8fafc', textAlign: 'left', color: '#64748b' }}><th style={{ padding: 12 }}>{t('purchaseCosts.categoryName')}</th><th style={{ padding: 12 }}>{t('purchaseCosts.treatment')}</th><th style={{ padding: 12 }}>{t('purchaseCosts.expenseMapping')}</th><th style={{ padding: 12 }}>{t('purchaseCosts.status')}</th>{canManage && <th />}</tr></thead><tbody>{items.map((item) => <tr key={item.id} style={{ borderTop: '1px solid #f0f4f9' }}><td style={{ padding: 12, fontWeight: 600 }}>{item.name}</td><td style={{ padding: 12 }}>{item.capitalizable ? t('purchaseCosts.capitalizable') : t('purchaseCosts.operatingExpense')}</td><td style={{ padding: 12 }}>{expenses.find((expense) => expense.id === item.financial_category_id)?.name ?? '—'}</td><td style={{ padding: 12 }}>{item.is_active ? t('status.active') : t('status.inactive')}</td>{canManage && <td style={{ padding: 8, textAlign: 'right' }}><button aria-label={t('common.edit')} onClick={() => { setEditingId(item.id); setName(item.name); setCapitalizable(item.capitalizable); setFinancialCategoryId(item.financial_category_id ? String(item.financial_category_id) : '') }} style={{ border: 0, background: 'none', color: '#2563eb' }}><Pencil size={14} /></button></td>}</tr>)}{!items.length && <tr><td colSpan={canManage ? 5 : 4} style={{ padding: 24, textAlign: 'center', color: '#718198' }}>{t('purchaseCosts.empty')}</td></tr>}</tbody></table></section>
    </>}
  </main>
}
