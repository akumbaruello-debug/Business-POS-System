'use client'

import { Bell, Check, ChevronDown, Globe2, LogOut, Menu, Search, Settings, UserRound, X } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { logout } from '@/lib/auth'

const notifications = [
  { icon: '!', tone: 'warning', title: 'Low stock alert', detail: '18 items need attention.', time: '8 min ago' },
  { icon: '✓', tone: 'success', title: 'Payment received', detail: 'INV-2024-0842 was paid.', time: '42 min ago' },
  { icon: '↩', tone: 'info', title: 'Return requested', detail: 'Review a new return request.', time: '2 hr ago' },
]
const searchResults = [
  'INV-2024-0842 · Warung Seafood Makmur',
  'Fresh Tuna 10kg · Product',
  'Restoran Laut Biru · Customer',
  'PT Nelayan Sejahtera · Supplier',
]

export function Header({
  onMenu,
  accountOpen,
  setAccountOpen,
  notificationsOpen,
  setNotificationsOpen,
  searchOpen,
  setSearchOpen,
}: {
  onMenu: () => void
  accountOpen: boolean
  setAccountOpen: (value: boolean) => void
  notificationsOpen: boolean
  setNotificationsOpen: (value: boolean) => void
  searchOpen: boolean
  setSearchOpen: (value: boolean) => void
}) {
  const router = useRouter()

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
          <span>Workspace</span>
          <ChevronDown size={14} />
          <strong>Dashboard</strong>
        </div>
      </div>

      <div className="top-actions">
        <button
          className="global-search-trigger"
          onClick={() => setSearchOpen(true)}
          aria-label="Open global search"
        >
          <Search size={16} />
          <span>Search anything</span>
          <kbd>⌘ K</kbd>
        </button>

        <div className="popover-wrap">
          <button
            className="icon-button"
            onClick={() => {
              setNotificationsOpen(!notificationsOpen)
              setAccountOpen(false)
            }}
            aria-label="Notifications"
            aria-expanded={notificationsOpen}
          >
            <Bell size={18} />
            <i />
          </button>
          {notificationsOpen && (
            <div className="popover notification-panel">
              <div className="popover-head">
                <strong>Notifications</strong>
                <button aria-label="Mark all as read">
                  <Check size={14} />
                </button>
              </div>
              {notifications.map((item) => (
                <div className="notification-item" key={item.title}>
                  <span className={`notification-icon ${item.tone}`}>{item.icon}</span>
                  <span>
                    <strong>{item.title}</strong>
                    <small>{item.detail}</small>
                    <small>{item.time}</small>
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="popover-wrap">
          <button
            className="account-trigger"
            onClick={() => {
              setAccountOpen(!accountOpen)
              setNotificationsOpen(false)
            }}
            aria-expanded={accountOpen}
          >
            <span className="top-avatar">AD</span>
            <span className="account-text">
              <strong>Andi Darmawan</strong>
              <small>Owner</small>
            </span>
            <ChevronDown size={15} />
          </button>
          {accountOpen && (
            <div className="popover account-menu">
              <div className="account-menu-head">
                <span className="top-avatar">AD</span>
                <span>
                  <strong>Andi Darmawan</strong>
                  <small>andi@perikanan.id</small>
                </span>
              </div>
              {[
                [UserRound, 'Profile'],
                [Settings, 'Account settings'],
                [Settings, 'Preferences'],
                [Globe2, 'Language: English'],
              ].map(([Icon, label]) => (
                <button key={label as string}>
                  <Icon size={15} />
                  {label as string}
                </button>
              ))}
              <hr />
              <button className="sign-out" onClick={handleSignOut}>
                <LogOut size={15} />
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>

      {searchOpen && (
        <div
          className="search-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Global search"
        >
          <div className="search-dialog">
            <div className="search-input">
              <Search size={19} />
              <input autoFocus placeholder="Search invoices, products, customers..." />
              <button onClick={() => setSearchOpen(false)} aria-label="Close search">
                <X size={17} />
              </button>
            </div>
            <small className="search-caption">Recent searches</small>
            {searchResults.map((result) => (
              <button
                className="search-result"
                key={result}
                onClick={() => setSearchOpen(false)}
              >
                <Search size={15} />
                {result}
              </button>
            ))}
            <div className="search-empty">
              <Search size={20} />
              <span>Type to search across your workspace</span>
            </div>
          </div>
        </div>
      )}
    </header>
  )
}