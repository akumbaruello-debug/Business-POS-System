import { ClipboardList } from 'lucide-react'

export default function PurchasesSkeleton() {
  return (
    <div className="content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">—</div>
          <h1>—</h1>
          <p>—</p>
        </div>
        <div className="heading-actions" />
      </div>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 10,
          alignItems: 'flex-end',
          marginBottom: 16,
          background: 'white',
          border: '1px solid var(--border)',
          borderRadius: 10,
          padding: 14,
        }}
      >
        <div style={{ minWidth: 220, flex: '1 1 200px', maxWidth: 360 }}>
          <div style={{ height: 34, borderRadius: 8, background: '#f0f4f9' }} />
        </div>
        <div style={{ width: 140, height: 34, borderRadius: 8, background: '#f0f4f9' }} />
        <div style={{ width: 140, height: 34, borderRadius: 8, background: '#f0f4f9' }} />
        <div style={{ width: 140, height: 34, borderRadius: 8, background: '#f0f4f9' }} />
        <div style={{ width: 110, height: 34, borderRadius: 8, background: '#f0f4f9' }} />
        <div style={{ width: 110, height: 34, borderRadius: 8, background: '#f0f4f9' }} />
      </div>
      <section
        style={{
          background: 'white',
          border: '1px solid var(--border)',
          borderRadius: 10,
          overflow: 'hidden',
        }}
      >
        <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} style={{ height: 46, borderRadius: 8, background: '#f0f4f9' }} />
          ))}
        </div>
      </section>
    </div>
  )
}
