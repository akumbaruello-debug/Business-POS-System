'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Check, CreditCard, Pencil, Plus, RefreshCw, Ruler, Search, Tags, X } from 'lucide-react'
import { api } from '@/lib/api-client'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import type { FinancialCategoryResponse, PaymentMethodResponse } from '@/lib/finance-types'

type MasterDataKind = 'units' | 'payment-methods' | 'financial-categories'
type UnitItem = { id: number; code: string; name: string; is_active: boolean; version?: number }
type MasterItem = UnitItem | PaymentMethodResponse | FinancialCategoryResponse
type DialogState = { mode: 'add' | 'edit'; item?: MasterItem } | null
const actionTitleKeys: Record<MasterDataKind, { add: string; edit: string; deactivate: string }> = {
  units: { add: 'settings.addUnit', edit: 'settings.editUnit', deactivate: 'settings.deactivateUnit' },
  'payment-methods': { add: 'settings.addPaymentMethod', edit: 'settings.editPaymentMethod', deactivate: 'settings.deactivatePaymentMethod' },
  'financial-categories': { add: 'settings.addFinancialCategory', edit: 'settings.editFinancialCategory', deactivate: 'settings.deactivateFinancialCategory' },
}

const definitions: Record<MasterDataKind, {
  titleKey: string
  descriptionKey: string
  capability: string
  endpoint: string
  icon: typeof Ruler
}> = {
  units: {
    titleKey: 'settings.units',
    descriptionKey: 'settings.unitsPageDescription',
    capability: 'unit.view',
    endpoint: '/units/',
    icon: Ruler,
  },
  'payment-methods': {
    titleKey: 'settings.paymentMethods',
    descriptionKey: 'settings.paymentMethodsPageDescription',
    capability: 'payment_method.view',
    endpoint: '/payment-methods/',
    icon: CreditCard,
  },
  'financial-categories': {
    titleKey: 'settings.financialCategories',
    descriptionKey: 'settings.financialCategoriesPageDescription',
    capability: 'financial_category.view',
    endpoint: '/financial-categories/',
    icon: Tags,
  },
}

function hasCashFlag(item: MasterItem): item is PaymentMethodResponse {
  return 'is_cash' in item
}

function hasEntryType(item: MasterItem): item is FinancialCategoryResponse {
  return 'entry_type' in item
}

export function MasterDataManager({ kind }: { kind: MasterDataKind }) {
  const { t } = useLanguage()
  const user = useSession()
  const definition = definitions[kind]
  const titleKeys = actionTitleKeys[kind]
  const Icon = definition.icon
  const canView = user.capabilities.includes(definition.capability)
  const canManage = user.capabilities.includes(definition.capability.replace('.view', '.manage'))

  const [items, setItems] = useState<MasterItem[]>([])
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dialog, setDialog] = useState<DialogState>(null)
  const [dialogError, setDialogError] = useState('')
  const [notice, setNotice] = useState('')

  const fetchItems = useCallback(async () => {
    if (!canView) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError('')
    try {
      const result = await api.get<{ data: MasterItem[] }>(definition.endpoint, {
        params: {
          per_page: 200,
          q: query.trim() || undefined,
          'filter[is_active]': statusFilter === 'active' ? true : undefined,
        },
      })
      setItems(result.data ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.masterDataLoadFailed'))
    } finally {
      setLoading(false)
    }
  }, [canView, definition.endpoint, query, statusFilter, t])

  useEffect(() => {
    const timer = window.setTimeout(() => { void fetchItems() }, query ? 220 : 0)
    return () => window.clearTimeout(timer)
  }, [fetchItems, query])

  const visibleItems = useMemo(
    () => statusFilter === 'inactive' ? items.filter((item) => !item.is_active) : items,
    [items, statusFilter],
  )

  const saveItem = async (values: { code: string; name: string; isCash: boolean; entryType: 'income' | 'expense' }) => {
    if (!canManage) return
    setBusy(true)
    setError('')
    setDialogError('')
    try {
      const item = dialog?.item
      if (kind === 'units') {
        if (item) await api.patch(`${definition.endpoint}${item.id}`, { name: values.name.trim() })
        else await api.post(definition.endpoint, { code: values.code.trim(), name: values.name.trim() }, { idempotencyKey: true })
      } else if (kind === 'payment-methods') {
        if (item) await api.patch(`${definition.endpoint}${item.id}`, { name: values.name.trim(), is_cash: values.isCash })
        else await api.post(definition.endpoint, { code: values.code.trim(), name: values.name.trim(), is_cash: values.isCash }, { idempotencyKey: true })
      } else if (item) {
        const detail = await api.headers.get<FinancialCategoryResponse>(`${definition.endpoint}${item.id}`)
        const etag = detail.headers.get('etag') ?? (detail.data.updated_at ? `"${detail.data.updated_at}"` : '')
        if (!etag) throw new Error(t('errors.preconditionFailed'))
        await api.patch(`${definition.endpoint}${item.id}`, { name: values.name.trim() }, { ifMatch: etag, idempotencyKey: true })
      } else {
        await api.post(definition.endpoint, {
          code: values.code.trim(),
          name: values.name.trim(),
          entry_type: values.entryType,
        }, { idempotencyKey: true })
      }
      setDialog(null)
      setNotice(t('settings.masterDataSaved'))
      await fetchItems()
    } catch (err) {
      setDialogError(err instanceof Error ? err.message : t('settings.masterDataSaveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const toggleActive = async (item: MasterItem) => {
    if (!canManage) return
    if (item.is_active && !window.confirm(t('settings.masterDataDeactivateConfirm', { name: item.name }))) return
    setBusy(true)
    setError('')
    try {
      if (kind === 'financial-categories') {
        const detail = await api.headers.get<FinancialCategoryResponse>(`${definition.endpoint}${item.id}`)
        const etag = detail.headers.get('etag') ?? (detail.data.updated_at ? `"${detail.data.updated_at}"` : '')
        if (!etag) throw new Error(t('errors.preconditionFailed'))
        await api.patch(`${definition.endpoint}${item.id}`, { is_active: !item.is_active }, { ifMatch: etag, idempotencyKey: true })
      } else {
        await api.patch(`${definition.endpoint}${item.id}`, { is_active: !item.is_active }, { idempotencyKey: true })
      }
      setNotice(item.is_active ? t('settings.masterDataDeactivated') : t('settings.masterDataActivated'))
      await fetchItems()
    } catch (err) {
      setError(err instanceof Error ? err.message : t('settings.masterDataSaveFailed'))
    } finally {
      setBusy(false)
    }
  }

  const filteredItems = visibleItems
  const columnCount = 3 + (kind === 'units' ? 0 : 1) + (canManage ? 1 : 0)

  if (!canView) {
    return (
      <main className="content settings-master-page">
        <div className="page-heading"><h1>{t(definition.titleKey)}</h1></div>
        <div className="notice error">{t('errors.lacksCapability', { capability: definition.capability })}</div>
      </main>
    )
  }

  return (
    <main className="content settings-master-page">
      <Link href="/settings" className="settings-master-back"><ArrowLeft size={15} /> {t('settings.title')}</Link>
      <header className="settings-master-hero">
        <div className="settings-master-title-icon"><Icon size={21} /></div>
        <div className="settings-master-heading-copy">
          <div className="eyebrow">{t('settings.masterData')}</div>
          <h1>{t(definition.titleKey)}</h1>
          <p>{t(definition.descriptionKey)}</p>
        </div>
        {canManage && (
          <button className="btn btn-primary" onClick={() => { setDialogError(''); setDialog({ mode: 'add' }) }} disabled={busy}>
            <Plus size={16} /> {t(titleKeys.add)}
          </button>
        )}
      </header>

      {notice && <div className="notice success master-data-notice"><Check size={15} /> {notice}<button onClick={() => setNotice('')} aria-label={t('common.close')}><X size={14} /></button></div>}
      {error && <div className="notice error master-data-notice">{error}<button onClick={() => setError('')} aria-label={t('common.close')}><X size={14} /></button></div>}

      <section className="panel settings-master-panel">
        <div className="settings-master-toolbar">
          <label className="settings-master-search">
            <Search size={16} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('settings.masterDataSearch')} />
          </label>
          <select aria-label={t('common.status')} value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}>
            <option value="all">{t('settings.masterDataAllStatuses')}</option>
            <option value="active">{t('common.active')}</option>
            <option value="inactive">{t('common.inactive')}</option>
          </select>
          <button className="btn btn-ghost" onClick={() => void fetchItems()} disabled={loading || busy}>
            <RefreshCw size={14} className={loading ? 'is-spinning' : ''} /> {t('common.refresh')}
          </button>
        </div>

        <div className="table-wrap settings-master-table-wrap">
          <table className="crud-table settings-master-table">
            <thead>
              <tr>
                <th>{t('common.name')}</th>
                <th>{t('common.code')}</th>
                {kind === 'payment-methods' && <th>{t('settings.paymentType')}</th>}
                {kind === 'financial-categories' && <th>{t('settings.entryType')}</th>}
                <th>{t('common.status')}</th>
                {canManage && <th className="settings-master-actions-heading">{t('common.actions')}</th>}
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={columnCount} className="settings-master-empty">{t('common.loading')}</td></tr>}
              {!loading && filteredItems.map((item) => (
                <tr key={item.id}>
                  <td><strong>{item.name}</strong></td>
                  <td><code>{item.code}</code></td>
                  {kind === 'payment-methods' && <td>{hasCashFlag(item) && item.is_cash ? t('settings.cashMethod') : t('settings.nonCashMethod')}</td>}
                  {kind === 'financial-categories' && <td>{hasEntryType(item) ? t(item.entry_type === 'income' ? 'manualEntries.income' : 'manualEntries.expense') : ''}</td>}
                  <td><span className={`badge ${item.is_active ? 'active' : 'inactive'}`}>{item.is_active ? t('common.active') : t('common.inactive')}</span></td>
                  {canManage && (
                    <td className="settings-master-actions">
                      <div className="settings-master-actions-inner">
                        <button className="icon-btn" title={t('common.edit')} aria-label={`${t('common.edit')} ${item.name}`} onClick={() => { setDialogError(''); setDialog({ mode: 'edit', item }) }} disabled={busy}>
                          <Pencil size={15} />
                        </button>
                        <button className={`btn btn-ghost settings-master-toggle ${item.is_active ? 'deactivate' : ''}`} onClick={() => void toggleActive(item)} disabled={busy}>
                          {item.is_active ? t(titleKeys.deactivate) : t('settings.masterDataActivate')}
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
              {!loading && filteredItems.length === 0 && (
                <tr><td colSpan={columnCount} className="settings-master-empty">{query ? t('settings.masterDataNoResults') : t('settings.masterDataEmpty')}</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="settings-master-footer">{t('settings.masterDataCount', { count: filteredItems.length })}</div>
      </section>

      {dialog && (
        <MasterDataDialog
          kind={kind}
          state={dialog}
          busy={busy}
          error={dialogError}
          onCancel={() => { if (!busy) { setDialog(null); setDialogError('') } }}
          onSave={saveItem}
          t={t}
          titleKey={titleKeys[dialog.mode]}
        />
      )}
    </main>
  )
}

function MasterDataDialog({
  kind,
  state,
  busy,
  error,
  onCancel,
  onSave,
  t,
  titleKey,
}: {
  kind: MasterDataKind
  state: Exclude<DialogState, null>
  busy: boolean
  error: string
  onCancel: () => void
  onSave: (values: { code: string; name: string; isCash: boolean; entryType: 'income' | 'expense' }) => void
  t: (key: string, params?: Record<string, string | number>) => string
  titleKey: string
}) {
  const [code, setCode] = useState(state.item?.code ?? '')
  const [name, setName] = useState(state.item?.name ?? '')
  const [isCash, setIsCash] = useState(state.item && hasCashFlag(state.item) ? state.item.is_cash : true)
  const [entryType, setEntryType] = useState<'income' | 'expense'>(state.item && hasEntryType(state.item) ? state.item.entry_type : 'expense')
  const editing = state.mode === 'edit'
  const requiresCode = !editing

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onSave({ code, name, isCash, entryType })
  }

  return (
    <div className="modal-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onCancel() }}>
      <form className="modal settings-master-dialog" onSubmit={submit}>
        <header>
          <div>
            <span className="eyebrow">{t('settings.masterData')}</span>
            <h2>{t(titleKey)}</h2>
          </div>
          <button type="button" className="icon-btn" onClick={onCancel} disabled={busy} aria-label={t('common.close')}><X size={17} /></button>
        </header>
        <div className="settings-master-dialog-fields">
          {error && <div className="settings-master-dialog-error" role="alert">{error}</div>}
          {requiresCode && (
            <label>{t('common.code')}<input autoFocus value={code} onChange={(event) => setCode(event.target.value)} required maxLength={kind === 'units' ? 20 : 50} pattern={kind === 'units' ? '[A-Za-z0-9_-]+' : undefined} /></label>
          )}
          {editing && <div className="master-data-fixed-code"><span>{t('common.code')}</span><code>{state.item?.code}</code><small>{t('settings.masterDataCodeImmutable')}</small></div>}
          <label>{t('common.name')}<input autoFocus={!requiresCode} value={name} onChange={(event) => setName(event.target.value)} required maxLength={kind === 'units' ? 50 : 100} /></label>
          {kind === 'payment-methods' && (
            <label className="master-data-checkbox"><input type="checkbox" checked={isCash} onChange={(event) => setIsCash(event.target.checked)} />{t('settings.cashMethod')}</label>
          )}
          {kind === 'financial-categories' && (
            <label>{t('settings.entryType')}
              <select value={entryType} onChange={(event) => setEntryType(event.target.value as 'income' | 'expense')} disabled={editing}>
                <option value="income">{t('manualEntries.income')}</option>
                <option value="expense">{t('manualEntries.expense')}</option>
              </select>
            </label>
          )}
        </div>
        <footer>
          <button type="button" className="btn btn-ghost" onClick={onCancel} disabled={busy}>{t('common.cancel')}</button>
          <button type="submit" className="btn btn-primary" disabled={busy || !name.trim() || (requiresCode && !code.trim())}>
            {busy ? t('common.saving') : t('common.save')}
          </button>
        </footer>
      </form>
    </div>
  )
}
