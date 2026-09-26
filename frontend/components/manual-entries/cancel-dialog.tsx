'use client'

import { useState } from 'react'
import { X } from 'lucide-react'
import { api, isApiError } from '@/lib/api-client'
import type {
  ManualEntryCancelRequest,
  ManualEntryResponse,
} from '@/lib/finance-types'

export type TFunction = (key: string, params?: Record<string, string | number>) => string

export interface CancelEntryDialogProps {
  open: boolean
  entry: ManualEntryResponse
  entryEtag: string
  onClose: () => void
  onCancelled: (entry: ManualEntryResponse) => void
  t: TFunction
}

export function CancelEntryDialog({
  open,
  entry,
  entryEtag,
  onClose,
  onCancelled,
  t,
}: CancelEntryDialogProps) {
  const [reason, setReason] = useState<string>('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!open) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (isSubmitting) return
    if (!reason.trim()) {
      setError(t('manualEntries.cancelReasonRequired'))
      return
    }

    setIsSubmitting(true)
    setError(null)

    const payload: ManualEntryCancelRequest = { reason: reason.trim() }

    try {
      // api.post returns the response data directly (not ApiResult wrapper)
      // If-Match ETag header is passed via the `ifMatch` option (api-client
      // translates it to the HTTP If-Match header).
      // Idempotency-Key is auto-generated via idempotencyKey: true.
      const resp = await api.post<ManualEntryResponse>(
        `/manual-entries/${entry.id}/cancel`,
        payload,
        {
          idempotencyKey: true,
          ifMatch: entryEtag,
        }
      )
      onCancelled(resp)
      setReason('')
      onClose()
    } catch (err) {
      if (isApiError(err)) {
        setError(err.message || t('manualEntries.cancelError'))
      } else {
        setError(t('manualEntries.cancelError'))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const entryDate = new Date(entry.entry_date)
  const amountStr = new Intl.NumberFormat('id-ID').format(entry.amount)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-lg font-semibold">{t('manualEntries.cancelTitle')}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-muted text-muted-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 border-b border-border">
          <div className="text-sm space-y-1">
            <div><span className="text-muted-foreground">{t('manualEntries.entryType')}:</span> {entry.entry_type}</div>
            <div><span className="text-muted-foreground">{t('manualEntries.category')}:</span> {entry.category_name || `#${entry.category_id}`}</div>
            <div><span className="text-muted-foreground">{t('manualEntries.amount')}:</span> {amountStr}</div>
            <div><span className="text-muted-foreground">{t('manualEntries.entryDate')}:</span> {entryDate.toLocaleDateString()}</div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          {error && (
            <div className="p-3 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium mb-1">{t('manualEntries.cancelReason')}</label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value.slice(0, 1000))}
              disabled={isSubmitting}
              maxLength={1000}
              rows={3}
              className="w-full px-3 py-2 border rounded bg-background resize-y"
              placeholder={t('manualEntries.cancelReasonPlaceholder')}
              required
            />
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2 text-sm border rounded hover:bg-muted"
            >
              {t('manualEntries.cancel')}
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !reason.trim()}
              className="px-4 py-2 text-sm bg-destructive text-destructive-foreground rounded hover:bg-destructive/90 disabled:opacity-50"
            >
              {isSubmitting ? t('manualEntries.cancelling') : t('manualEntries.confirmCancel')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
