'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { Button } from '@/components/ui/button'

// -----------------------------------------------------------------------------
// Shared Sales dialogs.
//
// One dialog implementation for every sensitive Sales action (list page and
// detail page both mount these), so there are never two incompatible modal
// systems. Behaviour it guarantees:
//
//   * Escape + backdrop click close, but NEVER while `busy` (no stray
//     dismissal mid-request).
//   * Focus moves into the dialog on open and back to the trigger on close.
//   * The confirm button is disabled while `busy`, which is what prevents a
//     duplicate submit (the handler also guards, belt and braces).
// -----------------------------------------------------------------------------

export function SalesDialog({
  title,
  onClose,
  busy = false,
  children,
  labelledBy,
}: {
  title: string
  onClose: () => void
  busy?: boolean
  children: React.ReactNode
  labelledBy?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLElement | null>(null)
  const autoId = useId()
  const titleId = labelledBy ?? `sales-dialog-${autoId}`

  useEffect(() => {
    trigger.current = document.activeElement as HTMLElement
    ref.current?.focus()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('keydown', key)
      trigger.current?.focus()
    }
  }, [onClose, busy])

  return (
    <div
      className="sales-dialog-backdrop"
      onMouseDown={() => !busy && onClose()}
    >
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="sales-dialog"
        style={{ outline: 'none' }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="sales-dialog-head">
          <h3 id={titleId}>{title}</h3>
          <button
            type="button"
            className="sales-dialog-close"
            aria-label="Close dialog"
            onClick={onClose}
            disabled={busy}
          >
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** Confirmation body shared by Post + Cancel (reason entry lives in the caller). */
export function SalesDialogWarning({ children }: { children: React.ReactNode }) {
  return (
    <div className="sales-dialog-warn">
      <AlertTriangle size={16} />
      <span>{children}</span>
    </div>
  )
}

export const CANCEL_REASON_MAX = 1000

/**
 * Cancel-sale dialog: confirmation + required reason in ONE flow (never a
 * `window.prompt`). Enforces the contract bound (1–1000 chars) before the
 * submit handler can fire.
 */
export function CancelSaleDialog({
  reference,
  busy,
  error,
  onClose,
  onConfirm,
}: {
  reference: string
  busy: boolean
  error?: string | null
  onClose: () => void
  onConfirm: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const trimmed = reason.trim()
  const tooLong = reason.length > CANCEL_REASON_MAX
  const canSubmit = trimmed.length >= 1 && !tooLong && !busy

  return (
    <SalesDialog title="Cancel this sale?" onClose={onClose} busy={busy}>
      <SalesDialogWarning>
        Cancelling <strong>{reference}</strong> is irreversible and reverses the
        stock and cash effects already recorded. The sale cannot be reopened.
      </SalesDialogWarning>
      <div className="sales-dialog-body">
        <label className="sales-field">
          <span className="sales-field-label">Reason for cancellation</span>
          <textarea
            className="sales-textarea"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Tell us why this sale is being cancelled"
            maxLength={CANCEL_REASON_MAX}
            disabled={busy}
            autoFocus
            aria-required="true"
            aria-invalid={trimmed.length === 0}
          />
          <span className="sales-field-foot">
            <span>
              {trimmed.length === 0 && (
                <span className="sales-field-error">A reason is required</span>
              )}
            </span>
            <span className={`sales-char-count${tooLong ? ' over' : ''}`}>
              {reason.length}/{CANCEL_REASON_MAX}
            </span>
          </span>
        </label>
        {error && <span className="sales-field-error">{error}</span>}
      </div>
      <div className="sales-dialog-foot">
        <Button variant="outline" onClick={onClose} disabled={busy}>
          Keep sale
        </Button>
        <Button
          variant="destructive"
          disabled={!canSubmit}
          onClick={() => onConfirm(trimmed)}
        >
          {busy ? 'Cancelling…' : 'Cancel sale'}
        </Button>
      </div>
    </SalesDialog>
  )
}

/** Post-sale confirmation. Posting finalizes the sale and fires stock/cost effects. */
export function PostSaleDialog({
  reference,
  busy,
  error,
  onClose,
  onConfirm,
}: {
  reference: string
  busy: boolean
  error?: string | null
  onClose: () => void
  onConfirm: () => void
}) {
  return (
    <SalesDialog title="Post this sale?" onClose={onClose} busy={busy}>
      <p className="sales-dialog-note">
        Posting <strong>{reference}</strong> finalizes the transaction and issues
        its stock and financial effects. Posted lines can no longer be edited.
      </p>
      {error && <span className="sales-field-error">{error}</span>}
      <div className="sales-dialog-foot">
        <Button variant="outline" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button onClick={onConfirm} disabled={busy}>
          {busy ? 'Posting…' : 'Post sale'}
        </Button>
      </div>
    </SalesDialog>
  )
}

/** Delete-draft confirmation. This is a hard delete, distinct from cancelling. */
export function DeleteDraftDialog({
  reference,
  busy,
  error,
  onClose,
  onConfirm,
}: {
  reference: string
  busy: boolean
  error?: string | null
  onClose: () => void
  onConfirm: () => void
}) {
  return (
    <SalesDialog title="Delete this draft?" onClose={onClose} busy={busy}>
      <SalesDialogWarning>
        Draft <strong>{reference}</strong> will be permanently deleted. This is
        not the same as cancelling a posted sale and cannot be undone.
      </SalesDialogWarning>
      {error && <span className="sales-field-error">{error}</span>}
      <div className="sales-dialog-foot">
        <Button variant="outline" onClick={onClose} disabled={busy}>
          Keep draft
        </Button>
        <Button variant="destructive" onClick={onConfirm} disabled={busy}>
          {busy ? 'Deleting…' : 'Delete draft'}
        </Button>
      </div>
    </SalesDialog>
  )
}
