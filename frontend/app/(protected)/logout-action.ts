'use server'

import { cookies } from 'next/headers'
import { serverApi } from '@/lib/server-api-client'

/**
 * Phase 1.5 — Server-side logout action.
 * Calls the backend /auth/logout to revoke the session, then clears both
 * httpOnly cookies (access_token + refresh_token) on the server side.
 * Use this in server components/forms where client-side JS is unavailable.
 */
export async function logoutAction() {
  try {
    await serverApi.post('/auth/logout', {})
  } catch {
    // Best-effort: session may already be revoked server-side.
  } finally {
    const cookieStore = await cookies()
    cookieStore.delete('access_token')
    cookieStore.delete('refresh_token')
  }
}