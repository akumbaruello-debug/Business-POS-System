'use client'

import { createContext, useContext } from 'react'
import type { SessionUser } from './auth'

/**
 * Session context — provides the authenticated user (from `/auth/me`)
 * to every component under the (protected) route group.
 *
 * The provider is mounted in app/(protected)/layout.tsx after the auth
 * check resolves; consumers can rely on `user` being non-null.
 */
export const SessionContext = createContext<{ user: SessionUser } | null>(null)

export function useSession(): SessionUser {
  const ctx = useContext(SessionContext)
  if (!ctx) {
    throw new Error('useSession must be used within SessionContext provider')
  }
  return ctx.user
}

/** "AD" → initials from full_name ("Andi Darmawan") or username fallback. */
export function initialsFor(user: Pick<SessionUser, 'full_name' | 'username'>): string {
  const name = user.full_name?.trim() || user.username || '?'
  const parts = name.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}
