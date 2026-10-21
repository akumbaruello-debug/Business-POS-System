'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api, isApiError } from '@/lib/api-client'
import { formatIDR } from '@/lib/format'
import { useCan } from '@/lib/authz'
import { useLanguage } from '@/lib/i18n'
import { Button } from '@/components/ui/button'

type PendingBatch = {
  id: number; purchase_line_id: number; unit_code_snapshot: string; base_unit_code_snapshot: string
  factor_to_base_snapshot: number; original_quantity: number; original_value: number
  available_quantity: number; inventory_value: number
}
type Product = { id: number; name: string; unit_id: number; unit_code: string; factor_to_base: number }
type Category = { id: number; name: string }
type Output = { product_id: number; quantity: number }
type Preview = { input_quantity: number; source_value: number; available_quantity: number; available_value: number; outputs: Array<{ product_id: number; product_name: string; quantity: number; value_snapshot: number }>; loss_value: number; remaining_quantity: number; remaining_value: number; reconciles: boolean }

const inputStyle: React.CSSProperties = { width: '100%', padding: '9px 10px', border: '1px solid var(--border)', borderRadius: 7, background: 'white', fontSize: 13 }

export default function SortingPage() {
  const { can } = useCan()
  const { t } = useLanguage()
  const allowed = can('inventory.sort')
  const [batches, setBatches] = useState<PendingBatch[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [batchId, setBatchId] = useState('')
  const [inputQty, setInputQty] = useState('')
  const [lossQty, setLossQty] = useState('0')
  const [remainingQty, setRemainingQty] = useState('0')
  const [categoryId, setCategoryId] = useState('')
  const [reason, setReason] = useState('')
  const [outputs, setOutputs] = useState<Output[]>([{ product_id: 0, quantity: 0 }])
  const [preview, setPreview] = useState<Preview | null>(null)
  const [event, setEvent] = useState<{ id: number; version: number } | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [pending, productList, categoryList] = await Promise.all([
        api.get<{ data: PendingBatch[] }>('/pending-receipts'),
        api.get<{ data: Product[] }>('/sorting-products'),
        api.get<{ data: Category[] }>('/sorting-expense-categories'),
      ])
      setBatches(pending.data); setProducts(productList.data); setCategories(categoryList.data)
      if (!batchId && pending.data[0]) { setBatchId(String(pending.data[0].id)); setInputQty(String(pending.data[0].available_quantity)) }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load pending receipts.')
    } finally { setLoading(false) }
  }, [batchId])

  useEffect(() => { void refresh() }, [refresh])

  const selectedBatch = useMemo(() => batches.find((batch) => String(batch.id) === batchId), [batches, batchId])
  const payload = () => ({
    pending_receipt_batch_id: Number(batchId), input_quantity: Number(inputQty), loss_quantity: Number(lossQty),
    remaining_quantity: Number(remainingQty), financial_category_id: Number(lossQty) > 0 ? Number(categoryId) : null,
    reason, outputs: outputs.map((item) => ({ product_id: item.product_id, quantity: item.quantity })),
  })

  const previewEvent = async () => {
    setBusy(true); setError(''); setNotice(''); setPreview(null)
    try {
      let current = event
      if (!current) {
        const created = await api.post<{ id: number; version: number }>('/sorting-events', payload())
        current = created; setEvent(created)
      } else {
        const updated = await api.patch<{ id: number; version: number }>(`/sorting-events/${current.id}`, payload(), { headers: { 'If-Match': `"${current.version}"` } })
        current = updated; setEvent(updated)
      }
      setPreview(await api.post<Preview>(`/sorting-events/${current.id}/preview`, {}))
    } catch (err) { setError(err instanceof Error ? err.message : 'Preview failed.') }
    finally { setBusy(false) }
  }

  const postEvent = async () => {
    if (!event) return
    setBusy(true); setError(''); setNotice('')
    try {
      await api.post(`/sorting-events/${event.id}/post`, {}, { headers: { 'If-Match': `"${event.version}"`, 'Idempotency-Key': crypto.randomUUID() } })
      setNotice('Sorting posted. Stock and carrying values are updated.')
      setEvent(null); setPreview(null); setReason(''); setLossQty('0'); setRemainingQty('0'); setOutputs([{ product_id: 0, quantity: 0 }])
      await refresh()
    } catch (err) { setError(isApiError(err) ? err.message : err instanceof Error ? err.message : 'Posting failed.') }
    finally { setBusy(false) }
  }

  if (!allowed) return <main style={{ padding: 28 }}><h1>{t('sorting.title')}</h1><p>Missing capability: inventory.sort</p></main>
  return <main style={{ maxWidth: 1180, margin: '0 auto', padding: '26px 22px 50px' }}>
    <div style={{ marginBottom: 22 }}>
      <h1 style={{ margin: 0, fontSize: 25, color: '#0f172a' }}>{t('sorting.title')}</h1>
      <p style={{ margin: '6px 0 0', color: '#64748b', fontSize: 13 }}>{t('sorting.description')}</p>
    </div>
    {error && <div role="alert" style={{ marginBottom: 14, padding: 12, color: '#991b1b', background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8 }}>{error}</div>}
    {notice && <div role="status" style={{ marginBottom: 14, padding: 12, color: '#166534', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 8 }}>{notice}</div>}
    <section style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, marginBottom: 18 }}>
      <header style={{ padding: 15, borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <strong>{t('sorting.pending')}</strong><Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}><RefreshCw size={14} className="mr-1" /> Refresh</Button>
      </header>
      {loading ? <p style={{ padding: 16, color: '#64748b' }}>Loading…</p> : batches.length === 0 ? <p style={{ padding: 16, color: '#64748b' }}>No unsorted receipt stock is available.</p> :
        <div style={{ overflowX: 'auto' }}><table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}><thead><tr style={{ background: '#f8fafc', textAlign: 'left' }}>
          <th style={{ padding: 10 }}>Batch</th><th style={{ padding: 10 }}>Source line</th><th style={{ padding: 10 }}>Available ({selectedBatch?.base_unit_code_snapshot ?? 'base'})</th><th style={{ padding: 10 }}>Carrying value</th><th style={{ padding: 10 }} />
        </tr></thead><tbody>{batches.map((batch) => <tr key={batch.id} style={{ borderTop: '1px solid #eef2f7' }}>
          <td style={{ padding: 10 }}>#{batch.id}</td><td style={{ padding: 10 }}>Purchase line #{batch.purchase_line_id}</td><td style={{ padding: 10 }}>{batch.available_quantity} {batch.base_unit_code_snapshot}</td><td style={{ padding: 10 }}>{formatIDR(batch.inventory_value)}</td><td style={{ padding: 10 }}><Button variant={String(batch.id) === batchId ? 'default' : 'outline'} size="sm" onClick={() => { setBatchId(String(batch.id)); setInputQty(String(batch.available_quantity)); setEvent(null); setPreview(null) }}>Select</Button></td>
        </tr>)}</tbody></table></div>}
    </section>
    <section style={{ background: 'white', border: '1px solid var(--border)', borderRadius: 10, padding: 18 }}>
      <h2 style={{ margin: '0 0 6px', fontSize: 17 }}>{t('sorting.create')}</h2>
      <p style={{ margin: '0 0 18px', color: '#64748b', fontSize: 12 }}>{t('sorting.exact')} {selectedBatch ? `Source #${selectedBatch.id}; ${selectedBatch.available_quantity} ${selectedBatch.base_unit_code_snapshot} available.` : ''}</p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12 }}>
        <label style={{ fontSize: 12 }}>{t('sorting.input')}<input type="number" min="0.0001" step="0.0001" value={inputQty} onChange={(e) => setInputQty(e.target.value)} style={inputStyle} /></label>
        <label style={{ fontSize: 12 }}>{t('sorting.loss')}<input type="number" min="0" step="0.0001" value={lossQty} onChange={(e) => setLossQty(e.target.value)} style={inputStyle} /></label>
        <label style={{ fontSize: 12 }}>{t('sorting.remaining')}<input type="number" min="0" step="0.0001" value={remainingQty} onChange={(e) => setRemainingQty(e.target.value)} style={inputStyle} /></label>
        {Number(lossQty) > 0 && <label style={{ fontSize: 12 }}>{t('sorting.category')}<select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} style={inputStyle}><option value="">Choose expense category…</option>{categories.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>}
      </div>
      <div style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}><strong style={{ fontSize: 13 }}>{t('sorting.output')}</strong><Button variant="outline" size="sm" onClick={() => setOutputs((rows) => [...rows, { product_id: 0, quantity: 0 }])}><Plus size={14} className="mr-1" /> Add output</Button></div>
        {outputs.map((output, index) => <div key={index} style={{ display: 'grid', gridTemplateColumns: 'minmax(200px,1fr) 180px 36px', gap: 8, marginBottom: 8 }}>
          <select value={output.product_id || ''} onChange={(e) => setOutputs((rows) => rows.map((row, i) => i === index ? { ...row, product_id: Number(e.target.value) } : row))} style={inputStyle}><option value="">Choose classified product…</option>{products.map((product) => <option key={product.id} value={product.id}>{product.name} ({product.unit_code})</option>)}</select>
          <input aria-label={`${t('sorting.quantity')} ${index + 1}`} type="number" min="0.0001" step="0.0001" value={output.quantity || ''} onChange={(e) => setOutputs((rows) => rows.map((row, i) => i === index ? { ...row, quantity: Number(e.target.value) } : row))} style={inputStyle} />
          <button aria-label="Remove output" disabled={outputs.length <= 1} onClick={() => setOutputs((rows) => rows.filter((_, i) => i !== index))} style={{ border: 0, background: 'transparent', color: '#64748b' }}><Trash2 size={16} /></button>
        </div>)}
      </div>
      <label style={{ display: 'block', marginTop: 12, fontSize: 12 }}>{t('sorting.reason')}<textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} rows={2} style={{ ...inputStyle, resize: 'vertical', marginTop: 4 }} /></label>
      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}><Button variant="outline" onClick={() => void previewEvent()} disabled={busy || !batchId || !reason.trim()}>{t('sorting.preview')}</Button>{event && <Button onClick={() => void postEvent()} disabled={busy || !preview?.reconciles}>{t('sorting.post')}</Button>}</div>
      {preview && <div style={{ marginTop: 16, padding: 14, background: preview.reconciles ? '#f0fdf4' : '#fef2f2', border: `1px solid ${preview.reconciles ? '#bbf7d0' : '#fecaca'}`, borderRadius: 9 }}>
        <strong>{preview.reconciles ? 'Quantity and value reconcile' : 'Reconciliation failed'}</strong><p style={{ margin: '5px 0 10px', fontSize: 12 }}>Input {preview.input_quantity ?? Number(inputQty)} · source value {formatIDR(preview.source_value)} · loss {formatIDR(preview.loss_value)} · remaining {formatIDR(preview.remaining_value)}</p>
        {preview.outputs.map((row, index) => <div key={index} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0' }}><span>{row.product_name} · {row.quantity}</span><strong>{formatIDR(row.value_snapshot)}</strong></div>)}
      </div>}
    </section>
  </main>
}
