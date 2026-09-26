'use client'

import { useState } from 'react'
import { X } from 'lucide-react'
import { api, isApiError } from '@/lib/api-client'
import type {
  FinancialCategory,
  PaymentMethod,
  ManualEntryRequest,
  ManualEntryResponse,
} from '@/lib/finance-types'

export type TFunction = (key: string, params?: Record<string, string | number>) => string

export interface CreateEntryDialogProps {
  open: boolean
  onClose: () => void
  categories: FinancialCategory[]
  paymentMethods: PaymentMethod[]
  availableTypes: ('income' | 'expense')[]
  onCreated: (entry: ManualEntryResponse) => void
  t: TFunction
}

export function CreateEntryDialog({
  open,
  onClose,
  categories,
  paymentMethods,
  availableTypes,
  onCreated,
  t,
}: CreateEntryDialogProps) {
  // Default type: prefer income if available, else expense
  const defaultType = availableTypes.includes('income') ? 'income' : 'expense'
  const [entryType, setEntryType] = useState<'income' | 'expense'>(defaultType)
  const [categoryId, setCategoryId] = useState<string>('')
  const [paymentMethodId, setPaymentMethodId] = useState<string>('')
  const [amount, setAmount] = useState<string>('')
  const [notes, setNotes] = useState<string>('')
  const [entryDate, setEntryDate] = useState<string>('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset form when dialog opens with a fresh type
  const handleOpenType = (type: 'income' | 'expense') => {
    setEntryType(type)
    setCategoryId('')
    setPaymentMethodId('')
    setAmount('')
    setNotes('')
    setEntryDate('')
    setError(null)
  }

  const filteredCategories = categories.filter((c) => c.entry_type === entryType)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (isSubmitting) return

    const amt = parseFloat(amount)
    if (!categoryId || !paymentMethodId || !amount || isNaN(amt) || amt <= 0) {
      setError(t('manualEntries.validationRequired'))
      return
    }

    setIsSubmitting(true)
    setError(null)

    const nowISO = entryDate || new Date().toISOString().slice(0, 16)
    const payload: ManualEntryRequest = {
      category_id: parseInt(categoryId, 10),
      entry_type: entryType,
      amount: amt,
      payment_method_id: parseInt(paymentMethodId, 10),
      entry_date: nowISO,
      notes: notes || null,
    }

    try {
      const resp = await api.post<ManualEntryResponse>(
        '/manual-entries',
        payload,
        { idempotencyKey: true },
      )
      onCreated(resp)
      onClose()
    } catch (err) {
      if (isApiError(err)) {
        // Idempotency violation (409, code 'idempotency_violation') or
        // other API error — the message surfaces the backend detail.
        setError(err.message || t('manualEntries.createError'))
      } else {
        setError(t('manualEntries.createError'))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-card border border-border rounded-lg shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h2 className="text-lg font-semibold">
            {t(entryType === 'income' ? 'manualEntries.createIncome' : 'manualEntries.createExpense')}
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-muted text-muted-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          {error && (
            <div className="p-3 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded">
              {error}
            </div>
          )}

          {/* Entry type selector — only show if both types are available */}
          {availableTypes.length > 1 && (
            <div>
              <label className="block text-sm font-medium mb-1">{t('manualEntries.entryType')}</label>
              <div className="flex gap-4">
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="entryType"
                    value="income"
                    checked={entryType === 'income'}
                    onChange={() => handleOpenType('income')}
                    disabled={isSubmitting}
                  />
                  {t('manualEntries.income')}
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="entryType"
                    value="expense"
                    checked={entryType === 'expense'}
                    onChange={() => handleOpenType('expense')}
                    disabled={isSubmitting}
                  />
                  {t('manualEntries.expense')}
                </label>
              </div>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium mb-1">{t('manualEntries.category')}</label>
            <select
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              disabled={isSubmitting}
              className="w-full px-3 py-2 border rounded bg-background"
              required
            >
              <option value="">{t('manualEntries.selectCategory')}</option>
              {filteredCategories.map((c) => (
                <option key={c.id} value={String(c.id)}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">{t('manualEntries.paymentMethod')}</label>
            <select
              value={paymentMethodId}
              onChange={(e) => setPaymentMethodId(e.target.value)}
              disabled={isSubmitting}
              className="w-full px-3 py-2 border rounded bg-background"
              required
            >
              <option value="">{t('manualEntries.selectMethod')}</option>
              {paymentMethods.map((m) => (
                <option key={m.id} value={String(m.id)}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">{t('manualEntries.amount')}</label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              disabled={isSubmitting}
              className="w-full px-3 py-2 border rounded bg-background"
              placeholder="0"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">{t('manualEntries.entryDate')}</label>
            <input
              type="datetime-local"
              value={entryDate}
              onChange={(e) => setEntryDate(e.target.value)}
              disabled={isSubmitting}
              className="w-full px-3 py-2 border rounded bg-background"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">{t('manualEntries.notes')}</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value.slice(0, 2000))}
              disabled={isSubmitting}
              maxLength={2000}
              rows={3}
              className="w-full px-3 py-2 border rounded bg-background resize-y"
              placeholder={t('manualEntries.notesPlaceholder')}
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
              disabled={isSubmitting}
              className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90 disabled:opacity-50"
            >
              {isSubmitting ? t('manualEntries.saving') : t('manualEntries.save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
