'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ChevronLeft } from 'lucide-react'
import { api } from '@/lib/api-client'
import type { Contact } from '@/lib/contact-types'
import type { Purchase } from '@/lib/purchase-types'
import type { SupplierRepayment } from '@/lib/supplier-types'
import { formatIDR } from '@/lib/format'
import { useSession } from '@/lib/session'

// Lifecycle statuses that contribute to supplier-level AP.
// Authoritative rule: DB-Design V1.0 §8.4 line 1169:
//   WHERE purchases.lifecycle_status IN ('posted','completed','partially_returned')
// Drafts are excluded (no goods received yet). Cancelled/returned contribute
// AP=0 numerically, but are not in the authorized set.
const AP_LIFECYCLE_STATUSES: ReadonlySet<Purchase['lifecycle_status']> =
  new Set(['posted', 'completed', 'partially_returned'])

function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('id-ID', {
    year: 'numeric', month: 'short', day: '2-digit',
  })
}

export default function SupplierDetailPage() {
  const params = useParams<{ id: string }>()
  const rawId = params?.id ?? ''
  const supplierId = Number(rawId)
  const user = useSession()
  const canView = user.capabilities.includes('contact.view')

  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [contact, setContact] = useState<Contact | null>(null)
  const [purchases, setPurchases] = useState<Purchase[]>([])
  const [purchasesTotal, setPurchasesTotal] = useState<number>(0)
  const [purchasesLoading, setPurchasesLoading] = useState(false)
  const [repayments, setRepayments] = useState<SupplierRepayment[]>([])
  const [repaymentsTotal, setRepaymentsTotal] = useState<number>(0)
  const [repaymentsLoading, setRepaymentsLoading] = useState(false)

  // ------------------------------------------------------------------
  // Contact detail — GET /contacts/{id}
  // ------------------------------------------------------------------
  const fetchContact = useCallback(async () => {
    if (!canView) return
    setLoading(true)
    setError(null)
    try {
      const row = await api.get<Contact>(`/contacts/${supplierId}`)
      setContact(row)
    } catch (e: any) {
      if (e?.message?.includes('404') || e?.message?.includes('Not Found')) {
        setNotFound(true)
      } else {
        setError(e?.message ?? 'Failed to load supplier.')
      }
    } finally {
      setLoading(false)
    }
  }, [supplierId, canView])

  // ------------------------------------------------------------------
  // Purchases — GET /purchases?filter[supplier_id]={id}
  // Fetches ALL pages (per_page=500) to avoid pagination undercount.
  // Then filters client-side for lifecycle_status per DB-Design §8.4.
  // The API only supports exact filter[lifecycle_status] matching, so
  // fetching all and filtering client-side is the smallest correct approach.
  // ------------------------------------------------------------------
  const fetchPurchases = useCallback(async () => {
    if (!canView) return
    setPurchasesLoading(true)
    try {
      let all: Purchase[] = []
      let page = 1
      const perPage = 500
      let total = 0
      while (true) {
        const res = await api.get<{
          data: Purchase[]
          pagination: { total: number }
        }>('/purchases', {
          params: {
            'filter[supplier_id]': String(supplierId),
            page: String(page),
            per_page: String(perPage),
          },
        })
        all = all.concat(res?.data ?? [])
        total = res?.pagination?.total ?? 0
        if (all.length >= total || (res?.data?.length ?? 0) < perPage) break
        page++
      }
      setPurchases(all)
      setPurchasesTotal(all.length)
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load purchases.')
    } finally {
      setPurchasesLoading(false)
    }
  }, [supplierId, canView])

  // ------------------------------------------------------------------
  // Repayments — GET /supplier-repayments?filter[supplier_id]={id}
  // Fetches ALL pages (per_page=500) to ensure complete repayment history.
  // ------------------------------------------------------------------
  const fetchRepayments = useCallback(async () => {
    if (!canView) return
    setRepaymentsLoading(true)
    try {
      let all: SupplierRepayment[] = []
      let page = 1
      const perPage = 500
      let total = 0
      while (true) {
        const res = await api.get<{
          data: SupplierRepayment[]
          pagination: { total: number }
        }>('/supplier-repayments', {
          params: {
            'filter[supplier_id]': String(supplierId),
            page: String(page),
            per_page: String(perPage),
          },
        })
        all = all.concat(res?.data ?? [])
        total = res?.pagination?.total ?? 0
        if (all.length >= total || (res?.data?.length ?? 0) < perPage) break
        page++
      }
      setRepayments(all)
      setRepaymentsTotal(all.length)
    } catch (e: any) {
      setError(e?.message ?? 'Failed to load repayments.')
    } finally {
      setRepaymentsLoading(false)
    }
  }, [supplierId, canView])

  useEffect(() => {
    if (Number.isNaN(supplierId) || supplierId < 1) {
      setNotFound(true)
      return
    }
    fetchContact()
    fetchPurchases()
    fetchRepayments()
  }, [fetchContact, fetchPurchases, fetchRepayments, supplierId])

  // Supplier-level AP = Σ per-purchase ap for qualifying lifecycle statuses.
  // Per-purchase `ap` already = max(0, total − Σ payments − Σ returns).
  // Cancelled/returned purchases have ap = 0 — excluded by lifecycle filter.
  // Drafts are excluded per DB-Design §8.4 line 1169.
  const totalAp = useMemo(() => {
    return purchases.reduce((sum, p) => {
      if (!AP_LIFECYCLE_STATUSES.has(p.lifecycle_status)) return sum
      return sum + (p.ap ?? 0)
    }, 0)
  }, [purchases])

  // Supplier-level SREC (Account 1300) = Σ repayments.amount − Σ repayments.received_amount.
  // Per DB-Design V1.0 §8.4 line 1170 — NOT tied to purchase lifecycle_status.
  // Computed from supplier_repayments table directly.
  const totalSrec = useMemo(() => {
    return repayments.reduce(
      (sum, r) => sum + (r.amount ?? 0) - (r.received_amount ?? 0),
      0,
    )
  }, [repayments])

  // Total repayments received from supplier (cash received).
  const totalRepayments = useMemo(() => {
    return repayments.reduce((sum, r) => sum + (r.received_amount ?? 0), 0)
  }, [repayments])

  if (notFound) {
    return (
      <div style={{ padding: 24 }}>
        <p style={{ color: '#94a3b8' }}>Supplier not found.</p>
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ padding: 24 }}>
        <p style={{ color: '#94a3b8' }}>Loading supplier…</p>
      </div>
    )
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      {/* Back */}
      <Link
        href="/suppliers"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          marginBottom: 20, fontSize: 13, fontWeight: 600,
          color: '#334155', textDecoration: 'none',
        }}
      >
        <ChevronLeft size={16} />
        Back to suppliers
      </Link>

      {/* Error toast */}
      {error && (
        <div
          style={{
            marginBottom: 16, padding: '10px 14px', borderRadius: 6,
            background: '#fef2f2', color: '#dc2626', fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {/* Contact header */}
      {contact && (
        <div style={{ marginBottom: 24 }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>
            {contact.name}
          </h1>
          <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 13, color: '#64748b' }}>
            <span>#{contact.id}</span>
            <span>{contact.type}</span>
            <span>{contact.is_active ? 'Active' : 'Inactive'}</span>
          </div>
        </div>
      )}

      {/* Financial summary */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: 16,
          marginBottom: 28,
        }}
      >
        <SummaryCard
          title="Accounts Payable"
          value={formatIDR(totalAp)}
          hint="Outstanding payable across posted purchases"
        />
        <SummaryCard
          title="Supplier Receivable"
          value={formatIDR(totalSrec)}
          hint="Refunds owed (from repayments)"
        />
        <SummaryCard
          title="Repayments Received"
          value={formatIDR(totalRepayments)}
          hint={`${repaymentsTotal} repayment${repaymentsTotal === 1 ? '' : 's'}`}
        />
      </div>

      {/* Purchases table */}
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 12px' }}>
        Purchases ({purchasesTotal})
      </h2>
      {purchasesLoading ? (
        <p style={{ color: '#94a3b8', fontSize: 13 }}>Loading purchases…</p>
      ) : purchases.length === 0 ? (
        <p style={{ color: '#94a3b8', fontSize: 13 }}>No purchases for this supplier.</p>
      ) : (
        <div style={{ overflowX: 'auto', marginBottom: 32 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>#</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Date</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Status</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Total</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>AP</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>SREC</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>View</th>
              </tr>
            </thead>
            <tbody>
              {purchases.filter(p => AP_LIFECYCLE_STATUSES.has(p.lifecycle_status)).map((p) => (
                <tr key={p.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 12px' }}>#{p.id}</td>
                  <td style={{ padding: '8px 12px' }}>{fmtDateShort(p.purchase_date)}</td>
                  <td style={{ padding: '8px 12px' }}>{p.lifecycle_status}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatIDR(p.total_amount)}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatIDR(p.ap)}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatIDR(p.supplier_receivable)}</td>
                  <td style={{ padding: '8px 12px' }}>
                    <Link
                      href={`/purchases/${p.id}`}
                      style={{ color: 'var(--accent)', textDecoration: 'none', fontSize: 12 }}
                    >
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Repayments table */}
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 12px' }}>
        Supplier Repayments ({repaymentsTotal})
      </h2>
      {repaymentsLoading ? (
        <p style={{ color: '#94a3b8', fontSize: 13 }}>Loading repayments…</p>
      ) : repayments.length === 0 ? (
        <p style={{ color: '#94a3b8', fontSize: 13 }}>No repayments recorded for this supplier.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Date</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Amount</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Received</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Reason</th>
              </tr>
            </thead>
            <tbody>
              {repayments.map((r) => (
                <tr key={r.id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '8px 12px' }}>{fmtDateShort(r.repayment_date)}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatIDR(r.amount)}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right' }}>{formatIDR(r.received_amount)}</td>
                  <td style={{ padding: '8px 12px', color: '#64748b' }}>{r.reason ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function SummaryCard({ title, value, hint }: {
  title: string
  value: string
  hint: string
}) {
  return (
    <div
      style={{
        padding: 16,
        borderRadius: 8,
        border: '1px solid var(--border)',
        background: 'var(--card)',
      }}
    >
      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: '#0f172a' }}>{value}</div>
      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>{hint}</div>
    </div>
  )
}
