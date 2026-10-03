'use client'

import { ChevronsUpDown } from 'lucide-react'

/** Shared sort-column toggle. Used by the sales list and returns/refunds tables. */
export function SortButton({
  label,
  active,
  onClick,
  sortDir,
  align,
}: {
  label: string
  active: boolean
  onClick: () => void
  sortDir: 'asc' | 'desc'
  align?: 'right'
}) {
  return (
    <button
      type="button"
      className={`sales-sort${active ? ' active' : ''}`}
      onClick={onClick}
      style={align === 'right' ? { flexDirection: 'row-reverse' } : undefined}
      aria-label={`Sort by ${label}`}
    >
      {label}
      <ChevronsUpDown
        size={12}
        style={{
          opacity: active ? 1 : 0.4,
          transform: active && sortDir === 'asc' ? 'rotate(180deg)' : undefined,
        }}
      />
    </button>
  )
}
