'use client'

import { useEffect, useMemo, useState } from 'react'
import { listCapabilities, type Capability } from '@/lib/capabilities-service'

interface CapabilityPickerProps {
  selected: string[]
  onChange: (selected: string[]) => void
  disabled?: boolean
  readOnly?: boolean
}

export function CapabilityPicker({ selected, onChange, disabled, readOnly }: CapabilityPickerProps) {
  const [capabilities, setCapabilities] = useState<Capability[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listCapabilities()
      .then((data) => {
        if (!cancelled) setCapabilities(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load capabilities')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const groups = useMemo(() => {
    const map = new Map<string, Capability[]>()
    capabilities.forEach((cap) => {
      const category = cap.domain || cap.category || 'General'
      if (!map.has(category)) map.set(category, [])
      map.get(category)!.push(cap)
    })
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [capabilities])

  const selectedSet = useMemo(() => new Set(selected), [selected])

  const toggle = (code: string) => {
    if (disabled || readOnly) return
    const next = new Set(selectedSet)
    if (next.has(code)) next.delete(code)
    else next.add(code)
    onChange(Array.from(next))
  }

  if (loading) return <div className="capability-picker loading">Loading capabilities...</div>
  if (error) return <div className="capability-picker error">{error}</div>

  return (
    <div className="capability-picker">
      {groups.map(([category, caps]) => (
        <div key={category} className="capability-group">
          <div className="capability-group-title">{category}</div>
          <div className="capability-list">
            {caps.map((cap) => {
              const checked = selectedSet.has(cap.code)
              return (
                <label key={cap.code} className="capability-item" title={cap.description || cap.code}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(cap.code)}
                    disabled={disabled || readOnly}
                  />
                  <span>
                    <strong>{cap.name}</strong>
                    <small>{cap.code}</small>
                  </span>
                </label>
              )
            })}
          </div>
        </div>
      ))}
    </div>
  )
}
