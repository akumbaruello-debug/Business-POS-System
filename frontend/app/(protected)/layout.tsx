'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { AppShell } from '@/components/layout/app-shell'
import { isAuthenticated, getCurrentUser, type SessionUser } from '@/lib/auth'
import { SessionContext } from '@/lib/session'
import { ApiError } from '@/lib/api-client'

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter()
  const [loading, setLoading] = useState(true)
  const [user, setUser] = useState<SessionUser | null>(null)

  useEffect(() => {
    const checkAuth = async () => {
      if (!isAuthenticated()) {
        router.replace('/login')
        return
      }
      try {
        const u = await getCurrentUser()
        if (!u) {
          router.replace('/login')
          return
        }
        setUser(u)
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace('/login')
          return
        }
        if (err instanceof ApiError && err.status >= 500) {
          setLoading(false)
          return
        }
        router.replace('/login')
        return
      } finally {
        setLoading(false)
      }
    }
    checkAuth()
  }, [router])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-muted-foreground">Loading...</p>
      </div>
    )
  }

  if (!user) return null

  return (
    <SessionContext.Provider value={{ user }}>
      <AppShell>{children}</AppShell>
    </SessionContext.Provider>
  )
}
