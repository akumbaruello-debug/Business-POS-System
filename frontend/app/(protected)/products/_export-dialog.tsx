/**
 * Export Products dialog — generates a real, downloadable PDF/XLSX file.
 *
 * Backend: POST /exports → async job → poll GET /exports/{id} → download.
 *   Contract: see backend/app/validation/exports_schemas.py
 *     report (string), format ('pdf' | 'xlsx'), filters (dict)
 *     job: { export_id, status, download_url, expires_at, error, created_at }
 *   Statuses: queued | processing | complete | failed.
 *
 * Filters sent to backend:
 *   period     — 'all' | 'today' | 'this_week' | 'this_month' | 'custom'
 *   from_iso   — ISO date (only when period=custom)
 *   to_iso     — ISO date (only when period=custom)
 *   filter[is_active]    — 'all' | 'active' | 'inactive'
 *   filter[category_id]  — 'all' | <int>
 *   filter[unit_id]      — 'all' | <int>
 *
 * Dataset: server-side filtering always applies; "Current filtered results"
 * is honored by including the current active filters in the request.
 *
 * Date dimension: products are filtered on `created_at` (the only date
 * the product table exposes). For "all" the date range is unbounded.
 */

'use client'

import { useEffect, useState } from 'react'
import { Check, Download, FileText, Loader2, X } from 'lucide-react'
import { API_BASE_URL } from '@/lib/constants'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'

type Format = 'pdf' | 'xlsx'
type PeriodPreset = 'all' | 'today' | 'this_week' | 'this_month' | 'custom'
type StatusFilter = 'all' | 'active' | 'inactive'

interface ExportProductsDialogProps {
  open: boolean
  onClose: () => void
  defaultIsActive: StatusFilter
  defaultCategoryId: number | 'all'
  defaultUnitId: number | 'all'
  categoryOptions: { id: number; name: string }[]
  unitOptions: { id: number; name: string }[]
  onNotice: (msg: string) => void
}

interface ExportJob {
  export_id: string
  status: 'queued' | 'processing' | 'complete' | 'failed'
  download_url: string | null
  expires_at: string | null
  error: string | null
  created_at: string
}

function startOfIsoDay(d: Date): string {
  // Local-day YYYY-MM-DD → treat as local midnight → ISO with offset.
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  // Use local-date semantics: we send the date as YYYY-MM-DD and let
  // the backend normalise. Backend accepts both date and datetime.
  return `${y}-${m}-${day}`
}

export default function ExportProductsDialog({
  open,
  onClose,
  defaultIsActive,
  defaultCategoryId,
  defaultUnitId,
  categoryOptions,
  unitOptions,
  onNotice,
}: ExportProductsDialogProps) {
  const [format, setFormat] = useState<Format>('pdf')
  const [period, setPeriod] = useState<PeriodPreset>('all')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>(defaultIsActive)
  const [categoryId, setCategoryId] = useState<number | 'all'>(defaultCategoryId)
  const [unitId, setUnitId] = useState<number | 'all'>(defaultUnitId)

  // Track the running export — once queued, we lock the form and poll.
  const [submitting, setSubmitting] = useState(false)
  const [job, setJob] = useState<ExportJob | null>(null)
  const [downloading, setDownloading] = useState(false)

  // Reset transient state when reopened.
  useEffect(() => {
    if (open) {
      setFormat('pdf')
      setPeriod('all')
      setFromDate('')
      setToDate('')
      setStatusFilter(defaultIsActive)
      setCategoryId(defaultCategoryId)
      setUnitId(defaultUnitId)
      setSubmitting(false)
      setJob(null)
      setDownloading(false)
    }
  }, [open, defaultIsActive, defaultCategoryId, defaultUnitId])

  // Lock body scroll while open (matches existing dialog pattern).
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [open])

  // Poll status when we have a non-terminal job. Cap at 60 attempts
  // (60s) so a hung job does not poll forever (rate-limit / request-loop guard).
  useEffect(() => {
    if (!open || !job) return
    if (job.status === 'complete' || job.status === 'failed') return
    const MAX_POLLS = 60
    let attempts = 0
    const id = window.setInterval(async () => {
      attempts += 1
      try {
        const next = await api.get<ExportJob>(`/exports/${job.export_id}`)
        setJob(next)
        if (next.status === 'complete' || next.status === 'failed') {
          window.clearInterval(id)
        }
      } catch (err) {
        if (process.env.NODE_ENV !== 'production') {
          console.warn('export poll failed', err)
        }
        if (attempts >= MAX_POLLS) {
          window.clearInterval(id)
          setJob((prev) => (prev ? { ...prev, status: 'failed', error: 'Export timed out' } : prev))
        }
      }
    }, 1000)
    return () => window.clearInterval(id)
  }, [open, job])

  if (!open) return null

  const customRangeInvalid =
    period === 'custom' && (fromDate === '' || toDate === '' || fromDate > toDate)

  const canSubmit = !submitting && !customRangeInvalid

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setJob(null)
    try {
      const filters: Record<string, unknown> = { period }
      if (period === 'custom') {
        filters.from_iso = fromDate
        filters.to_iso = toDate
      }
      if (statusFilter !== 'all') filters['filter[is_active]'] = statusFilter
      if (categoryId !== 'all') filters['filter[category_id]'] = String(categoryId)
      if (unitId !== 'all') filters['filter[unit_id]'] = String(unitId)

      const created = await api.post<ExportJob>(
        '/exports',
        { report: 'products', format, filters },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
      )
      setJob(created)
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Export failed')
      setSubmitting(false)
    }
  }

  const handleDownload = async () => {
    if (!job || job.status !== 'complete' || !job.download_url) return
    setDownloading(true)
    try {
      const token = localStorage.getItem('access_token')
      const url = job.download_url.startsWith('http')
        ? job.download_url
        : `${API_BASE_URL}${job.download_url}`
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        credentials: 'include',
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      const filename = `products-${job.export_id}.${format}`
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(a.href)
      onNotice(`Saved ${filename}`)
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setDownloading(false)
    }
  }

  const handleClose = () => {
    if (submitting || downloading) return
    onClose()
  }

  const showForm = !job
  const showProgress = job && (job.status === 'queued' || job.status === 'processing')
  const showResult = job && (job.status === 'complete' || job.status === 'failed')

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) handleClose() }}
    >
      <div
        role="dialog"
        aria-label="Export products"
        style={{ width: '100%', maxWidth: 520, background: 'white', borderRadius: 14, border: '1px solid var(--border)', boxShadow: '0 20px 48px rgba(0,0,0,.12)', display: 'flex', flexDirection: 'column', maxHeight: '90vh' }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Download size={18} color="var(--primary)" />
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--foreground)' }}>Export products</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>PDF or Excel (.xlsx) of your product catalog</div>
            </div>
          </div>
          <button
            onClick={handleClose}
            disabled={submitting || downloading}
            aria-label="Close"
            style={{ background: 'none', border: 0, cursor: submitting || downloading ? 'not-allowed' : 'pointer', padding: 4, color: 'var(--muted)' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: 20, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {showForm && (
            <>
              {/* Format */}
              <Field label="Format">
                <div role="radiogroup" aria-label="Export format" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  {(['pdf', 'xlsx'] as Format[]).map((f) => {
                    const selected = format === f
                    return (
                      <button
                        key={f}
                        role="radio"
                        aria-checked={selected}
                        onClick={() => setFormat(f)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 10,
                          padding: '10px 12px', borderRadius: 8, cursor: 'pointer',
                          border: `1px solid ${selected ? 'var(--primary)' : 'var(--border)'}`,
                          background: selected ? '#eff6ff' : 'white',
                        }}
                      >
                        <FileText size={16} color={selected ? 'var(--primary)' : 'var(--muted)'} />
                        <div style={{ textAlign: 'left' }}>
                          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>
                            {f === 'pdf' ? 'PDF document' : 'Excel (.xlsx)'}
                          </div>
                          <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                            {f === 'pdf' ? 'Print-ready report' : 'Spreadsheet for analysis'}
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </Field>

              {/* Period */}
              <Field label="Period" hint="Filters by when the product was created.">
                <select
                  aria-label="Period"
                  value={period}
                  onChange={(e) => setPeriod(e.target.value as PeriodPreset)}
                  style={selectStyle()}
                >
                  <option value="all">All time</option>
                  <option value="today">Today</option>
                  <option value="this_week">This week</option>
                  <option value="this_month">This month</option>
                  <option value="custom">Custom range…</option>
                </select>
                {period === 'custom' && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 8 }}>
                    <input
                      type="date"
                      aria-label="From date"
                      value={fromDate}
                      max={toDate || undefined}
                      onChange={(e) => setFromDate(e.target.value)}
                      style={inputStyle()}
                    />
                    <input
                      type="date"
                      aria-label="To date"
                      value={toDate}
                      min={fromDate || undefined}
                      onChange={(e) => setToDate(e.target.value)}
                      style={inputStyle()}
                    />
                  </div>
                )}
                {customRangeInvalid && (
                  <div style={errorText()}>Pick a valid from/to date.</div>
                )}
              </Field>

              {/* Filters */}
              <Field label="Dataset" hint="Current page filters are sent to the export so it matches what you see.">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                  <select
                    aria-label="Status"
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
                    style={selectStyle()}
                  >
                    <option value="all">All status</option>
                    <option value="active">Active only</option>
                    <option value="inactive">Inactive only</option>
                  </select>
                  <select
                    aria-label="Category"
                    value={String(categoryId)}
                    onChange={(e) => setCategoryId(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                    style={selectStyle()}
                    disabled={categoryOptions.length === 0}
                  >
                    <option value="all">All categories</option>
                    {categoryOptions.map((c) => (
                      <option key={c.id} value={String(c.id)}>{c.name}</option>
                    ))}
                  </select>
                  <select
                    aria-label="Unit"
                    value={String(unitId)}
                    onChange={(e) => setUnitId(e.target.value === 'all' ? 'all' : Number(e.target.value))}
                    style={selectStyle()}
                    disabled={unitOptions.length === 0}
                  >
                    <option value="all">All units</option>
                    {unitOptions.map((u) => (
                      <option key={u.id} value={String(u.id)}>{u.name}</option>
                    ))}
                  </select>
                </div>
              </Field>
            </>
          )}

          {showProgress && job && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 16, background: '#f8fafc', borderRadius: 10, border: '1px solid var(--border)' }}>
              <Loader2 size={20} color="var(--primary)" className="spin" />
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--foreground)' }}>
                  {job.status === 'queued' ? 'Queued…' : 'Generating your export…'}
                </div>
                <div style={{ fontSize: 11, color: 'var(--muted)' }}>Job {job.export_id}</div>
              </div>
            </div>
          )}

          {showResult && job && job.status === 'complete' && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: 16, background: '#ecfdf5', borderRadius: 10, border: '1px solid #a7f3d0' }}>
              <Check size={20} color="#059669" />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#065f46' }}>Export ready</div>
                <div style={{ fontSize: 11, color: '#047857', marginTop: 2 }}>
                  Job {job.export_id}
                </div>
                {job.expires_at && (
                  <div style={{ fontSize: 11, color: '#047857' }}>
                    Download link expires {new Date(job.expires_at).toLocaleString()}
                  </div>
                )}
              </div>
            </div>
          )}

          {showResult && job && job.status === 'failed' && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: 16, background: '#fef2f2', borderRadius: 10, border: '1px solid #fecaca' }}>
              <X size={20} color="#dc2626" />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#991b1b' }}>Export failed</div>
                <div style={{ fontSize: 12, color: '#b91c1c', marginTop: 2 }}>
                  {job.error || 'Unknown error'}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '12px 20px', borderTop: '1px solid var(--border)' }}>
          {showForm && (
            <>
              <Button variant="outline" onClick={handleClose} disabled={submitting}>Cancel</Button>
              <Button onClick={handleSubmit} disabled={!canSubmit}>
                {submitting ? <><Loader2 size={14} className="spin" /> Submitting…</> : <>Generate {format.toUpperCase()}</>}
              </Button>
            </>
          )}
          {showProgress && (
            <Button variant="outline" onClick={handleClose} disabled>Working…</Button>
          )}
          {showResult && job?.status === 'complete' && (
            <>
              <Button variant="outline" onClick={handleClose}>Close</Button>
              <Button onClick={handleDownload} disabled={downloading}>
                {downloading ? <><Loader2 size={14} className="spin" /> Downloading…</> : <><Download size={14} /> Download</>}
              </Button>
            </>
          )}
          {showResult && job?.status === 'failed' && (
            <>
              <Button variant="outline" onClick={() => { setJob(null); setSubmitting(false) }}>Try again</Button>
              <Button onClick={handleClose}>Close</Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--foreground)', marginBottom: 4 }}>{label}</label>
      {children}
      {hint && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>{hint}</div>}
    </div>
  )
}

function selectStyle(): React.CSSProperties {
  return {
    width: '100%', height: 36, borderRadius: 6, border: '1px solid var(--border)',
    background: 'var(--background)', padding: '0 10px', fontSize: 13, color: 'var(--foreground)',
  }
}
function inputStyle(): React.CSSProperties {
  return { ...selectStyle(), padding: '0 10px' }
}
function errorText(): React.CSSProperties {
  return { fontSize: 11, color: '#dc2626', marginTop: 4 }
}
