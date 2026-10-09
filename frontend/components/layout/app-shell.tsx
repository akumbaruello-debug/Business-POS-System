'use client'

import { useEffect, useState } from 'react'
import { Header } from './header'
import { Sidebar } from './sidebar'
import { SessionContext } from '@/lib/session'
import type { SessionUser } from '@/lib/auth'

export function AppShell({
  children,
  user,
}: {
  children: React.ReactNode
  user: SessionUser
}) {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileOpen(false)
        setAccountOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [mobileOpen])

  return (
    <SessionContext.Provider value={{ user }}>
      <div className="app-shell">
        <Sidebar
          collapsed={collapsed}
          onCollapse={() => setCollapsed((v) => !v)}
          mobileOpen={mobileOpen}
          onNavigate={() => setMobileOpen(false)}
        />
        {mobileOpen && (
          <button
            className="drawer-backdrop"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
          />
        )}
        <main className={`workspace ${collapsed ? 'workspace-collapsed' : ''}`}>
          <Header
            onMenu={() => setMobileOpen(true)}
            accountOpen={accountOpen}
            setAccountOpen={setAccountOpen}
          />
          {children}
        </main>
      </div>
    </SessionContext.Provider>
  )
}