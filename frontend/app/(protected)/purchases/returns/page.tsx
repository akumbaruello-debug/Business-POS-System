'use client'

import { Fragment, useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Search, X } from 'lucide-react'
import { api, isApiError } from '@/lib/api-client'
import type { Contact, ContactListResponse } from '@/lib/contact-types'
import type { PurchaseReturn } from '@/lib/purchase-types'
import type { SupplierRepayment, SupplierRepaymentsResponse } from '@/lib/supplier-types'
import { formatIDR } from '@/lib/format'
import { fmtDateTime, num, sanitizeEtag } from '@/lib/sale-ui'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'

// -----------------------------------------------------------------------------
// Backend contract (backend/app/api/v1/purchase_returns.py):
//
//   GET  /purchase-returns?page=&per_page=&sort=
///      &purchase_id=&supplier_id=&lifecycle_status=   (purchase.view)
//     → { data: PurchaseReturn[], pagination } — rows carry Phase-E courier
//       state (finalized_*, supplier_arrival_*, overdue_override_*, is_overdue)
//   GET  /purchase-returns/{id}                        (purchase.view)
//   POST /purchase-returns/{id}/cancel {reason}        (purchase.return)
//     Idempotency-Key + If-Match REQUIRED.
//   POST /purchase-returns/{id}/finalize {reason?}     (purchase.return.finalize)
//   POST /purchase-returns/{id}/arrival {note?}        (purchase.return.arrival)
//   POST /purchase-returns/{id}/override-expired-window {reason}
//                                                     (purchase.return.override)
//     Phase-E actions: Idempotency-Key required, If-Match optional (omitted;
//     the backend compares a version etag the list payload does not carry).
//
// Creation lives on the purchase detail page (POST /purchases/{id}/returns).
// Courier actions (finalize / arrival / override) live in this workspace.
// Frontend gates are UX-only; the backend lifecycle is authoritative.
// -----------------------------------------------------------------------------

interface ReturnsListResponse {
  data: PurchaseReturn[]
  pagination: { page: number; per_page: number; total: number; total_pages: number }
}

export default function PurchaseReturnsPage() {
  const user = useSession()
  const { t } = useLanguage()
  const canView = user.capabilities.includes('purchase.view')
  const canCancel = user.capabilities.includes('purchase.return')
  const canFinalize = user.capabilities.includes('purchase.return.finalize')
  const canArrive = user.capabilities.includes('purchase.return.arrival')
  const canOverride = user.capabilities.includes('purchase.return.override')

  const [rows, setRows] = useState<PurchaseReturn[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [purchaseId, setPurchaseId] = useState('')
  const [supplierId, setSupplierId] = useState('')
  const [lifecycle, setLifecycle] = useState('')
  const [suppliers, setSuppliers] = useState<Contact[]>([])
  const [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [cancelId, setCancelId] = useState<number | null>(null)
  const [cancelReason, setCancelReason] = useState('')
  const [busy, setBusy] = useState(false)

  // Phase-E courier action dialog: { kind, returnId } or null.
  const [phaseAction, setPhaseAction] = useState<{ kind: 'finalize' | 'arrival' | 'override'; id: number } | null>(null)
  const [phaseReason, setPhaseReason] = useState('')

  // Supplier-repayment rows linked to the expanded return (Part B: SREC
  // awareness). Fetched lazily on expand; keyed by return id.
  const [srecByReturn, setSrecByReturn] = useState<Record<number, SupplierRepayment[]>>({})
  const [srecLoading, setSrecLoading] = useState<Record<number, boolean>>({})

  const fetchSuppliers = useCallback(async () => {
    try {
      const res = await api.get<ContactListResponse>('/contacts', {
        params: { 'filter[type]': 'supplier', per_page: 200, sort: 'name' },
      })
      setSuppliers(res.data ?? [])
    } catch {
      setSuppliers([])
    }
  }, [])

  const fetchRows = useCallback(async () => {
    if (!canView) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<ReturnsListResponse>('/purchase-returns', {
        params: {
          page,
          per_page: 25,
          sort: '-id',
          ...(purchaseId ? { purchase_id: Number(purchaseId) } : {}),
          ...(supplierId ? { supplier_id: Number(supplierId) } : {}),
          ...(lifecycle ? { lifecycle_status: lifecycle } : {}),
        },
      })
      setRows(res.data ?? [])
      setTotal(res.pagination?.total ?? 0)
    } catch (e) {
      setError(isApiError(e) ? e.message : t('purchaseReturns.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [canView, page, purchaseId, supplierId, lifecycle, t])

  useEffect(() => {
    if (canView) {
      void fetchSuppliers()
    }
  }, [canView, fetchSuppliers])

  useEffect(() => {
    if (canView) void fetchRows()
  }, [canView, fetchRows])

  const applyFilters = () => {
    setPage(1)
    void fetchRows()
  }

  const clearFilters = () => {
    setPurchaseId('')
    setSupplierId('')
    setLifecycle('')
    setPage(1)
  }

  // Visibility rules mirror the backend lifecycle gates
  // (services/purchases.py): posted-only; finalize/arrival once each;
  // override only when server-derived is_overdue and not yet overridden.
  // These are UX gates — the backend remains authoritative.
  const canFinalizeRow = (r: PurchaseReturn) =>
    canFinalize && r.lifecycle_status === 'posted' && !r.finalized_at
  const canArriveRow = (r: PurchaseReturn) =>
    canArrive && r.lifecycle_status === 'posted' && !r.supplier_arrival_at
  const canOverrideRow = (r: PurchaseReturn) =>
    canOverride && r.lifecycle_status === 'posted' && !!r.is_overdue && !r.overdue_override_at
  const needsOverrideNote = (r: PurchaseReturn) =>
    r.lifecycle_status === 'posted' && !!r.is_overdue && !r.overdue_override_at && !canOverride

  const fetchSrecForReturn = useCallback(
    async (ret: PurchaseReturn) => {
      if (srecByReturn[ret.id] || srecLoading[ret.id]) return
      setSrecLoading((prev) => ({ ...prev, [ret.id]: true }))
      try {
        const res = await api.get<SupplierRepaymentsResponse>('/supplier-repayments', {
          params: { purchase_id: ret.purchase_id, per_page: 200 },
        })
        const linked = (res.data ?? []).filter((s) => s.purchase_return_id === ret.id)
        setSrecByReturn((prev) => ({ ...prev, [ret.id]: linked }))
      } catch {
        setSrecByReturn((prev) => ({ ...prev, [ret.id]: [] }))
      } finally {
        setSrecLoading((prev) => ({ ...prev, [ret.id]: false }))
      }
    },
    [srecByReturn, srecLoading],
  )

  const toggleExpanded = (ret: PurchaseReturn) => {
    const next = expanded === ret.id ? null : ret.id
    setExpanded(next)
    if (next != null) void fetchSrecForReturn(ret)
  }

  const openPhaseAction = (kind: 'finalize' | 'arrival' | 'override', id: number) => {
    setPhaseAction({ kind, id })
    setPhaseReason('')
  }

  const handlePhaseAction = async () => {
    if (!phaseAction || busy) return
    const { kind, id } = phaseAction
    if (kind === 'override' && !phaseReason.trim()) return
    setBusy(true)
    setNotice(null)
    try {
      const path =
        kind === 'finalize'
          ? `/purchase-returns/${id}/finalize`
          : kind === 'arrival'
            ? `/purchase-returns/${id}/arrival`
            : `/purchase-returns/${id}/override-expired-window`
      // If-Match is optional on Phase-E endpoints (omitted: the list payload
      // carries a version etag the backend would compare; tests omit it too).
      const body =
        kind === 'finalize'
          ? { ...(phaseReason.trim() ? { reason: phaseReason.trim() } : {}) }
          : kind === 'arrival'
            ? { ...(phaseReason.trim() ? { note: phaseReason.trim() } : {}) }
            : { reason: phaseReason.trim() }
      await api.headers.post(path, body, { idempotencyKey: true })
      setPhaseAction(null)
      setPhaseReason('')
      setNotice(
        kind === 'finalize'
          ? t('purchaseReturns.finalizedToast')
          : kind === 'arrival'
            ? t('purchaseReturns.arrivedToast')
            : t('purchaseReturns.overriddenToast'),
      )
      await fetchRows()
    } catch (e) {
      if (isApiError(e) && (e.code === 'version_mismatch' || e.status === 412)) {
        setNotice(t('purchaseReturns.staleRetry'))
        await fetchRows()
      } else {
        setNotice(isApiError(e) ? e.message : t('purchaseReturns.actionFailed'))
      }
    } finally {
      setBusy(false)
    }
  }

  const handleCancel = async () => {
    if (cancelId == null || !cancelReason.trim() || busy) return
    setBusy(true)
    setNotice(null)
    try {
      const detail = await api.headers.get<PurchaseReturn>(`/purchase-returns/${cancelId}`)
      const tag = sanitizeEtag(detail.headers.get('etag'))
      await api.headers.post(
        `/purchase-returns/${cancelId}/cancel`,
        { reason: cancelReason.trim() },
        { idempotencyKey: true, ifMatch: tag || undefined },
      )
      setCancelId(null)
      setCancelReason('')
      setNotice(t('purchaseReturns.cancelled'))
      await fetchRows()
    } catch (e) {
      setNotice(isApiError(e) ? e.message : t('purchaseReturns.cancelFailed'))
    } finally {
      setBusy(false)
    }
  }

  if (!canView) {
    return (
      <div className="page">
        <div className="notice error">{t('common.accessRestricted')}</div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>{t('purchaseReturns.title')}</h1>
          <p className="muted">{t('purchaseReturns.subtitle')}</p>
        </div>
        <span className="pill">{t('purchaseReturns.total')}: {total}</span>
      </div>

      {notice && <div className="notice info">{notice}</div>}

      <section className="panel">
        <div className="pos-body" style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <label className="field">
            <span>{t('purchaseReturns.purchase')}</span>
            <input
              value={purchaseId}
              inputMode="numeric"
              placeholder={t('purchaseReturns.purchasePlaceholder')}
              onChange={(e) => setPurchaseId(e.target.value.replace(/[^0-9]/g, ''))}
            />
          </label>
          <label className="field">
            <span>{t('purchaseReturns.supplier')}</span>
            <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
              <option value="">{t('common.all')}</option>
              {suppliers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t('common.status')}</span>
            <select value={lifecycle} onChange={(e) => setLifecycle(e.target.value)}>
              <option value="">{t('common.all')}</option>
              <option value="posted">{t('status.posted')}</option>
              <option value="cancelled">{t('status.cancelled')}</option>
            </select>
          </label>
          <Button onClick={applyFilters}>
            <Search size={14} /> {t('common.filter')}
          </Button>
          {(purchaseId || supplierId || lifecycle) && (
            <button type="button" className="link" onClick={clearFilters}>
              <X size={13} /> {t('common.clearFilters')}
            </button>
          )}
        </div>
      </section>

      {loading ? (
        <div className="panel"><p className="muted">{t('common.loading')}</p></div>
      ) : error ? (
        <div className="notice error">
          {error}{' '}
          <button type="button" className="link" onClick={() => void fetchRows()}>
            {t('common.retry')}
          </button>
        </div>
      ) : (
        <section className="panel">
          <table className="crud-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>{t('purchaseReturns.purchase')}</th>
                <th>{t('purchaseReturns.date')}</th>
                <th>{t('purchaseReturns.value')}</th>
                <th>{t('common.status')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td><strong>#{r.id}</strong></td>
                    <td>
                      <Link className="link" href={`/purchases/${r.purchase_id}`}>
                        #{r.purchase_id}
                      </Link>
                    </td>
                    <td className="muted">{fmtDateTime(r.return_date)}</td>
                    <td>{formatIDR(num(r.total_value_returned))}</td>
                    <td>
                      {r.lifecycle_status === 'cancelled' ? (
                        <span className="tag tag-grey">{t('status.cancelled')}</span>
                      ) : (
                        <span className="tag tag-green">{t('status.posted')}</span>
                      )}
                      {r.lifecycle_status === 'posted' && r.finalized_at && (
                        <span className="tag tag-blue">{t('purchaseReturns.finalized')}</span>
                      )}
                      {r.lifecycle_status === 'posted' && r.supplier_arrival_at && (
                        <span className="tag tag-blue">{t('purchaseReturns.arrived')}</span>
                      )}
                      {r.lifecycle_status === 'posted' && !!r.is_overdue && !r.overdue_override_at && (
                        <span className="tag tag-red">{t('purchaseReturns.overdue')}</span>
                      )}
                      {r.lifecycle_status === 'posted' && r.overdue_override_at && (
                        <span className="tag tag-grey">{t('purchaseReturns.overridden')}</span>
                      )}
                    </td>
                    <td className="row-actions">
                      <button
                        type="button"
                        className="link"
                        onClick={() => toggleExpanded(r)}
                      >
                        {expanded === r.id ? t('common.close') : t('common.view')}
                      </button>
                      {canFinalizeRow(r) && (
                        <button
                          type="button"
                          className="link"
                          onClick={() => openPhaseAction('finalize', r.id)}
                        >
                          {t('purchaseReturns.finalize')}
                        </button>
                      )}
                      {canArriveRow(r) && (
                        <button
                          type="button"
                          className="link"
                          onClick={() => openPhaseAction('arrival', r.id)}
                        >
                          {t('purchaseReturns.recordArrival')}
                        </button>
                      )}
                      {canOverrideRow(r) && (
                        <button
                          type="button"
                          className="link"
                          onClick={() => openPhaseAction('override', r.id)}
                        >
                          {t('purchaseReturns.overrideWindow')}
                        </button>
                      )}
                      {canCancel && r.lifecycle_status === 'posted' && (
                        <button
                          type="button"
                          className="link danger"
                          onClick={() => {
                            setCancelId(r.id)
                            setCancelReason('')
                          }}
                        >
                          {t('common.cancel')}
                        </button>
                      )}
                    </td>
                  </tr>
                  {expanded === r.id && (
                    <tr key={`${r.id}-detail`}>
                      <td colSpan={6}>
                        <p className="muted small">
                          {t('purchaseReturns.reason')}: {r.reason ?? '—'}
                        </p>
                        <ul className="tender-history">
                          {(r.lines ?? []).map((l) => (
                            <li key={l.id}>
                              #{l.product_id} × {num(l.quantity)} · {formatIDR(num(l.line_value))}
                            </li>
                          ))}
                        </ul>
                        <p className="muted small" style={{ marginTop: 8, fontWeight: 700 }}>
                          {t('purchaseReturns.courier')}
                        </p>
                        <ul className="tender-history">
                          <li>
                            {t('purchaseReturns.finalized')}: {r.finalized_at ? fmtDateTime(r.finalized_at) : '—'}
                            {r.finalized_reason ? ` · ${r.finalized_reason}` : ''}
                          </li>
                          <li>
                            {t('purchaseReturns.arrived')}: {r.supplier_arrival_at ? fmtDateTime(r.supplier_arrival_at) : '—'}
                          </li>
                          <li>
                            {t('purchaseReturns.overridden')}: {r.overdue_override_at ? fmtDateTime(r.overdue_override_at) : '—'}
                            {r.overdue_override_reason ? ` · ${r.overdue_override_reason}` : ''}
                          </li>
                        </ul>
                        {needsOverrideNote(r) && (
                          <p className="muted small">{t('purchaseReturns.overrideRequiresOwner')}</p>
                        )}
                        <p className="muted small" style={{ marginTop: 8, fontWeight: 700 }}>
                          {t('purchaseReturns.srec')}
                        </p>
                        {srecLoading[r.id] ? (
                          <p className="muted small">{t('common.loading')}</p>
                        ) : (srecByReturn[r.id] ?? []).length === 0 ? (
                          <p className="muted small">{t('purchaseReturns.srecNone')}</p>
                        ) : (
                          <ul className="tender-history">
                            {(srecByReturn[r.id] ?? []).map((s) => {
                              const outstanding = num(s.amount) - num(s.received_amount)
                              return (
                                <li key={s.id}>
                                  {t('purchaseReturns.srecExpected')}: {formatIDR(num(s.amount))}
                                  {' · '}{t('purchaseReturns.srecReceived')}: {formatIDR(num(s.received_amount))}
                                  {' · '}{t('purchaseReturns.srecOutstanding')}: {formatIDR(outstanding)}
                                  {s.reason ? ` · ${s.reason}` : ''}
                                </li>
                              )
                            })}
                          </ul>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    {t('purchaseReturns.empty')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <div className="pos-body" style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            <Button
              variant="outline"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ←
            </Button>
            <span className="muted small">
              {t('purchaseReturns.page')} {page}
            </span>
            <Button variant="outline" onClick={() => setPage((p) => p + 1)}>
              →
            </Button>
          </div>
        </section>
      )}

      {cancelId != null && (
        <div className="search-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setCancelId(null) }}>
          <div className="search-dialog">
            <div className="popover-head">
              <strong>
                {t('purchaseReturns.cancelTitle')} #{cancelId}
              </strong>
            </div>
            <div className="pos-body">
              <label className="field">
                <span>{t('purchaseReturns.reason')} *</span>
                <input
                  value={cancelReason}
                  onChange={(e) => setCancelReason(e.target.value)}
                  maxLength={500}
                />
              </label>
              <div className="receipt-actions">
                <Button variant="outline" onClick={() => setCancelId(null)} disabled={busy}>
                  {t('common.cancel')}
                </Button>
                <Button onClick={() => void handleCancel()} disabled={busy || !cancelReason.trim()}>
                  {busy ? t('common.saving') : t('common.confirm')}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {phaseAction && (
        <div className="search-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) setPhaseAction(null) }}>
          <div className="search-dialog">
            <div className="popover-head">
              <strong>
                {phaseAction.kind === 'finalize'
                  ? t('purchaseReturns.finalizeTitle')
                  : phaseAction.kind === 'arrival'
                    ? t('purchaseReturns.arrivalTitle')
                    : t('purchaseReturns.overrideTitle')}{' '}
                #{phaseAction.id}
              </strong>
            </div>
            <div className="pos-body">
              <p className="muted small">
                {phaseAction.kind === 'finalize'
                  ? t('purchaseReturns.finalizeHelp')
                  : phaseAction.kind === 'arrival'
                    ? t('purchaseReturns.arrivalHelp')
                    : t('purchaseReturns.overrideHelp')}
              </p>
              <label className="field">
                <span>
                  {phaseAction.kind === 'arrival'
                    ? t('purchaseReturns.noteOptional')
                    : t('purchaseReturns.reasonOptional')}
                </span>
                <input
                  value={phaseReason}
                  onChange={(e) => setPhaseReason(e.target.value)}
                  maxLength={1000}
                />
              </label>
              <div className="receipt-actions">
                <Button variant="outline" onClick={() => setPhaseAction(null)} disabled={busy}>
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={() => void handlePhaseAction()}
                  disabled={busy || (phaseAction.kind === 'override' && !phaseReason.trim())}
                >
                  {busy ? t('common.saving') : t('common.confirm')}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
