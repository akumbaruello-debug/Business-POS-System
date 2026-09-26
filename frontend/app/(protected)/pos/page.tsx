'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  Banknote,
  Minus,
  Plus,
  Receipt,
  Search,
  ShoppingCart,
  Trash2,
  User,
  X,
} from 'lucide-react'
import { api, isApiError } from '@/lib/api-client'
import type { Contact, ContactListResponse } from '@/lib/contact-types'
import type { PagedResponse, Product } from '@/lib/product-types'
import type {
  Pagination,
  PaymentMethod,
  Sale,
  SalePayment,
  SalePaymentInput,
} from '@/lib/sale-types'
import { formatIDR } from '@/lib/format'
import { num, sanitizeEtag } from '@/lib/sale-ui'
import { Button } from '@/components/ui/button'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'

// -----------------------------------------------------------------------------
// Backend contract (backend/app/api/v1/sales.py):
//
//   POST /sales {customer_id?, lines:[{product_id,quantity,unit_price?,
///            discount_amount?}]}                    (sale.create)
//     → 201 Sale (Idempotency-Key required)
//   POST /sales/{id}/post {}                         (sale.post)
//     → 200 Sale; stock decreases only on post (PRD §12 S3)
//   POST /sales/{id}/payments {payment_method_id, amount,
//     tendered_amount?, reference?}                   (sale.create)
//     → 201 SalePayment {change_amount} (server-computed)
//   GET  /sales/{id}                                  (sale.view)
//     → enriched Sale (total_amount, outstanding, payment_state —
//        server-computed, never recomputed here)
//
// Over-tender (PRD §12): tendered ≥ amount on is_cash methods only;
// revenue = allocated amount, change = tendered − amount (server).
// Split tender = sequential POSTs until outstanding reaches 0.
// -----------------------------------------------------------------------------

interface CartLine {
  product: Product
  quantity: number
  unitPrice: number
  discount: number
}

type Phase = 'build' | 'tender' | 'done'

export default function PosPage() {
  const user = useSession()
  const { t } = useLanguage()
  const canCreate = user.capabilities.includes('sale.create')
  const canPriceOverride = user.capabilities.includes('sale.price_override')
  const canDiscount = user.capabilities.includes('sale.discount')

  const [products, setProducts] = useState<Product[]>([])
  const [customers, setCustomers] = useState<Contact[]>([])
  const [payMethods, setPayMethods] = useState<PaymentMethod[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [search, setSearch] = useState('')
  const [cart, setCart] = useState<CartLine[]>([])
  const [customerId, setCustomerId] = useState('')
  const [saleDate, setSaleDate] = useState('')
  const [notes, setNotes] = useState('')
  const [headerDiscount, setHeaderDiscount] = useState('')

  const [phase, setPhase] = useState<Phase>('build')
  const [sale, setSale] = useState<Sale | null>(null)
  const [etag, setEtag] = useState('')
  const [recorded, setRecorded] = useState<SalePayment[]>([])

  const [payMethod, setPayMethod] = useState<number | ''>('')
  const [payAmount, setPayAmount] = useState('')
  const [payTendered, setPayTendered] = useState('')
  const [payReference, setPayReference] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [prods, contacts, methods] = await Promise.all([
        api.get<PagedResponse<Product>>('/products', {
          params: { per_page: 500, sort: 'name' },
        }),
        api.get<ContactListResponse>('/contacts', {
          params: { 'filter[type]': 'customer', per_page: 500, sort: 'name' },
        }),
        api.get<{ data: PaymentMethod[]; pagination: Pagination }>('/payment-methods', {
          params: { per_page: 200 },
        }),
      ])
      setProducts((prods.data ?? []).filter((p) => p.is_sellable && p.is_active))
      setCustomers(contacts.data ?? [])
      setPayMethods((methods.data ?? []).filter((m) => m.is_active !== false))
    } catch (e) {
      setError(isApiError(e) ? e.message : t('pos.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    if (canCreate) void load()
  }, [canCreate, load])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return products
    return products.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.code ?? '').toLowerCase().includes(q),
    )
  }, [products, search])

  const addToCart = (product: Product) => {
    setCart((prev) => {
      const found = prev.find((l) => l.product.id === product.id)
      const onHand = num(product.on_hand_quantity)
      const current = found ? found.quantity : 0
      if (!product.allow_negative_stock && current + 1 > onHand) {
        setNotice(t('pos.onlyAvailable').replace('{n}', String(onHand)))
        return prev
      }
      setNotice(null)
      if (found) {
        return prev.map((l) =>
          l.product.id === product.id ? { ...l, quantity: l.quantity + 1 } : l,
        )
      }
      return [
        ...prev,
        {
          product,
          quantity: 1,
          unitPrice: num(product.selling_price),
          discount: 0,
        },
      ]
    })
  }

  const setQty = (id: number, qty: number) => {
    setCart((prev) =>
      prev
        .map((l) => {
          if (l.product.id !== id) return l
          const onHand = num(l.product.on_hand_quantity)
          const next = Math.max(0, Math.floor(qty))
          if (!l.product.allow_negative_stock && next > onHand) {
            setNotice(t('pos.onlyAvailable').replace('{n}', String(onHand)))
            return { ...l, quantity: onHand }
          }
          return { ...l, quantity: next }
        })
        .filter((l) => l.quantity > 0),
    )
  }

  /** Client-side estimate only — the server total is authoritative. */
  const linesEstimate = useMemo(
    () =>
      cart.reduce(
        (sum, l) => sum + l.quantity * num(l.unitPrice) - num(l.discount),
        0,
      ),
    [cart],
  )

  // Header discount (order-level, V1 optional, gated by sale.discount like
  // line discounts). Clamped to [0, lines] for the estimate; the server is
  // authoritative (discount_amount ≤ total).
  const headerDiscountNum = useMemo(() => {
    const v = Number(headerDiscount)
    if (!Number.isFinite(v) || v <= 0) return 0
    return Math.min(v, Math.max(0, linesEstimate))
  }, [headerDiscount, linesEstimate])

  const estimate = Math.max(0, linesEstimate - headerDiscountNum)

  const resetAll = () => {
    setCart([])
    setCustomerId('')
    setSaleDate('')
    setNotes('')
    setHeaderDiscount('')
    setPhase('build')
    setSale(null)
    setEtag('')
    setRecorded([])
    setPayMethod('')
    setPayAmount('')
    setPayTendered('')
    setPayReference('')
    setNotice(null)
    setStranded(null)
    createKeyRef.current = null
    postKeyRef.current = null
  }

  // Idempotency keys are stable across retries of the same logical checkout
  // (fresh key per new sale). Retrying create/post with the same key replays
  // instead of duplicating, so a failed post never orphans silently.
  const createKeyRef = useRef<string | null>(null)
  const postKeyRef = useRef<string | null>(null)
  // Draft created server-side whose post did not complete: explicit recovery.
  const [stranded, setStranded] = useState<{ id: number } | null>(null)
  const [confirmDeleteDraft, setConfirmDeleteDraft] = useState(false)

  const refreshSale = useCallback(async (id: number) => {
    const res = await api.headers.get<Sale>(`/sales/${id}`)
    setSale(res.data)
    setEtag(sanitizeEtag(res.headers.get('etag')))
    return res.data
  }, [])

  const handleCheckout = async () => {
    if (cart.length === 0 || busy) return
    setBusy(true)
    setNotice(null)
    setStranded(null)
    setConfirmDeleteDraft(false)
    try {
      if (!createKeyRef.current) createKeyRef.current = crypto.randomUUID()
      if (!postKeyRef.current) postKeyRef.current = crypto.randomUUID()
      const created = await api.headers.post<Sale>(
        '/sales',
        {
          customer_id: customerId ? Number(customerId) : null,
          ...(saleDate ? { sale_date: new Date(`${saleDate}T00:00:00`).toISOString() } : {}),
          ...(headerDiscountNum > 0 ? { discount_amount: headerDiscountNum } : {}),
          ...(notes.trim() ? { notes: notes.trim() } : {}),
          lines: cart.map((l) => ({
            product_id: l.product.id,
            quantity: l.quantity,
            unit_price: num(l.unitPrice),
            discount_amount: num(l.discount),
          })),
        },
        { idempotencyKey: createKeyRef.current },
      )
      const tag = sanitizeEtag(created.headers.get('etag'))
      const id = created.data.id
      let posted: Sale
      try {
        const postRes = await api.headers.post<Sale>(
          `/sales/${id}/post`,
          {},
          { idempotencyKey: postKeyRef.current, ifMatch: tag || undefined },
        )
        posted = postRes.data
      } catch (postErr) {
        // Draft exists server-side but is not posted: explicit recovery
        // instead of a stranded draft. Retrying replays via stable keys.
        setSale(created.data)
        setEtag(tag)
        setStranded({ id })
        setNotice(isApiError(postErr) ? postErr.message : t('pos.checkoutFailed'))
        return
      }
      createKeyRef.current = null
      postKeyRef.current = null
      // The post response is the authoritative posted sale; refresh is
      // best-effort enrichment for the tender phase.
      setSale(posted)
      setEtag(tag)
      try {
        await refreshSale(id)
      } catch {
        // non-fatal: posted sale already in hand
      }
      const firstCash = payMethods.find((m) => m.is_cash)
      const firstAny = payMethods[0]
      setPayMethod((firstCash ?? firstAny)?.id ?? '')
      setPayAmount(String(num(posted.outstanding ?? 0)))
      setPayTendered('')
      setPhase('tender')
      setNotice(t('pos.salePosted'))
    } catch (e) {
      setNotice(isApiError(e) ? e.message : t('pos.checkoutFailed'))
    } finally {
      setBusy(false)
    }
  }

  const handleRetryPost = async () => {
    if (!stranded || busy) return
    setBusy(true)
    setNotice(null)
    try {
      if (!postKeyRef.current) postKeyRef.current = crypto.randomUUID()
      const postRes = await api.headers.post<Sale>(
        `/sales/${stranded.id}/post`,
        {},
        { idempotencyKey: postKeyRef.current, ifMatch: etag || undefined },
      )
      const posted = postRes.data
      createKeyRef.current = null
      postKeyRef.current = null
      setSale(posted)
      setStranded(null)
      const firstCash = payMethods.find((m) => m.is_cash)
      const firstAny = payMethods[0]
      setPayMethod((firstCash ?? firstAny)?.id ?? '')
      setPayAmount(String(num(posted.outstanding ?? 0)))
      setPayTendered('')
      setPhase('tender')
      try {
        await refreshSale(posted.id)
      } catch {
        // non-fatal
      }
      setNotice(t('pos.salePosted'))
    } catch (e) {
      setNotice(isApiError(e) ? e.message : t('pos.checkoutFailed'))
    } finally {
      setBusy(false)
    }
  }

  const handleDeleteDraft = async () => {
    if (!stranded || busy) return
    setBusy(true)
    setNotice(null)
    try {
      await api.headers.post(
        `/sales/${stranded.id}`,
        {},
        { idempotencyKey: crypto.randomUUID() },
      )
      setStranded(null)
      setConfirmDeleteDraft(false)
      createKeyRef.current = null
      postKeyRef.current = null
      setNotice(t('pos.draftDeleted'))
    } catch (e) {
      setNotice(isApiError(e) ? e.message : t('pos.checkoutFailed'))
    } finally {
      setBusy(false)
    }
  }

  const selectedMethod = payMethods.find((m) => m.id === payMethod)
  const cashTender = selectedMethod?.is_cash === true
  const outstanding = num(sale?.outstanding)
  const tenderedNum = payTendered === '' ? null : Number(payTendered)
  const expectedChange =
    cashTender && tenderedNum != null && Number.isFinite(tenderedNum)
      ? Math.max(0, tenderedNum - Number(payAmount || 0))
      : 0

  const handlePayment = async () => {
    if (!sale || payMethod === '' || busy) return
    const amount = Number(payAmount)
    if (!Number.isFinite(amount) || amount <= 0) {
      setNotice(t('pos.invalidAmount'))
      return
    }
    if (cashTender) {
      if (tenderedNum == null || !Number.isFinite(tenderedNum)) {
        setNotice(t('pos.tenderRequired'))
        return
      }
      if (tenderedNum < amount) {
        setNotice(t('pos.tenderBelowTotal'))
        return
      }
    }
    setBusy(true)
    setNotice(null)
    try {
      const body: SalePaymentInput = {
        payment_method_id: Number(payMethod),
        amount,
        ...(cashTender ? { tendered_amount: tenderedNum } : {}),
        reference: payReference.trim() || null,
      }
      const res = await api.headers.post<SalePayment>(
        `/sales/${sale.id}/payments`,
        body,
        { idempotencyKey: true, ifMatch: etag || undefined },
      )
      setRecorded((prev) => [...prev, res.data])
      const fresh = await refreshSale(sale.id)
      setPayAmount(String(num(fresh.outstanding)))
      setPayTendered('')
      setPayReference('')
      if (num(fresh.outstanding) <= 0) {
        setPhase('done')
      } else {
        setNotice(t('pos.partialRecorded'))
      }
    } catch (e) {
      setNotice(isApiError(e) ? e.message : t('pos.paymentFailed'))
    } finally {
      setBusy(false)
    }
  }

  if (!canCreate) {
    return (
      <div className="page">
        <div className="notice error">{t('common.accessRestricted')}</div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>{t('pos.title')}</h1>
          <p className="muted">{t('pos.subtitle')}</p>
        </div>
        {phase !== 'build' && sale && (
          <span className="pill">
            {t('pos.sale')} #{sale.id} · {formatIDR(num(sale.total_amount))}
          </span>
        )}
      </div>

      {error && (
        <div className="notice error">
          {error}{' '}
          <button type="button" className="link" onClick={() => void load()}>
            {t('common.retry')}
          </button>
        </div>
      )}
      {notice && <div className="notice info">{notice}</div>}

      {loading ? (
        <div className="panel">{t('common.loading')}</div>
      ) : (
        <div className="pos-layout">
          {/* Catalog */}
          <section className="panel pos-catalog">
            <div className="panel-header">
              <h2>{t('pos.catalog')}</h2>
              <label className="search-box">
                <Search size={15} />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('pos.searchProducts')}
                />
                {search && (
                  <button type="button" className="icon-btn" onClick={() => setSearch('')}>
                    <X size={14} />
                  </button>
                )}
              </label>
            </div>
            <div className="pos-grid">
              {filtered.map((p) => {
                const onHand = num(p.on_hand_quantity)
                const out = !p.allow_negative_stock && onHand <= 0
                return (
                  <button
                    key={p.id}
                    type="button"
                    className="pos-card"
                    disabled={out || phase !== 'build'}
                    onClick={() => addToCart(p)}
                    title={p.name}
                  >
                    <strong>{p.name}</strong>
                    <small className="muted">{p.code ?? `#${p.id}`}</small>
                    <span className="pos-price">{formatIDR(num(p.selling_price))}</span>
                    <span className={`stock ${out ? 'out' : onHand <= num(p.low_stock_threshold) ? 'low' : ''}`}>
                      {out ? t('pos.outOfStock') : `${t('pos.stock')}: ${formatIDR(onHand)}`}
                    </span>
                  </button>
                )
              })}
              {filtered.length === 0 && <p className="muted">{t('pos.noProducts')}</p>}
            </div>
          </section>

          {/* Cart / tender / receipt */}
          <aside className="panel pos-cart">
            <div className="panel-header">
              <h2>
                <ShoppingCart size={16} /> {t('pos.cart')} ({cart.length})
              </h2>
              {cart.length > 0 && phase === 'build' && (
                <button type="button" className="link danger" onClick={() => setCart([])}>
                  {t('pos.clearCart')}
                </button>
              )}
            </div>

            <div className="pos-body">
            {phase === 'build' && (
              <>
                <label className="field">
                  <span>
                    <User size={13} /> {t('pos.customer')}
                  </span>
                  <select value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
                    <option value="">{t('pos.walkIn')}</option>
                    {customers.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </label>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <label className="field">
                    <span>{t('pos.saleDate')}</span>
                    <input
                      type="date"
                      value={saleDate}
                      onChange={(e) => setSaleDate(e.target.value)}
                    />
                  </label>
                  {canDiscount && (
                    <label className="field">
                      <span>{t('pos.headerDiscount')}</span>
                      <input
                        value={headerDiscount}
                        inputMode="decimal"
                        placeholder="0"
                        onChange={(e) => setHeaderDiscount(e.target.value)}
                      />
                    </label>
                  )}
                </div>
                <label className="field">
                  <span>{t('pos.notes')}</span>
                  <input
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder={t('pos.optional')}
                    maxLength={2000}
                  />
                </label>

                {cart.length === 0 ? (
                  <p className="muted">{t('pos.emptyCart')}</p>
                ) : (
                  <ul className="cart-lines">
                    {cart.map((l) => (
                      <li key={l.product.id} className="cart-line">
                        <div className="cart-line-top">
                          <strong>{l.product.name}</strong>
                          <button
                            type="button"
                            className="icon-btn danger"
                            onClick={() => setQty(l.product.id, 0)}
                            aria-label={t('common.delete')}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                        <div className="cart-line-controls">
                          <button type="button" className="icon-btn" onClick={() => setQty(l.product.id, l.quantity - 1)}>
                            <Minus size={14} />
                          </button>
                          <input
                            value={l.quantity}
                            inputMode="numeric"
                            onChange={(e) => setQty(l.product.id, Number(e.target.value))}
                          />
                          <button type="button" className="icon-btn" onClick={() => setQty(l.product.id, l.quantity + 1)}>
                            <Plus size={14} />
                          </button>
                          <input
                            value={l.unitPrice}
                            inputMode="decimal"
                            disabled={!canPriceOverride}
                            title={t('pos.price')}
                            onChange={(e) =>
                              setCart((prev) =>
                                prev.map((x) =>
                                  x.product.id === l.product.id
                                    ? { ...x, unitPrice: Number(e.target.value) }
                                    : x,
                                ),
                              )
                            }
                          />
                          {canDiscount && (
                            <input
                              value={l.discount}
                              inputMode="decimal"
                              title={t('pos.discount')}
                              placeholder="0"
                              onChange={(e) =>
                                setCart((prev) =>
                                  prev.map((x) =>
                                    x.product.id === l.product.id
                                      ? { ...x, discount: Number(e.target.value) }
                                      : x,
                                  ),
                                )
                              }
                            />
                          )}
                        </div>
                        <small className="muted">
                          {formatIDR(l.quantity * num(l.unitPrice) - num(l.discount))}
                        </small>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="cart-total">
                  <span>{t('pos.estimatedTotal')}</span>
                  <strong>{formatIDR(estimate)}</strong>
                </div>
                {canDiscount && headerDiscountNum > 0 && (
                  <p className="muted small">
                    {t('pos.headerDiscountApplied')}: {formatIDR(headerDiscountNum)}
                  </p>
                )}
                <Button disabled={cart.length === 0 || busy} onClick={() => void handleCheckout()}>
                  {busy ? t('common.creating') : t('pos.checkout')}
                </Button>
                {stranded && (
                  <div className="notice error" style={{ marginTop: 8 }}>
                    <p style={{ margin: '0 0 8px' }}>
                      {t('pos.draftCreated')}: #{stranded.id} — {t('pos.draftNotPosted')}
                    </p>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <Button variant="outline" disabled={busy} onClick={() => void handleRetryPost()}>
                        {t('pos.retryPost')}
                      </Button>
                      <Link className="btn" href={`/sales/${stranded.id}`}>
                        {t('pos.openSale')}
                      </Link>
                      {!confirmDeleteDraft ? (
                        <Button variant="outline" disabled={busy} onClick={() => setConfirmDeleteDraft(true)}>
                          {t('pos.deleteDraft')}
                        </Button>
                      ) : (
                        <Button variant="outline" disabled={busy} onClick={() => void handleDeleteDraft()}>
                          {t('pos.confirmDeleteDraft')}
                        </Button>
                      )}
                    </div>
                  </div>
                )}
              </>
            )}

            {phase !== 'build' && sale && (
              <>
                <div className="tender-summary">
                  <div>
                    <span>{t('pos.total')}</span>
                    <strong>{formatIDR(num(sale.total_amount))}</strong>
                  </div>
                  <div>
                    <span>{t('pos.paid')}</span>
                    <strong>{formatIDR(num(sale.paid_amount))}</strong>
                  </div>
                  <div>
                    <span>{t('pos.outstanding')}</span>
                    <strong>{formatIDR(outstanding)}</strong>
                  </div>
                  <div>
                    <span>{t('pos.paymentState')}</span>
                    <strong>{sale.payment_state}</strong>
                  </div>
                </div>

                {recorded.length > 0 && (
                  <ul className="tender-history">
                    {recorded.map((p) => (
                      <li key={p.id}>
                        <Receipt size={13} />
                        <span>
                          {formatIDR(num(p.amount))}
                          {num(p.change_amount) > 0 &&
                            ` · ${t('pos.change')}: ${formatIDR(num(p.change_amount))}`}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                {phase === 'tender' && (
                  <div className="tender-form">
                    <label className="field">
                      <span>
                        <Banknote size={13} /> {t('pos.paymentMethod')}
                      </span>
                      <select
                        value={payMethod}
                        onChange={(e) => {
                          setPayMethod(e.target.value === '' ? '' : Number(e.target.value))
                          setPayTendered('')
                        }}
                      >
                        <option value="">{t('pos.chooseMethod')}</option>
                        {payMethods.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>{t('pos.amount')}</span>
                      <input
                        value={payAmount}
                        inputMode="decimal"
                        onChange={(e) => setPayAmount(e.target.value)}
                      />
                    </label>
                    {cashTender && (
                      <label className="field">
                        <span>{t('pos.tendered')}</span>
                        <input
                          value={payTendered}
                          inputMode="decimal"
                          placeholder={payAmount}
                          onChange={(e) => setPayTendered(e.target.value)}
                        />
                      </label>
                    )}
                    {cashTender && tenderedNum != null && (
                      <p className="muted">
                        {t('pos.expectedChange')}: {formatIDR(expectedChange)}
                      </p>
                    )}
                    <label className="field">
                      <span>{t('pos.reference')}</span>
                      <input
                        value={payReference}
                        onChange={(e) => setPayReference(e.target.value)}
                        placeholder={t('pos.optional')}
                      />
                    </label>
                    <Button disabled={busy || payMethod === ''} onClick={() => void handlePayment()}>
                      {busy ? t('common.saving') : t('pos.recordPayment')}
                    </Button>
                    <p className="muted small">{t('pos.splitHint')}</p>
                    {sale && (
                      <p className="muted small">
                        <Link className="link" href={`/sales/${sale.id}`}>
                          {t('pos.viewSale')} #{sale.id}
                        </Link>
                      </p>
                    )}
                  </div>
                )}

                {phase === 'done' && (
                  <div className="receipt">
                    <h3>{t('pos.paymentComplete')}</h3>
                    <p className="muted">
                      {t('pos.saleTotal')}: {formatIDR(num(sale.total_amount))}
                    </p>
                    <div className="receipt-actions">
                      <Link className="btn" href={`/sales/${sale.id}`}>
                        {t('pos.viewSale')}
                      </Link>
                      <Button onClick={resetAll}>{t('pos.newSale')}</Button>
                    </div>
                  </div>
                )}
              </>
            )}
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
