'use client'

import { ChevronDown, LogOut, Menu } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { logout } from '@/lib/auth'
import { useSession, initialsFor } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'

// Header surface. Global search and the notifications bell are deliberately
// absent: there is no backend contract for either, and shipping fabricated
// demo panels would be misleading. The account menu keeps Sign out and a
// Settings link; profile/preferences remain unimplemented.
export function Header({
  onMenu,
  accountOpen,
  setAccountOpen,
}: {
  onMenu: () => void
  accountOpen: boolean
  setAccountOpen: (value: boolean) => void
}) {
  const router = useRouter()
  const user = useSession()
  const initials = initialsFor(user)
  const { t } = useLanguage()

  const handleSignOut = () => {
    logout()
    router.push('/login')
  }

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button className="mobile-menu" onClick={onMenu} aria-label="Open navigation">
          <Menu size={19} />
        </button>
        <div className="breadcrumb">
          <span>{t('header.workspace')}</span>
          <ChevronDown size={14} />
          <strong>{t('nav.dashboard')}</strong>
        </div>
      </div>

      <div className="top-actions">
        <div className="popover-wrap">
          <button
            className="account-trigger"
            onClick={() => setAccountOpen(!accountOpen)}
            aria-expanded={accountOpen}
          >
            <span className="top-avatar">{initials}</span>
            <span className="account-text">
              <strong>{user.full_name || user.username}</strong>
              <small>{user.role_name}</small>
            </span>
            <ChevronDown size={15} />
          </button>
          {accountOpen && (
            <div className="popover account-menu">
              <div className="account-menu-head">
                <span className="top-avatar">{initials}</span>
                <span>
                  <strong>{user.full_name || user.username}</strong>
                  <small>{user.email || user.username}</small>
                </span>
              </div>
              <hr />
              <button
                className="account-menu-item"
                onClick={() => {
                  setAccountOpen(false)
                  router.push('/settings')
                }}
              >
                {t('nav.settings')}
              </button>
              <hr />
              <button className="sign-out" onClick={handleSignOut}>
                <LogOut size={15} />
                {t('header.signOut')}
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
