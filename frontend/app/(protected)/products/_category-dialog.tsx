'use client'

import { useState, useEffect } from 'react'
import { X, Plus, Pencil, Archive, RotateCcw, Trash2 } from 'lucide-react'
import { api } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import type { Category } from '@/lib/product-types'

export default function CategoryManagementDialog({
  open,
  onClose,
  onNotice,
}: {
  open: boolean
  onClose: () => void
  onNotice: (msg: string) => void
}) {
  const [categories, setCategories] = useState<Category[]>([])
  const [loading, setLoading] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')

  const fetchCategories = async () => {
    setLoading(true)
    try {
      const res = await api.get<{ data: Category[] }>('/categories?per_page=500')
      setCategories(res.data)
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Failed to load categories')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) fetchCategories()
  }, [open])

  const handleAdd = async () => {
    if (!newName.trim()) return
    try {
      await api.post('/categories', { name: newName.trim(), is_active: true }, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
      onNotice(`Category "${newName.trim()}" added`)
      setNewName('')
      setAdding(false)
      fetchCategories()
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Add failed')
    }
  }

  const handleRename = async (id: number) => {
    if (!editName.trim()) return
    try {
      const cat = categories.find(c => c.id === id)
      await api.patch(`/categories/${id}`, { name: editName.trim() }, { headers: { 'If-Match': `"${cat?.updated_at}"` } })
      onNotice('Category renamed')
      setEditingId(null)
      setEditName('')
      fetchCategories()
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Rename failed')
    }
  }

  const handleToggleActive = async (id: number) => {
    const cat = categories.find(c => c.id === id)
    try {
      if (cat?.is_active) {
        await api.post(`/categories/${id}/deactivate`, { reason: 'Deactivated from UI' }, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
        onNotice('Category deactivated')
      } else {
        await api.patch(`/categories/${id}`, { is_active: true }, { headers: { 'If-Match': `"${cat?.updated_at}"` } })
        onNotice('Category reactivated')
      }
      fetchCategories()
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Toggle failed')
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this category? It must not be referenced by any product.')) return
    try {
      await api.delete(`/categories/${id}`, { headers: { 'Idempotency-Key': crypto.randomUUID() } })
      onNotice('Category deleted')
      fetchCategories()
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Delete failed')
    }
  }

  if (!open) return null

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(15,23,42,.35)', padding: 16 }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        role="dialog"
        aria-label="Manage categories"
        style={{ width: '100%', maxWidth: 520, background: 'white', borderRadius: 14, border: '1px solid var(--border)', boxShadow: '0 20px 48px rgba(0,0,0,.12)', display: 'flex', flexDirection: 'column', maxHeight: '90vh' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <TagIcon />
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--foreground)' }}>Categories</div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>Manage catalog groupings</div>
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, color: 'var(--muted)' }}>
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 20, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {adding && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Category name"
                style={{ flex: 1, height: 36, borderRadius: 6, border: '1px solid var(--border)', padding: '0 10px', fontSize: 13 }}
              />
              <Button size="sm" onClick={handleAdd}>Add</Button>
              <Button variant="outline" size="sm" onClick={() => { setAdding(false); setNewName('') }}>Cancel</Button>
            </div>
          )}

          {loading ? (
            <div style={{ padding: 20, textAlign: 'center', color: 'var(--muted)' }}>Loading…</div>
          ) : categories.length === 0 && !adding ? (
            <div style={{ padding: 30, textAlign: 'center', color: 'var(--muted)' }}>
              <p>No categories yet.</p>
              <p style={{ fontSize: 12 }}>Add one to group products.</p>
            </div>
          ) : (
            categories.map((cat) => (
              <div key={cat.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
                {editingId === cat.id ? (
                  <>
                    <input
                      type="text"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      style={{ flex: 1, height: 32, borderRadius: 6, border: '1px solid var(--border)', padding: '0 8px', fontSize: 13 }}
                    />
                    <Button size="sm" onClick={() => handleRename(cat.id)}>Save</Button>
                    <Button variant="outline" size="sm" onClick={() => { setEditingId(null); setEditName('') }}>Cancel</Button>
                  </>
                ) : (
                  <>
                    <span style={{ flex: 1, fontWeight: 500 }}>{cat.name}</span>
                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: cat.is_active ? '#ecfdf5' : '#f1f5f9', color: cat.is_active ? '#059669' : '#64748b' }}>
                      {cat.is_active ? 'Active' : 'Inactive'}
                    </span>
                    <button onClick={() => { setEditingId(cat.id); setEditName(cat.name) }} style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, color: 'var(--muted)' }} title="Rename"><Pencil size={14} /></button>
                    <button onClick={() => handleToggleActive(cat.id)} style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, color: 'var(--muted)' }} title={cat.is_active ? 'Deactivate' : 'Reactivate'}>
                      {cat.is_active ? <Archive size={14} /> : <RotateCcw size={14} />}
                    </button>
                    <button onClick={() => handleDelete(cat.id)} style={{ background: 'none', border: 0, cursor: 'pointer', padding: 4, color: '#dc2626' }} title="Delete"><Trash2 size={14} /></button>
                  </>
                )}
              </div>
            ))
          )}

          {!adding && (
            <Button variant="outline" onClick={() => setAdding(true)} style={{ alignSelf: 'flex-start' }}>
              <Plus size={14} /> Add category
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

function TagIcon() {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1769e0" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2H2v10l9.29 9.29a1 1 0 0 0 1.42 0l8.58-8.58a1 1 0 0 0 0-1.42L12 2Z" /><path d="M7 7h.01" /></svg>
}