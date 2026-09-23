'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api-client'
import type { Contact } from '@/lib/contact-types'
import type { Product } from '@/lib/product-types'
import { useSession } from '@/lib/session'
import { Button } from '@/components/ui/button'
import { SalesDialog } from '@/components/sales/sale-dialogs'
import { formatIDR } from '@/lib/format'

// -----------------------------------------------------------------------------
// Minimal sale creation dialog. The backend requires at least one line on
// create (SaleCreateRequest.min_length=1), so this form collects the first
// line and then routes to the detail page for full draft editing.
// -----------------------------------------------------------------------------

export function CreateSaleDialog({
  onClose,
  busy,
  setBusy,
}: {
  onClose: () => void
  busy: boolean
  setBusy: (v: boolean) => void
}) {
  const router = useRouter()
  const user = useSession()
  const canPriceOverride = user.capabilities.includes('sale.price_override')
  const canDiscount = user.capabilities.includes('sale.discount')

  const [customers, setCustomers] = useState<Contact[]>([])
  const [products, setProducts] = useState<Product[]>([])
  const [customerId, setCustomerId] = useState('')
  const [productId, setProductId] = useState('')
  const [quantity, setQuantity] = useState('1')
  const [unitPrice, setUnitPrice] = useState('')
  const [discount, setDiscount] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await api.get<{ data: Contact[] }>('/contacts', {
          params: { 'filter[type]': 'customer', per_page: '500', sort: 'name' },
        })
        if (!cancelled) setCustomers(Array.isArray(res.data) ? res.data : [])
      } catch {
        /* best-effort */
      }
      try {
        const res = await api.get<{ data: Product[] }>('/products', {
          params: { per_page: '500', sort: 'name' },
        })
        if (!cancelled) setProducts(Array.isArray(res.data) ? res.data : [])
      } catch {
        /* best-effort */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const selectedProduct = products.find((p) => String(p.id) === productId)
  const effectiveUnitPrice =
    unitPrice.trim() === '' ? selectedProduct?.selling_price ?? 0 : Number(unitPrice)

  const handleSubmit = async () => {
    if (!productId || Number(quantity) <= 0 || effectiveUnitPrice < 0) {
      setError('Select a product and enter a positive quantity.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      const body = {
        customer_id: customerId ? Number(customerId) : null,
        lines: [
          {
            product_id: Number(productId),
            quantity: Number(quantity),
            unit_price: effectiveUnitPrice,
            discount_amount: discount ? Number(discount) : 0,
          },
        ],
      }
      const res = await api.post<{ id: number }>('/sales', body, {
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      })
      onClose()
      router.push(`/sales/${res.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create sale')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SalesDialog title="New sale" onClose={onClose} busy={busy}>
      <p className="sales-dialog-note">
        Creates a draft sale with one line. You can edit lines and totals on the
        detail page after creation.
      </p>
      <div className="sales-dialog-body">
        <label className="sales-field">
          <span className="sales-field-label">Customer (optional)</span>
          <select
            className="sales-select"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            disabled={busy}
          >
            <option value="">Walk-in customer</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label className="sales-field">
          <span className="sales-field-label">Product</span>
          <select
            className="sales-select"
            value={productId}
            onChange={(e) => {
              setProductId(e.target.value)
              const p = products.find((x) => String(x.id) === e.target.value)
              if (p && unitPrice === '') setUnitPrice(String(p.selling_price))
            }}
            disabled={busy}
          >
            <option value="">Select…</option>
            {products
              .filter((p) => p.is_sellable && p.is_active)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <label className="sales-field">
            <span className="sales-field-label">Quantity</span>
            <input
              className="sales-input"
              type="number"
              inputMode="decimal"
              step="any"
              min={0}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              disabled={busy}
            />
          </label>
          <label className="sales-field">
            <span className="sales-field-label">
              Unit price {canPriceOverride ? '' : '(default)'}
            </span>
            <input
              className="sales-input"
              type="number"
              inputMode="decimal"
              step="any"
              min={0}
              value={unitPrice}
              onChange={(e) => setUnitPrice(e.target.value)}
              disabled={busy || !canPriceOverride}
            />
          </label>
        </div>
        {canDiscount && (
          <label className="sales-field">
            <span className="sales-field-label">Line discount (optional)</span>
            <input
              className="sales-input"
              type="number"
              inputMode="decimal"
              step="any"
              min={0}
              value={discount}
              onChange={(e) => setDiscount(e.target.value)}
              disabled={busy}
            />
          </label>
        )}
        {selectedProduct && (
          <p className="sales-muted" style={{ fontSize: 13 }}>
            Default price: {formatIDR(selectedProduct.selling_price)} · Stock:{' '}
            {selectedProduct.on_hand_quantity}
          </p>
        )}
        {error && <span className="sales-field-error">{error}</span>}
      </div>
      <div className="sales-dialog-foot">
        <Button variant="outline" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button onClick={handleSubmit} disabled={busy || !productId}>
          {busy ? 'Creating…' : 'Create draft'}
        </Button>
      </div>
    </SalesDialog>
  )
}
