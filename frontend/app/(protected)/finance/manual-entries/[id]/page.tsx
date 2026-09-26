'use client'

import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, RefreshCw, AlertCircle, X } from 'lucide-react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { CancelEntryDialog } from '@/components/manual-entries/cancel-dialog'
import type {
  ManualEntry,
  PaymentMethod,
} from '@/lib/finance-types'
import { fetchPaymentMethods } from '@/lib/payment-methods-service'
import { formatIDR, formatDateTime } from '@/lib/format'
import { num, sanitizeEtag } from '@/lib/sale-ui'
import { useCan } from '@/lib/authz'
import { useLanguage } from '@/lib/i18n'

// Backend contract (backend/app/api/v1/manual_entries.py):
//   GET  /manual-entries/{id}  → ManualEntryResponse (ETag header)
//   POST /manual-entries/{id}/cancel  → ManualEntryResponse (Idempotency-Key + If-Match)
//
// Capabilities:
//   manual_entry.view   → detail
//   manual_entry.cancel → POST /cancel (with If-Match ETag)

export default function ManualEntryDetailPage() {
  const params = useParams<{ id: string }>()
  const rawId = params?.id ?? ''
  const entryId = Number(rawId)

  const { t, language } = useLanguage()
  const { can } = useCan()

  const canView = can('manual_entry.view')
  const canCancel = can('manual_entry.cancel')

  const [entry, setEntry] = useState<ManualEntry | null>(null)
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [etag, setEtag] = useState('')

  // Cancel dialog state
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const fetchEntry = useCallback(async () => {
    if (!Number.isFinite(entryId) || entryId <= 0) {
      setNotFound(true)
      setLoading(false)
      return
    }
    if (!canView) {
      setLoading(false)
      setError('Missing capability: manual_entry.view')
      return
    }
    setLoading(true)
    setError(null)
    setNotFound(false)
    try {
      const res = await api.headers.get<ManualEntry>(`/manual-entries/${entryId}`)
      setEntry(res.data)
      const headerEtag = sanitizeEtag(res.headers.get('etag'))
      setEtag(headerEtag)
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('manualEntries.failedToLoad')
      const code = (err as Error & { code?: string }).code
      if (code === 'not_found' || /not found|404/i.test(msg)) {
        setNotFound(true)
        setEntry(null)
      } else {
        setError(msg)
      }
    } finally {
      setLoading(false)
    }
  }, [entryId, canView, t])

  useEffect(() => {
    void fetchEntry()
  }, [fetchEntry])

  // Fetch payment methods for name resolution (best-effort)
  useEffect(() => {
    if (!canView) return
    let cancelled = false
    ;(async () => {
      try {
        const methods = await fetchPaymentMethods()
        if (!cancelled) setPaymentMethods(methods)
      } catch {
        /* best-effort */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [canView])

  const handleCancelled = (_refreshed: ManualEntry) => {
    void fetchEntry()
  }

  const handleCancelConflict = () => {
    setCancelDialogOpen(false)
    void fetchEntry()
  }

  const methodMap = new Map(paymentMethods.map((m) => [m.id, m]))
  const methodName = entry ? methodMap.get(entry.payment_method_id)?.name ?? `#${entry.payment_method_id}` : '—'

  const isPosted = entry?.lifecycle_status === 'posted'
  const isCancelled = entry?.lifecycle_status === 'cancelled'
  const canPerformCancel = canCancel && isPosted

  if (!canView) {
    return (
      <div className="content">
        <div className="page-heading">
          <div>
            <h1>{t('manualEntries.title')}</h1>
          </div>
        </div>
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={32} />
          </div>
          <strong>{t('common.accessRestricted')}</strong>
          <p>{t('common.permissionContactAdmin')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      {/* Back button */}
      <div style={{ marginBottom: 12 }}>
        <Link href="/finance/manual-entries" className="sales-ref">
          <ArrowLeft size={16} /> {t('common.back')}
        </Link>
      </div>

      {/* Heading */}
      <div className="page-heading">
        <div>
          <h1>{t('manualEntries.title')}</h1>
          {entry && (
            <p className="muted">
              {isCancelled
                ? t('manualEntries.status.cancelled')
                : t('manualEntries.status.posted')}
            </p>
          )}
        </div>
        <div className="heading-actions">
          <Button variant="outline" onClick={() => void fetchEntry()} disabled={loading || busy}>
            <RefreshCw size={14} />
            {t('common.refresh')}
          </Button>
        </div>
      </div>

      {/* Loading skeleton */}
      {loading && (
        <div style={{ padding: 16 }}>
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 40, marginBottom: 8 }} />
          ))}
        </div>
      )}

      {/* Error state */}
      {!loading && error && (
        <div className="error-state">
          <div className="error-icon">
            <AlertCircle size={28} />
          </div>
          <strong>{t('manualEntries.failedToLoad')}</strong>
          <p style={{ maxWidth: 440, fontSize: 13 }}>{error}</p>
          <Button variant="outline" onClick={() => void fetchEntry()}>
            <RefreshCw size={14} />
            {t('common.tryAgain')}
          </Button>
        </div>
      )}

      {/* Not found */}
      {!loading && notFound && (
        <div className="empty-workspace">
          <div className="empty-icon">
            <AlertCircle size={26} />
          </div>
          <strong>{t('manualEntries.notFound', { id: String(entryId) })}</strong>
          <p>{t('manualEntries.notFoundDesc')}</p>
          <Button variant="outline" asChild>
            <Link href="/finance/manual-entries">{t('manualEntries.backToList')}</Link>
          </Button>
        </div>
      )}

      {/* Detail content */}
      {!loading && entry && (
        <section className="panel">
          <div className="sales-detail-grid">
            <DetailRow label={t('manualEntries.col.category')} value={entry.category_name || `#${entry.category_id}`} />
            <DetailRow label={t('manualEntries.col.type')} value={entry.entry_type === 'income' ? t('manualEntries.income') : t('manualEntries.expense')} />
            <DetailRow label={t('manualEntries.col.amount')} value={formatIDR(num(entry.amount), language)} />
            <DetailRow label={t('manualEntries.col.paymentMethod')} value={methodName} />
            <DetailRow label={t('manualEntries.entryDate')} value={formatDateTime(entry.entry_date, language)} />
            <DetailRow label={t('manualEntries.notes')} value={entry.notes || '—'} />
            <DetailRow label={t('manualEntries.col.status')} value={isCancelled ? t('manualEntries.status.cancelled') : t('manualEntries.status.posted')} />
            <DetailRow label={t('common.created')} value={formatDateTime(entry.created_at, language)} />
            <DetailRow label={t('manualEntries.col.createdBy')} value={String(entry.created_by)} />
            {isCancelled && (
              <>
                <DetailRow label={t('manualEntries.cancelReason')} value={entry.cancellation_reason || '—'} />
                <DetailRow label={t('manualEntries.cancelledAt')} value={entry.cancellation_date ? formatDateTime(entry.cancellation_date, language) : '—'} />
                <DetailRow label={t('manualEntries.cancelledBy')} value={entry.cancelled_by ? String(entry.cancelled_by) : '—'} />
              </>
            )}
          </div>

          {/* Primary action: cancel */}
          {canPerformCancel && (
            <div className="sales-detail-actions">
              <Button
                variant="destructive"
                onClick={() => setCancelDialogOpen(true)}
                disabled={busy}
              >
                <X size={14} />
                {t('manualEntries.cancelEntry')}
              </Button>
            </div>
          )}
        </section>
      )}

      {/* Cancel dialog */}
      {cancelDialogOpen && entry && (
        <CancelEntryDialog
          open={cancelDialogOpen}
          onClose={() => setCancelDialogOpen(false)}
          entry={entry}
          entryEtag={etag}
          onCancelled={handleCancelled}
          onConflict={handleCancelConflict}
          t={(key, params) => t(key, params)}
        />
      )}
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="sales-detail-stat">
      <span className="sales-field-label">{label}</span>
      <span style={{ fontSize: 14, fontWeight: 600 }}>{value}</span>
    </div>
  )
}
