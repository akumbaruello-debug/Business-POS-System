'use client'

import { useEffect, useState } from 'react'
import { Header } from './header'
import { Sidebar } from './sidebar'

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMobileOpen(false)
        setAccountOpen(false)
        setNotificationsOpen(false)
        setSearchOpen(false)
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setSearchOpen(true)
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
          notificationsOpen={notificationsOpen}
          setNotificationsOpen={setNotificationsOpen}
          searchOpen={searchOpen}
          setSearchOpen={setSearchOpen}
        />
        {children}
      </main>
    </div>
  )
}