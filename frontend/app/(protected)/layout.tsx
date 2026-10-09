import { redirect } from 'next/navigation'
import { cookies } from 'next/headers'
import { AppShell } from '@/components/layout/app-shell'
import { serverApi, isUnauthorizedError } from '@/lib/server-api-client'
import type { SessionUser } from '@/lib/auth'

/**
 * Server component that validates auth via httpOnly cookie.
 *
 * Reads the access_token cookie and validates it via /auth/me.
 * If not authenticated, redirects to /login.
 * If authenticated, renders the AppShell with children.
 *
 * The AppShell remains a client component for UI state (sidebar, mobile drawer).
 * SessionContext is provided by AppShell (or a nested client component).
 */
export default async function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies()
  const token = cookieStore.get('access_token')?.value

  if (!token) {
    redirect('/login')
  }

  // Validate token server-side
  try {
    const user = await serverApi.get<SessionUser>('/auth/me')
    // AppShell (client component) wraps children and provides SessionContext
    return <AppShell user={user}>{children}</AppShell>
  } catch (error) {
    if (isUnauthorizedError(error)) redirect('/login')
    throw error
  }
}