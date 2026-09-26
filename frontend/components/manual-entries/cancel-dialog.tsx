'use client'

import { useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
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
  onConflict: () => void
  t: TFunction
}

/**
 * Cancel-entry confirmation dialog.
 *
 * Follows the audit contract (§6.49–55):
 *  - POST /manual-entries/{id}/cancel requires Idempotency-Key + If-Match (ETag).
 *  - On 412 version_mismatch: calls onConflict() to refresh the entry from
 *    the server rather than silently overwriting another user's change.
 */
export function CancelEntryDialog({
  open,
  entry,
  entryEtag,
  onClose,
  onCancelled,
  onConflict,
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
      await api.headers.post<ManualEntryResponse>(
        `/manual-entries/${entry.id}/cancel`,
        payload,
        {
          idempotencyKey: true,
          ifMatch: entryEtag,
        },
      )
      onCancelled(entry)
      setReason('')
      onClose()
    } catch (err) {
      const code = (err as Error & { code?: string }).code
      if (code === 'version_mismatch' || code === 'precondition_failed') {
        // ETag no longer matches — the entry was modified elsewhere.
        // Surface the conflict and let the parent refresh the entry.
        setError(t('manualEntries.errors.versionMismatch'))
        onConflict()
      } else if (isApiError(err)) {
        setError(err.message || t('manualEntries.cancelError'))
      } else {
        setError(t('manualEntries.cancelError'))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

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
            <div>
              <span className="text-muted-foreground">{t('manualEntries.entryType')}:</span>{' '}
              {entry.entry_type}
            </div>
            <div>
              <span className="text-muted-foreground">{t('manualEntries.category')}:</span>{' '}
              {entry.category_name || `#${entry.category_id}`}
            </div>
            <div>
              <span className="text-muted-foreground">{t('manualEntries.amount')}:</span>{' '}
              {amountStr}
            </div>
            <div>
              <span className="text-muted-foreground">{t('manualEntries.entryDate')}:</span>{' '}
              {new Date(entry.entry_date).toLocaleDateString()}
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          {error && (
            <div className="p-3 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded">
              {error}
            </div>
          )}

          <div className="flex items-start gap-3 p-3 text-sm text-muted-foreground bg-muted/30 border border-border rounded">
            <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" />
            <span>{t('manualEntries.cancelWarning')}</span>
          </div>

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
            <div className="text-xs text-muted-foreground mt-1">
              {reason.length}/1000
            </div>
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
