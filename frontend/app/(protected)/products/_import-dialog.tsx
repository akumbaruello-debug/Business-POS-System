'use client'

import { useState, useRef } from 'react'
import { Download, FileSpreadsheet, Loader2, Upload, X, AlertCircle } from 'lucide-react'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import Papa from 'papaparse'
import * as XLSX from 'xlsx'

type ValidationError = { row: number; errors: string[] }
type ImportResult = { row: number; action: string; product_id?: number; errors?: string[] }

export default function ImportProductsDialog({
  open,
  onClose,
  onNotice,
  onImported,
}: {
  open: boolean
  onClose: () => void
  onNotice: (msg: string) => void
  onImported?: () => void
}) {
  const [step, setStep] = useState<'upload' | 'review' | 'done'>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [parsedRows, setParsedRows] = useState<Record<string, unknown>[]>([])
  const [validation, setValidation] = useState<{ valid: number; invalid: number; errors: ValidationError[] } | null>(null)
  const [commitReport, setCommitReport] = useState<ImportResult[] | null>(null)
  const [loading, setLoading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const reset = () => {
    setStep('upload')
    setFile(null)
    setParsedRows([])
    setValidation(null)
    setCommitReport(null)
    setLoading(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleClose = () => {
    if (loading) return
    reset()
    onClose()
  }

  const handleFile = (f: File) => {
    setFile(f)
    const ext = f.name.split('.').pop()?.toLowerCase()
    if (ext === 'csv') {
      const reader = new FileReader()
      reader.onload = (ev) => {
        const text = String(ev.target?.result ?? '')
        const parsed = Papa.parse<Record<string, unknown>>(text, { header: true, skipEmptyLines: true })
        const rows = parsed.data.filter((r) => Object.values(r).some((v) => String(v ?? '').trim() !== ''))
        setParsedRows(rows)
        void runValidation(rows)
      }
      reader.readAsText(f)
    } else if (ext === 'xlsx' || ext === 'xls') {
      const reader = new FileReader()
      reader.onload = (ev) => {
        try {
          const data = new Uint8Array(ev.target?.result as ArrayBuffer)
          const wb = XLSX.read(data, { type: 'array' })
          const ws = wb.Sheets[wb.SheetNames[0]]
          const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(ws, { defval: '' })
          setParsedRows(rows)
          void runValidation(rows)
        } catch {
          onNotice('Could not read that file. Is it a valid XLSX?')
          setLoading(false)
        }
      }
      reader.readAsArrayBuffer(f)
    } else {
      onNotice('Unsupported file — please use CSV or XLSX.')
      setLoading(false)
    }
  }

  const runValidation = async (rows: Record<string, unknown>[]) => {
    setLoading(true)
    try {
      const res = await api.post<{ total_rows: number; valid: number; invalid: number; errors: ValidationError[] }>(
        '/products/import',
        { rows, commit: false },
      )
      setValidation(res)
      setStep('review')
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Validation request failed')
    } finally {
      setLoading(false)
    }
  }

  const handleCommit = async () => {
    setLoading(true)
    try {
      const res = await api.post<{ total_rows: number; created: number; skipped: number; failed: number; results: ImportResult[] }>(
        '/products/import',
        { rows: parsedRows, commit: true },
        { headers: { 'Idempotency-Key': crypto.randomUUID() } },
      )
      setCommitReport(res.results)
      setStep('done')
      onNotice(`Import committed: ${res.created} created, ${res.failed} failed`)
      onImported?.()
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Import commit failed')
    } finally {
      setLoading(false)
    }
  }

  const downloadTemplate = () => {
    const headers = ['name', 'code', 'category_id', 'unit_id', 'purchase_price', 'selling_price', 'low_stock_threshold', 'allow_negative_stock', 'notes', 'is_sellable', 'is_purchasable', 'is_producible', 'is_active']
    const sample = ['Example Fish', 'FSH-001', '1', '1', '50000', '65000', '10', 'false', '', 'true', 'true', 'false', 'true']
    const csv = [headers, sample].map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'product-import-template.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!open) return null

  const createdCount = commitReport?.filter((r) => r.action === 'created').length ?? 0
  const failedRows = commitReport?.filter((r) => r.action === 'failed') ?? []

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) handleClose() }}
    >
      <div
        role="dialog"
        aria-label="Import products"
        style={{ width: '100%', maxWidth: 600, background: 'white', borderRadius: 14, border: '1px solid var(--border)', boxShadow: '0 20px 48px rgba(0,0,0,.12)', display: 'flex', flexDirection: 'column', maxHeight: '90vh' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Upload size={18} color="var(--primary)" />
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--foreground)' }}>Import products</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>CSV or XLSX — validate before committing</div>
            </div>
          </div>
          <button onClick={handleClose} disabled={loading} aria-label="Close" style={{ background: 'none', border: 0, cursor: loading ? 'not-allowed' : 'pointer', padding: 4, color: 'var(--muted)' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {step === 'upload' && (
            <>
              <div style={{ border: '2px dashed var(--border)', borderRadius: 10, padding: 28, textAlign: 'center', background: '#f8fafc' }}>
                <FileSpreadsheet size={28} color="var(--primary)" style={{ margin: '0 auto' }} />
                <p style={{ marginTop: 10, fontWeight: 600, fontSize: 14 }}>Choose a CSV or XLSX file</p>
                <p style={{ marginTop: 2, fontSize: 12, color: 'var(--muted)' }}>Columns: name*, code, category_id, unit_id, purchase_price, selling_price, low_stock_threshold, allow_negative_stock, notes, is_sellable, is_purchasable, is_producible, is_active</p>
                <div style={{ marginTop: 12 }}>
                  <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xls" onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }} style={{ fontSize: 13 }} />
                </div>
              </div>
              <Button variant="outline" onClick={downloadTemplate} style={{ alignSelf: 'flex-start' }}>
                <Download size={14} /> Download CSV template
              </Button>
            </>
          )}

          {step === 'review' && validation && (
            <>
              <div style={{ display: 'flex', gap: 10 }}>
                <div style={{ flex: 1, padding: '12px 16px', background: '#f8fafc', borderRadius: 8, border: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 22, fontWeight: 700 }}>{validation.valid}</div>
                  <div style={{ fontSize: 12, color: 'var(--muted)' }}>Valid rows</div>
                </div>
                <div style={{ flex: 1, padding: '12px 16px', background: validation.invalid > 0 ? '#fef2f2' : '#f8fafc', borderRadius: 8, border: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 22, fontWeight: 700, color: validation.invalid > 0 ? '#dc2626' : 'inherit' }}>{validation.invalid}</div>
                  <div style={{ fontSize: 12, color: 'var(--muted)' }}>Invalid rows</div>
                </div>
              </div>

              {validation.errors.length > 0 && (
                <div style={{ background: '#fef2f2', borderRadius: 8, padding: 12, border: '1px solid #fecaca', maxHeight: 180, overflowY: 'auto' }}>
                  {validation.errors.map((e) => (
                    <div key={e.row} style={{ fontSize: 12, padding: '3px 0' }}>
                      <strong>Row {e.row}:</strong> {e.errors.join('; ')}
                    </div>
                  ))}
                </div>
              )}

              {validation.invalid === 0 ? (
                <Button onClick={handleCommit} disabled={loading}>
                  {loading ? <><Loader2 size={14} className="spin" /> Committing…</> : `Import ${validation.valid} products`}
                </Button>
              ) : (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, background: '#fffbeb', borderRadius: 8, padding: 10, border: '1px solid #fde68a' }}>
                    <AlertCircle size={16} color="#b45309" />
                    <span style={{ fontSize: 13, color: '#78350f' }}>Fix the invalid rows in your file and re-upload. Nothing was committed.</span>
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <Button variant="outline" onClick={() => { setStep('upload') }}>Choose another file</Button>
                    <Button variant="ghost" onClick={handleClose}>Cancel</Button>
                  </div>
                </>
              )}
            </>
          )}

          {step === 'done' && commitReport && (
            <>
              <div style={{ background: '#ecfdf5', borderRadius: 8, padding: 16, border: '1px solid #a7f3d0' }}>
                <div style={{ fontSize: 22, fontWeight: 700, color: '#065f46' }}>{createdCount} imported</div>
                {failedRows.length > 0 && (
                  <div style={{ marginTop: 8, color: '#991b1b' }}>
                    <p style={{ fontWeight: 600 }}>{failedRows.length} failed:</p>
                    {failedRows.map((r) => (
                      <div key={r.row} style={{ fontSize: 12, padding: '2px 0' }}>
                        Row {r.row}: {r.errors?.join('; ')}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <Button onClick={() => { reset(); onClose() }}>Done</Button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}