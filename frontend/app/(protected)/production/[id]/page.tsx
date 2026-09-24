'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams, useRouter } from 'next/navigation'
import { AlertTriangle, ArrowLeft, Package, Pencil, Plus, Trash2, X } from 'lucide-react'
import { api } from '@/lib/api-client'
import { formatIDR, formatDate, formatDateTime } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import type {
  CostType,
  ProductionCostLine,
  ProductionCostLineRequest,
  ProductionInput,
  ProductionInputRequest,
  ProductionRun,
} from '@/lib/production-types'
import type { Product } from '@/lib/product-types'

// ---------------------------------------------------------------------------
// Detail: GET /production-runs/{id}?include=inputs,cost_lines,output
// Draft mutations use If-Match (required by backend). Post/cancel require
// both If-Match and Idempotency-Key. Only draft runs are editable.
// ---------------------------------------------------------------------------

function statusTone(status: string): string {
  const map: Record<string, string> = {
    draft: '#f1f5f9,#475569',
    posted: '#eff6ff,#2563eb',
    completed: '#ecfdf5,#059669',
    cancelled: '#fef2f2,#dc2626',
  }
  return map[status] ?? map.draft
}

function StatusBadge({ status, t }: { status: string; t: (key: string, params?: Record<string, string | number>) => string }) {
  const [bg, color] = statusTone(status).split(',')
  const labelKey: Record<string, string> = {
    draft: 'status.draft',
    posted: 'status.posted',
    completed: 'status.completed',
    cancelled: 'status.cancelled',
  }
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 8px',
        borderRadius: 999,
        fontSize: 11,
        fontWeight: 700,
        textTransform: 'capitalize',
        whiteSpace: 'nowrap',
        background: bg,
        color: color,
        border: `1px solid ${bg}`,
      }}
    >
      <span style={{ width: 6, height: 6, borderRadius: 999, background: 'currentColor' }} />
      {t(labelKey[status] ?? status, { defaultValue: status })}
    </span>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: '#f8fafc' }}>
        <h3 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>{title}</h3>
      </div>
      {children}
    </div>
  )
}

export default function ProductionDetailPage() {
  const params = useParams<{ id: string }>()
  const runId = Number(params?.id)
  const router = useRouter()
  const user = useSession()
  const { t } = useLanguage()

  const canView = user.capabilities.includes('production.view')
  const canEditDraft = user.capabilities.includes('production.edit_own_draft')
  const canPost = user.capabilities.includes('production.post')
  const canCancel = user.capabilities.includes('production.cancel')

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [run, setRun] = useState<ProductionRun | null>(null)
  const [etag, setEtag] = useState<string | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [costTypes, setCostTypes] = useState<CostType[]>([])
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [postConfirmOpen, setPostConfirmOpen] = useState(false)
  const [cancelConfirmOpen, setCancelConfirmOpen] = useState(false)
  const [headerOpen, setHeaderOpen] = useState(false)
  const [inputDialog, setInputDialog] = useState<{ mode: 'add' | 'edit'; input?: ProductionInput } | null>(null)
  const [costLineDialog, setCostLineDialog] = useState<{ mode: 'add' | 'edit'; line?: ProductionCostLine } | null>(null)

  const toast = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(''), 2400)
  }

  const productMap = useMemo(() => {
    const m = new Map<number, Product>()
    products.forEach((p) => m.set(p.id, p))
    return m
  }, [products])

  const costTypeMap = useMemo(() => {
    const m = new Map<number, CostType>()
    costTypes.forEach((c) => m.set(c.id, c))
    return m
  }, [costTypes])

  const productName = (id: number) => productMap.get(id)?.name ?? `${t('production.product')} #${id}`
  const costTypeName = (id: number) => costTypeMap.get(id)?.name ?? `${t('production.costType')} #${id}`

  const fetchRun = useCallback(async () => {
    if (!canView || !Number.isFinite(runId)) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await api.headers.get<ProductionRun>(`/production-runs/${runId}?include=inputs,cost_lines,output`)
      const data = res.data
      const derivedEtag = res.headers.get('etag') ?? data.etag ?? `"${data.updated_at}"`
      setEtag(derivedEtag)
      setRun(data)
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      const msg = err instanceof Error ? err.message : t('production.failedToLoad')
      if (code === 'not_found' || /not found|404/i.test(msg)) {
        setNotFound(true)
        setRun(null)
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }, [canView, runId])

  useEffect(() => {
    fetchRun()
  }, [fetchRun])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [pres, cres] = await Promise.all([
          api.get<{ data: Product[] }>('/products', { params: { per_page: '500' } }),
          api.get<{ data: CostType[] }>('/cost-types', { params: { per_page: '200' } }),
        ])
        if (cancelled) return
        setProducts(pres.data)
        setCostTypes(cres.data)
      } catch {
        if (!cancelled) {
          setProducts([])
          setCostTypes([])
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const refetch = useCallback(async () => {
    await fetchRun()
  }, [fetchRun])

  const runMutation = async (label: string, fn: () => Promise<void>, opts: { refresh?: boolean } = { refresh: true }) => {
    setBusy(true)
    setConflict(null)
    try {
      await fn()
      toast(label)
      if (opts.refresh !== false) await refetch()
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      const msg = err instanceof Error ? err.message : 'Request failed'
      if (code === 'version_mismatch' || /stale|412|precondition/i.test(msg)) {
        setConflict(t('production.staleRetry'))
        await refetch()
      } else if (code === 'lifecycle_state_invalid' || code?.startsWith('lifecycle')) {
        toast(t('production.lifecycleChanged'))
        await refetch()
      } else if (code === 'idempotency_violation') {
        toast(t('production.idempotencyViolation'))
        await refetch()
      } else if (code === 'production_inputs_exceed_stock') {
        toast(t('production.insufficientStock'))
      } else if (code === 'production_finished_cost_invalid') {
        toast(t('production.finishedCostInvalid'))
      } else if (code === 'cost_type_deactivated') {
        toast(t('production.costTypeDeactivated'))
      } else if (code === 'insufficient_cash') {
        toast(t('production.insufficientCash'))
      } else {
        toast(msg)
      }
    } finally {
      setBusy(false)
    }
  }

  const patchHeader = (runDate: string, outputProductId: number, outputQuantity: number, notes: string) =>
    runMutation(t('production.updatedToast'), async () => {
      const body: Record<string, unknown> = {}
      if (runDate) body.run_date = new Date(`${runDate}T00:00:00`).toISOString()
      if (outputProductId) body.output_product_id = outputProductId
      if (Number.isFinite(outputQuantity) && outputQuantity > 0) body.output_quantity = outputQuantity
      body.notes = notes.trim() ? notes.trim() : null
      await api.patch(`/production-runs/${runId}`, body, {
        headers: { 'If-Match': etag ?? '' },
      })
      setHeaderOpen(false)
    })

  const addInput = (input: ProductionInputRequest) =>
    runMutation(t('production.inputAdded'), async () => {
      await api.post(`/production-runs/${runId}/inputs`, input, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
      setInputDialog(null)
    })

  const updateInput = (inputId: number, input: ProductionInputRequest) =>
    runMutation(t('production.inputUpdated'), async () => {
      await api.patch(`/production-runs/${runId}/inputs/${inputId}`, input, {
        headers: { 'If-Match': etag ?? '' },
      })
      setInputDialog(null)
    })

  const deleteInput = (inputId: number) =>
    runMutation(t('production.inputRemoved'), async () => {
      await api.delete(`/production-runs/${runId}/inputs/${inputId}`, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
    })

  const addCostLine = (line: ProductionCostLineRequest) =>
    runMutation(t('production.costLineAdded'), async () => {
      await api.post(`/production-runs/${runId}/cost-lines`, line, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
      setCostLineDialog(null)
    })

  const updateCostLine = (lineId: number, line: ProductionCostLineRequest) =>
    runMutation(t('production.costLineUpdated'), async () => {
      await api.patch(`/production-runs/${runId}/cost-lines/${lineId}`, line, {
        headers: { 'If-Match': etag ?? '' },
      })
      setCostLineDialog(null)
    })

  const deleteCostLine = (lineId: number) =>
    runMutation(t('production.costLineRemoved'), async () => {
      await api.delete(`/production-runs/${runId}/cost-lines/${lineId}`, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
    })

  const postRun = () =>
    runMutation(t('production.postedToast'), async () => {
      await api.post(`/production-runs/${runId}/post`, {}, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
      setPostConfirmOpen(false)
    })

  const cancelRun = (reason: string) =>
    runMutation(t('production.cancelledToast'), async () => {
      await api.post(`/production-runs/${runId}/cancel`, { reason }, {
        headers: { 'If-Match': etag ?? '', 'Idempotency-Key': crypto.randomUUID() },
      })
      setCancelConfirmOpen(false)
    })

  if (loading) {
    return (
      <div className="content">
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="skeleton" style={{ height: 80, borderRadius: 8 }} />
          ))}
        </div>
      </div>
    )
  }

  if (!canView) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <h1>{t('production.title')}</h1>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>{t('errors.forbidden')}</strong>
          <p>{t('errors.lacksCapability', { capability: 'production.view' })}</p>
        </div>
      </div>
    )
  }

  if (notFound || !run) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <Link href="/production" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--primary)', textDecoration: 'none', fontSize: 13 }}>
              <ArrowLeft size={14} /> {t('production.backToList')}
            </Link>
            <h1 style={{ marginTop: 8 }}>{t('production.title')}</h1>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>{t('production.notFound')}</strong>
          <p>{t('production.runNotFound', { id: runId })}</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <Link href="/production" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--primary)', textDecoration: 'none', fontSize: 13 }}>
              <ArrowLeft size={14} /> {t('production.backToList')}
            </Link>
            <h1 style={{ marginTop: 8 }}>{t('production.title')}</h1>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertTriangle size={32} />
          </div>
          <strong>{t('production.failedToLoad')}</strong>
          <p>{error}</p>
          <Button variant="outline" onClick={fetchRun}>
            {t('common.retry')}
          </Button>
        </div>
      </div>
    )
  }

  const isDraft = run.lifecycle_status === 'draft'
  const isPosted = run.lifecycle_status === 'posted'
  const editable = canEditDraft && isDraft
  const postable = canPost && isDraft
  const cancellable = canCancel && isPosted

  const totalRaw = run.total_raw_cost
  const totalOverhead = run.total_overhead_cost
  const totalCost = totalRaw + totalOverhead
  const previewUnitCost = run.output_quantity > 0 ? totalCost / run.output_quantity : 0

  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <Link href="/production" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--primary)', textDecoration: 'none', fontSize: 13 }}>
            <ArrowLeft size={14} /> {t('production.backToList')}
          </Link>
          <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 style={{ margin: 0 }}>
              {t('production.run')} #{run.id}
            </h1>
            <StatusBadge status={run.lifecycle_status} t={t} />
          </div>
          <p style={{ margin: '8px 0 0', fontSize: 12, color: '#718198' }}>
            {formatDate(run.run_date)} · {t('production.outputProduct')}: {productName(run.output_product_id)} · {t('common.version')} {run.version}
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {postable && (
            <Button onClick={() => setPostConfirmOpen(true)} disabled={busy}>
              <Package size={14} className="mr-1" /> {t('production.postRun')}
            </Button>
          )}
          {cancellable && (
            <Button variant="outline" onClick={() => setCancelConfirmOpen(true)} disabled={busy}>
              {t('production.cancelRun')}
            </Button>
          )}
          {editable && (
            <Button variant="outline" onClick={() => setHeaderOpen(true)} disabled={busy}>
              <Pencil size={14} className="mr-1" /> {t('common.edit')}
            </Button>
          )}
        </div>
      </div>

      {conflict && (
        <div
          style={{
            marginBottom: 16,
            padding: '10px 14px',
            borderRadius: 8,
            background: '#fffbeb',
            border: '1px solid #fde68a',
            color: '#92400e',
            fontSize: 13,
          }}
        >
          <AlertTriangle size={15} style={{ marginRight: 8, verticalAlign: 'text-bottom' }} />
          {conflict}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16, marginBottom: 16 }}>
        <Card title={t('production.runDetails')}>
          <div style={{ padding: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontSize: 13 }}>
            <div>
              <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('production.runDate')}</div>
              <div style={{ marginTop: 4, fontWeight: 600 }}>{formatDate(run.run_date)}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('production.outputProduct')}</div>
              <div style={{ marginTop: 4, fontWeight: 600 }}>{productName(run.output_product_id)}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('production.outputQuantity')}</div>
              <div style={{ marginTop: 4 }}>{run.output_quantity}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('production.finishedUnitCost')}</div>
              <div style={{ marginTop: 4, fontWeight: 600 }}>{formatIDR(run.finished_unit_cost)}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('common.created')}</div>
              <div style={{ marginTop: 4, fontSize: 12 }}>{formatDateTime(run.created_at)} · #{run.created_by}</div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('production.posted')}</div>
              <div style={{ marginTop: 4, fontSize: 12 }}>
                {run.posted_at ? `${formatDateTime(run.posted_at)} · #${run.posted_by ?? '—'}` : '—'}
              </div>
            </div>
            {run.cancellation_date && (
              <>
                <div>
                  <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('production.cancelled')}</div>
                  <div style={{ marginTop: 4, fontSize: 12 }}>
                    {`${formatDateTime(run.cancellation_date)} · #${run.cancelled_by ?? '—'}`}
                  </div>
                </div>
                <div style={{ gridColumn: '1 / -1' }}>
                  <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('production.cancellationReason')}</div>
                  <div style={{ marginTop: 4, fontSize: 12, color: '#334155', whiteSpace: 'pre-wrap' }}>{run.cancellation_reason}</div>
                </div>
              </>
            )}
            {run.notes && (
              <div style={{ gridColumn: '1 / -1' }}>
                <div style={{ fontSize: 11, color: '#8a98ab', fontWeight: 700, letterSpacing: 0.6, textTransform: 'uppercase' }}>{t('common.notes')}</div>
                <div style={{ marginTop: 4, fontSize: 12, color: '#334155', whiteSpace: 'pre-wrap' }}>{run.notes}</div>
              </div>
            )}
          </div>
        </Card>

        <Card title={t('production.costComposition')}>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10, fontSize: 13 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#718198' }}>{t('production.totalRawCost')}</span>
              <span>{formatIDR(totalRaw)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#718198' }}>{t('production.totalOverheadCost')}</span>
              <span>{formatIDR(totalOverhead)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 10, fontWeight: 700 }}>
              <span>{t('production.totalCost')}</span>
              <span>{formatIDR(totalCost)}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: '#718198' }}>{t('production.previewUnitCost')}</span>
              <span style={{ fontWeight: 600 }}>{formatIDR(previewUnitCost)}</span>
            </div>
            <p style={{ margin: '4px 0 0', fontSize: 11, color: '#94a3b8' }}>{t('production.previewUnitCostNote')}</p>
          </div>
        </Card>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <Card title={t('production.rawInputs')}>
          {editable && (
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
              <Button size="sm" variant="outline" onClick={() => setInputDialog({ mode: 'add' })} disabled={busy}>
                <Plus size={13} className="mr-1" /> {t('production.addInput')}
              </Button>
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#f8fafc', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                <th style={{ padding: '9px 16px', textAlign: 'left', fontWeight: 700 }}>{t('production.product')}</th>
                <th style={{ padding: '9px 16px', textAlign: 'right', fontWeight: 700 }}>{t('production.quantity')}</th>
                <th style={{ padding: '9px 16px', textAlign: 'right', fontWeight: 700 }}>{t('production.unitCost')}</th>
                <th style={{ padding: '9px 16px', textAlign: 'right', fontWeight: 700 }}>{t('production.lineCost')}</th>
                {editable && <th style={{ width: 80 }} />}
              </tr>
            </thead>
            <tbody>
              {run.inputs.map((input) => (
                <tr key={input.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                  <td style={{ padding: '10px 16px' }}>{productName(input.product_id)}</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right' }}>{input.quantity}</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right' }}>{formatIDR(input.unit_cost_snapshot)}</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', fontWeight: 600 }}>{formatIDR(input.line_cost)}</td>
                  {editable && (
                    <td style={{ padding: '10px 16px', textAlign: 'right' }}>
                      <button
                        onClick={() => setInputDialog({ mode: 'edit', input })}
                        style={{ border: 0, background: 'none', color: '#2563eb', marginRight: 8 }}
                        aria-label={t('common.edit')}
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        onClick={() => deleteInput(input.id)}
                        style={{ border: 0, background: 'none', color: '#dc2626' }}
                        aria-label={t('common.delete')}
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {run.inputs.length === 0 && (
                <tr>
                  <td colSpan={editable ? 5 : 4} style={{ padding: 20, textAlign: 'center', color: '#94a3b8', fontSize: 13 }}>
                    {t('production.noInputs')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>

        <Card title={t('production.costLines')}>
          {editable && (
            <div style={{ padding: '10px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'flex-end' }}>
              <Button size="sm" variant="outline" onClick={() => setCostLineDialog({ mode: 'add' })} disabled={busy}>
                <Plus size={13} className="mr-1" /> {t('production.addCostLine')}
              </Button>
            </div>
          )}
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#f8fafc', fontSize: 11, letterSpacing: 0.5, textTransform: 'uppercase', color: '#64748b' }}>
                <th style={{ padding: '9px 16px', textAlign: 'left', fontWeight: 700 }}>{t('production.costType')}</th>
                <th style={{ padding: '9px 16px', textAlign: 'right', fontWeight: 700 }}>{t('production.amount')}</th>
                <th style={{ padding: '9px 16px', textAlign: 'left', fontWeight: 700 }}>{t('production.paidInCash')}</th>
                {editable && <th style={{ width: 80 }} />}
              </tr>
            </thead>
            <tbody>
              {run.cost_lines.map((line) => (
                <tr key={line.id} style={{ borderTop: '1px solid #f0f4f9' }}>
                  <td style={{ padding: '10px 16px' }}>
                    {costTypeName(line.cost_type_id)}
                    {line.description && <div style={{ fontSize: 11, color: '#718198' }}>{line.description}</div>}
                  </td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', fontWeight: 600 }}>{formatIDR(line.amount)}</td>
                  <td style={{ padding: '10px 16px' }}>{line.paid_in_cash ? t('common.yes') : t('common.no')}</td>
                  {editable && (
                    <td style={{ padding: '10px 16px', textAlign: 'right' }}>
                      <button
                        onClick={() => setCostLineDialog({ mode: 'edit', line })}
                        style={{ border: 0, background: 'none', color: '#2563eb', marginRight: 8 }}
                        aria-label={t('common.edit')}
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        onClick={() => deleteCostLine(line.id)}
                        style={{ border: 0, background: 'none', color: '#dc2626' }}
                        aria-label={t('common.delete')}
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {run.cost_lines.length === 0 && (
                <tr>
                  <td colSpan={editable ? 4 : 3} style={{ padding: 20, textAlign: 'center', color: '#94a3b8', fontSize: 13 }}>
                    {t('production.noCostLines')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Card>
      </div>

      {notice && (
        <div
          role="status"
          style={{
            position: 'fixed',
            bottom: 20,
            right: 20,
            zIndex: 50,
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 16px',
            borderRadius: 10,
            background: 'var(--foreground)',
            color: 'white',
            fontSize: 13,
            boxShadow: '0 8px 24px rgba(0,0,0,.15)',
          }}
        >
          {notice}
        </div>
      )}

      {headerOpen && run && (
        <HeaderEditDialog
          run={run}
          products={products}
          t={t}
          busy={busy}
          onCancel={() => setHeaderOpen(false)}
          onSave={patchHeader}
        />
      )}

      {postConfirmOpen && run && (
        <PostConfirmDialog
          run={run}
          productName={productName(run.output_product_id)}
          t={t}
          busy={busy}
          onCancel={() => { if (!busy) setPostConfirmOpen(false) }}
          onConfirm={postRun}
        />
      )}

      {cancelConfirmOpen && run && (
        <CancelConfirmDialog
          run={run}
          t={t}
          busy={busy}
          onCancel={() => { if (!busy) setCancelConfirmOpen(false) }}
          onConfirm={cancelRun}
        />
      )}

      {inputDialog && (
        <InputDialog
          mode={inputDialog.mode}
          input={inputDialog.input}
          products={products}
          t={t}
          busy={busy}
          onCancel={() => setInputDialog(null)}
          onSave={(data) =>
            inputDialog.mode === 'add'
              ? addInput(data)
              : updateInput(inputDialog.input!.id, data)
          }
        />
      )}

      {costLineDialog && (
        <CostLineDialog
          mode={costLineDialog.mode}
          line={costLineDialog.line}
          costTypes={costTypes}
          t={t}
          busy={busy}
          onCancel={() => setCostLineDialog(null)}
          onSave={(data) =>
            costLineDialog.mode === 'add'
              ? addCostLine(data)
              : updateCostLine(costLineDialog.line!.id, data)
          }
        />
      )}

      <style>{`@media(max-width: 900px){ div[style*="gridTemplateColumns: '2fr 1fr'"]{grid-template-columns:1fr!important} div[style*="gridTemplateColumns: '1fr 1fr'"]{grid-template-columns:1fr!important} }`}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Dialogs
// ---------------------------------------------------------------------------

const FIELD_LABEL: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: 0.8,
  textTransform: 'uppercase',
  color: '#8a98ab',
}

const FIELD_INPUT: React.CSSProperties = {
  height: 34,
  borderRadius: 8,
  border: '1px solid var(--border)',
  background: 'white',
  padding: '0 10px',
  fontSize: 13,
  width: '100%',
}

function DialogShell({
  title,
  description,
  onCancel,
  busy,
  onSave,
  saveLabel,
  children,
  t,
  width = 520,
}: {
  title: string
  description?: string
  onCancel: () => void
  busy: boolean
  onSave: () => void
  saveLabel: string
  children: React.ReactNode
  t: (key: string, params?: Record<string, string | number>) => string
  width?: number
}) {
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
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel()
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: width,
          background: 'white',
          borderRadius: 14,
          border: '1px solid var(--border)',
          boxShadow: '0 20px 48px rgba(0,0,0,.12)',
          margin: '40px 0',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>{title}</h2>
            {description && <p style={{ margin: '4px 0 0', fontSize: 12, color: '#718198' }}>{description}</p>}
          </div>
          <button onClick={onCancel} disabled={busy} aria-label="Close" style={{ border: 0, background: 'none', color: '#8a98ab' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20 }}>{children}</div>

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            padding: '14px 20px',
            borderTop: '1px solid var(--border)',
          }}
        >
          <Button variant="outline" onClick={onCancel} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button onClick={onSave} disabled={busy}>
            {busy ? t('common.saving') : saveLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

function HeaderEditDialog({
  run,
  products,
  t,
  busy,
  onCancel,
  onSave,
}: {
  run: ProductionRun
  products: Product[]
  t: (key: string, params?: Record<string, string | number>) => string
  busy: boolean
  onCancel: () => void
  onSave: (runDate: string, outputProductId: number, outputQuantity: number, notes: string) => void
}) {
  const [runDate, setRunDate] = useState(run.run_date ? run.run_date.slice(0, 10) : '')
  const [outputProductId, setOutputProductId] = useState(String(run.output_product_id))
  const [outputQuantity, setOutputQuantity] = useState(String(run.output_quantity))
  const [notes, setNotes] = useState(run.notes ?? '')

  const producibleProducts = useMemo(() => products.filter((p) => p.is_producible && p.is_active), [products])

  return (
    <DialogShell t={t}
      title={t('production.editRun')}
      description={t('production.editRunHelp')}
      onCancel={onCancel}
      busy={busy}
      onSave={() => onSave(runDate, Number(outputProductId), Number(outputQuantity), notes)}
      saveLabel={t('common.save')}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={FIELD_LABEL}>{t('production.runDate')}</label>
            <input type="date" value={runDate} onChange={(e) => setRunDate(e.target.value)} style={FIELD_INPUT} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={FIELD_LABEL}>{t('production.outputProduct')} *</label>
            <select value={outputProductId} onChange={(e) => setOutputProductId(e.target.value)} style={FIELD_INPUT}>
              {producibleProducts.map((p) => (
                <option key={p.id} value={String(p.id)}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={FIELD_LABEL}>{t('production.outputQuantity')} *</label>
            <input
              type="number"
              min="0"
              step="0.0001"
              value={outputQuantity}
              onChange={(e) => setOutputQuantity(e.target.value)}
              style={FIELD_INPUT}
            />
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>{t('common.notes')}</label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            maxLength={2000}
            rows={3}
            style={{ ...FIELD_INPUT, height: 'auto', padding: 10, resize: 'vertical' }}
          />
        </div>
      </div>
    </DialogShell>
  )
}

function InputDialog({
  mode,
  input,
  products,
  t,
  busy,
  onCancel,
  onSave,
}: {
  mode: 'add' | 'edit'
  input?: ProductionInput
  products: Product[]
  t: (key: string, params?: Record<string, string | number>) => string
  busy: boolean
  onCancel: () => void
  onSave: (data: ProductionInputRequest) => void
}) {
  const [productId, setProductId] = useState(input ? String(input.product_id) : '')
  const [quantity, setQuantity] = useState(input ? String(input.quantity) : '1')
  const [localError, setLocalError] = useState<string | null>(null)

  const submit = () => {
    setLocalError(null)
    if (!productId) {
      setLocalError(t('production.inputProductRequired'))
      return
    }
    const q = Number(quantity)
    if (!Number.isFinite(q) || q <= 0) {
      setLocalError(t('production.inputQuantityPositive'))
      return
    }
    onSave({ product_id: Number(productId), quantity: q })
  }

  return (
    <DialogShell t={t}
      title={mode === 'add' ? t('production.addInput') : t('production.editInput')}
      onCancel={onCancel}
      busy={busy}
      onSave={submit}
      saveLabel={mode === 'add' ? t('production.addInput') : t('common.save')}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>{t('production.product')}</label>
          <select
            value={productId}
            onChange={(e) => setProductId(e.target.value)}
            style={FIELD_INPUT}
            disabled={mode === 'edit'}
          >
            <option value="">{t('production.selectProduct')}</option>
            {products.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>{t('production.quantity')}</label>
          <input
            type="number"
            min="0"
            step="0.0001"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            style={FIELD_INPUT}
          />
        </div>
        {localError && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 13, border: '1px solid #fecaca' }}>
            <AlertTriangle size={15} /> {localError}
          </div>
        )}
      </div>
    </DialogShell>
  )
}

function CostLineDialog({
  mode,
  line,
  costTypes,
  t,
  busy,
  onCancel,
  onSave,
}: {
  mode: 'add' | 'edit'
  line?: ProductionCostLine
  costTypes: CostType[]
  t: (key: string, params?: Record<string, string | number>) => string
  busy: boolean
  onCancel: () => void
  onSave: (data: ProductionCostLineRequest) => void
}) {
  const [costTypeId, setCostTypeId] = useState(line ? String(line.cost_type_id) : '')
  const [amount, setAmount] = useState(line ? String(line.amount) : '')
  const [paidInCash, setPaidInCash] = useState(line ? line.paid_in_cash : true)
  const [description, setDescription] = useState(line?.description ?? '')
  const [localError, setLocalError] = useState<string | null>(null)

  const submit = () => {
    setLocalError(null)
    if (!costTypeId) {
      setLocalError(t('production.costTypeRequired'))
      return
    }
    const a = Number(amount)
    if (!Number.isFinite(a) || a <= 0) {
      setLocalError(t('production.costAmountPositive'))
      return
    }
    onSave({
      cost_type_id: Number(costTypeId),
      amount: a,
      paid_in_cash: paidInCash,
      description: description.trim() || null,
    })
  }

  return (
    <DialogShell t={t}
      title={mode === 'add' ? t('production.addCostLine') : t('production.editCostLine')}
      onCancel={onCancel}
      busy={busy}
      onSave={submit}
      saveLabel={mode === 'add' ? t('production.addCostLine') : t('common.save')}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={FIELD_LABEL}>{t('production.costType')} *</label>
            <select value={costTypeId} onChange={(e) => setCostTypeId(e.target.value)} style={FIELD_INPUT}>
              <option value="">{t('production.selectCostType')}</option>
              {costTypes
                .filter((c) => c.is_active)
                .map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.name}
                  </option>
                ))}
            </select>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={FIELD_LABEL}>{t('production.amount')} *</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              style={FIELD_INPUT}
            />
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>{t('production.description')}</label>
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} style={FIELD_INPUT} />
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#334155' }}>
          <input type="checkbox" checked={paidInCash} onChange={(e) => setPaidInCash(e.target.checked)} />
          {t('production.paidInCash')}
        </label>
        {localError && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 13, border: '1px solid #fecaca' }}>
            <AlertTriangle size={15} /> {localError}
          </div>
        )}
      </div>
    </DialogShell>
  )
}

function PostConfirmDialog({
  run,
  productName,
  t,
  busy,
  onCancel,
  onConfirm,
}: {
  run: ProductionRun
  productName: string
  t: (key: string, params?: Record<string, string | number>) => string
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const totalCost = run.total_raw_cost + run.total_overhead_cost
  return (
    <DialogShell t={t}
      title={t('production.postRunTitle')}
      description={t('production.postRunHelp')}
      onCancel={onCancel}
      busy={busy}
      onSave={onConfirm}
      saveLabel={t('production.confirmPost')}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, fontSize: 13 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#718198' }}>{t('production.outputProduct')}</span>
          <span style={{ fontWeight: 600 }}>{productName}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#718198' }}>{t('production.outputQuantity')}</span>
          <span>{run.output_quantity}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#718198' }}>{t('production.totalRawCost')}</span>
          <span>{formatIDR(run.total_raw_cost)}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ color: '#718198' }}>{t('production.totalOverheadCost')}</span>
          <span>{formatIDR(run.total_overhead_cost)}</span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', borderTop: '1px solid var(--border)', paddingTop: 10, fontWeight: 700 }}>
          <span>{t('production.totalCost')}</span>
          <span>{formatIDR(totalCost)}</span>
        </div>
        <p style={{ margin: 0, fontSize: 12, color: '#dc2626' }}>{t('production.postIrreversible')}</p>
      </div>
    </DialogShell>
  )
}

function CancelConfirmDialog({
  run,
  t,
  busy,
  onCancel,
  onConfirm,
}: {
  run: ProductionRun
  t: (key: string, params?: Record<string, string | number>) => string
  busy: boolean
  onCancel: () => void
  onConfirm: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)

  const submit = () => {
    setLocalError(null)
    if (!reason.trim()) {
      setLocalError(t('production.reasonRequired'))
      return
    }
    onConfirm(reason.trim())
  }

  return (
    <DialogShell t={t}
      title={t('production.cancelRunTitle')}
      description={t('production.cancelRunHelp')}
      onCancel={onCancel}
      busy={busy}
      onSave={submit}
      saveLabel={t('production.confirmCancel')}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 13 }}>
        <div style={{ padding: 12, borderRadius: 8, background: '#fffbeb', border: '1px solid #fde68a', color: '#92400e' }}>
          <AlertTriangle size={16} style={{ marginRight: 8, verticalAlign: 'text-bottom' }} />
          {t('production.cancelStockWarning')}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={FIELD_LABEL}>{t('production.cancellationReason')} *</label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={1000}
            rows={3}
            style={{ ...FIELD_INPUT, height: 'auto', padding: 10, resize: 'vertical' }}
          />
        </div>
        {localError && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: '#fef2f2', color: '#dc2626', fontSize: 13, border: '1px solid #fecaca' }}>
            <AlertTriangle size={15} /> {localError}
          </div>
        )}
      </div>
    </DialogShell>
  )
}
