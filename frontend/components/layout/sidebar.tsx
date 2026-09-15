'use client'

import {
  Boxes,
  ClipboardList,
  LayoutDashboard,
  Package,
  Store,
  Truck,
  Users,
} from 'lucide-react'
import { useSession, initialsFor } from '@/lib/session'

// Only routes with implemented pages ship. Dead sections (Sales,
// Purchasing, Finance, Administration) have no page yet, so their links
// are withheld rather than 404ing. Reintroduce a group when its first
// page lands.
const navigation = [
  { label: 'Overview', items: [{ label: 'Dashboard', icon: LayoutDashboard, href: '/dashboard' }] },
  {
    label: 'Inventory',
    items: [
      { label: 'Products', icon: Package, href: '/products' },
      { label: 'Inventory', icon: Boxes, href: '/inventory' },
    ],
  },
  {
    label: 'Customers',
    items: [{ label: 'Customers', icon: Users, href: '/customers' }],
  },
  {
    label: 'Purchasing',
    items: [
      { label: 'Purchases', icon: ClipboardList, href: '/purchases' },
      { label: 'Suppliers', icon: Truck, href: '/suppliers' },
    ],
  },
] as const

export function Sidebar({
  collapsed,
  onCollapse,
  mobileOpen,
  onNavigate,
}: {
  collapsed: boolean
  onCollapse: () => void
  mobileOpen: boolean
  onNavigate: () => void
}) {
  const user = useSession()
  const initials = initialsFor(user)
  return (
    <aside
      className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'sidebar-mobile-open' : ''}`}
      aria-label="Primary navigation"
    >
      <button
        type="button"
        className="brand brand-toggle"
        onClick={onCollapse}
        aria-label={collapsed ? 'Toggle sidebar (expanded)' : 'Toggle sidebar (collapsed)'}
        aria-expanded={!collapsed}
        title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        <span className="brand-mark">
          <Store size={19} />
        </span>
        <span className="brand-copy">
          <strong>Perikanan</strong>
          <small>INDONESIA</small>
        </span>
      </button>

      <nav className="nav-area">
        {navigation.map((group) => (
          <div className="nav-group" key={group.label}>
            <div className="nav-label">{group.label}</div>
            {group.items.map((item) => (
              <a
                key={item.label}
                href={item.href}
                className="nav-item"
                onClick={onNavigate}
                title={collapsed ? item.label : undefined}
              >
                <item.icon size={17} />
                <span>{item.label}</span>
              </a>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-bottom">
        <div className="user-card">
          <span className="avatar">{initials}</span>
          <span>
            <strong>{user.full_name || user.username}</strong>
            <small>{user.role_name}</small>
          </span>
        </div>
      </div>
    </aside>
  )
}
