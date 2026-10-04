'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Banknote, RefreshCw, Search } from 'lucide-react'
import { RouteGuard } from '@/components/route-guard'
import { api, isApiError } from '@/lib/api-client'
import { fetchPaymentMethods } from '@/lib/payment-methods-service'
import type { PaymentMethod, Sale, SaleListResponse, SalePaymentInput } from '@/lib/sale-types'
import { formatDate, formatIDR } from '@/lib/format'

function RecordPaymentPage() {
  const [sales, setSales] = useState<Sale[]>([])
  const [methods, setMethods] = useState<PaymentMethod[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const [methodId, setMethodId] = useState('')
  const [amount, setAmount] = useState('')
  const [tendered, setTendered] = useState('')
  const [reference, setReference] = useState('')
  const loadVersion = useRef(0)

  const load = useCallback(async () => {
    const version = ++loadVersion.current
    setLoading(true)
    setError('')
    try {
      const [salesResponse, paymentMethods] = await Promise.all([
        api.get<SaleListResponse>('/sales', { params: { lifecycle_status: 'posted', per_page: '200' } }),
        fetchPaymentMethods(),
      ])
      if (version !== loadVersion.current) return
      const outstanding = (salesResponse.data ?? []).filter((item) => item.payment_state !== 'paid' && Number(item.outstanding) > 0)
      setSales(outstanding.sort((a, b) => b.id - a.id))
      setMethods(paymentMethods)
      setSelected((current) => current && outstanding.some((s) => s.id === current) ? current : outstanding[0]?.id ?? null)
      setMethodId((current) => current || String(paymentMethods[0]?.id ?? ''))
    } catch (e) {
      if (version !== loadVersion.current) return
      setError(e instanceof Error ? e.message : 'Could not load outstanding sales.')
    } finally {
      if (version === loadVersion.current) setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const sale = sales.find((item) => item.id === selected) ?? null
  const selectedMethod = methods.find((method) => method.id === Number(methodId))
  const visibleSales = useMemo(() => sales.filter((item) => {
    const q = query.trim().toLowerCase()
    return !q || `${item.reference_no ?? ''} ${item.customer?.name ?? ''} ${item.id}`.toLowerCase().includes(q)
  }), [sales, query])

  useEffect(() => {
    if (!sale) {
      setAmount('')
      return
    }
    setAmount(String(Math.max(0, Number(sale.outstanding) || 0)))
    setTendered('')
    setReference('')
    setNotice('')
  }, [sale?.id, sale?.outstanding])

  const submit = async () => {
    if (!sale || !selectedMethod || saving) return
    const payAmount = Number(amount)
    const tenderAmount = tendered.trim() ? Number(tendered) : undefined
    if (!Number.isFinite(payAmount) || payAmount <= 0 || payAmount > sale.outstanding) {
      setNotice('Enter an amount up to the outstanding balance.')
      return
    }
    if (selectedMethod.is_cash && (tenderAmount === undefined || !Number.isFinite(tenderAmount) || tenderAmount < payAmount)) {
      setNotice('Enter the cash received. It must cover this payment.')
      return
    }
    setSaving(true)
    setNotice('')
    try {
      const payload: SalePaymentInput = {
        payment_method_id: selectedMethod.id,
        amount: payAmount,
        ...(selectedMethod.is_cash && tenderAmount !== undefined ? { tendered_amount: tenderAmount } : {}),
        reference: reference.trim() || null,
      }
      await api.headers.post(`/sales/${sale.id}/payments`, payload, { idempotencyKey: true, ifMatch: sale.etag })
      setNotice('Payment recorded.')
      await load()
    } catch (e) {
      setNotice(isApiError(e) ? e.message : 'Could not record this payment.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="content">
      <div className="page-heading">
        <div><div className="eyebrow">SALES / PAYMENTS</div><h1>Record payment</h1><p>Apply a payment to an outstanding sale and track the remaining balance.</p></div>
        <button className="button button-secondary" onClick={load} disabled={loading}><RefreshCw size={15} /> Refresh</button>
      </div>
      {(error || notice) && <div className={`notice ${error ? 'error' : 'success'}`}>{error || notice}</div>}
      <div className="payment-workspace">
        <section className="panel payment-sales-panel">
          <div className="panel-header"><div><h2>Outstanding sales</h2><p>{sales.length} sale{sales.length === 1 ? '' : 's'} waiting for payment</p></div></div>
          <label className="cash-search payment-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search customer or sale" /></label>
          <div className="payment-sale-list">
            {loading ? <div className="empty">Loading outstanding sales…</div> : visibleSales.length === 0 ? <div className="empty">No outstanding sales found.</div> : visibleSales.map((item) => (
              <button className={`payment-sale-option ${item.id === selected ? 'selected' : ''}`} key={item.id} onClick={() => setSelected(item.id)}>
                <span className="payment-sale-name">{item.customer?.name || 'Walk-in customer'}<small>{item.reference_no || `Sale #${item.id}`} · {formatDate(item.sale_date)}</small></span>
                <span className="payment-sale-balance">{formatIDR(item.outstanding)}<small>{item.payment_state}</small></span>
              </button>
            ))}
          </div>
        </section>
        <section className="panel payment-form-panel">
          <div className="panel-header"><div><h2>Payment details</h2><p>{sale ? `Sale ${sale.reference_no || `#${sale.id}`}` : 'Choose a sale to continue'}</p></div></div>
          {sale ? (
            <div className="payment-form">
              <div className="payment-balance-card"><span>Outstanding balance</span><strong>{formatIDR(sale.outstanding)}</strong><small>Sale total {formatIDR(Number(sale.total_amount))}</small></div>
              <label>Payment method<select value={methodId} onChange={(e) => setMethodId(e.target.value)}><option value="">Select a payment method</option>{methods.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
              <label>Amount<input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ''))} /></label>
              {selectedMethod?.is_cash && <label>Cash received<input inputMode="decimal" value={tendered} onChange={(e) => setTendered(e.target.value.replace(/[^\d.]/g, ''))} placeholder="Amount received" /></label>}
              <label>Reference <span className="optional-label">Optional</span><input value={reference} maxLength={100} onChange={(e) => setReference(e.target.value)} placeholder="Transfer or receipt reference" /></label>
              <button className="button button-primary payment-submit" onClick={submit} disabled={saving || !methodId || !amount}><Banknote size={16} />{saving ? 'Recording…' : 'Record payment'}</button>
            </div>
          ) : <div className="empty-workspace"><strong>No sale selected</strong><p>Select an outstanding sale to record its payment.</p></div>}
        </section>
      </div>
    </div>
  )
}

export default function PaymentsRoute() {
  return <RouteGuard required="sale.view"><RecordPaymentPage /></RouteGuard>
}
