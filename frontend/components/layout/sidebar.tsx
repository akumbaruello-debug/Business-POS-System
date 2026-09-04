'use client'

import {
  BarChart3,
  Boxes,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  FileBarChart,
  LayoutDashboard,
  Package,
  Settings,
  ShoppingCart,
  Store,
  Truck,
  Users,
} from 'lucide-react'

const navigation = [
  { label: 'Overview', items: [{ label: 'Dashboard', icon: LayoutDashboard, href: '/dashboard' }] },
  {
    label: 'Sales',
    items: [
      { label: 'Sales', icon: ShoppingCart, href: '/sales' },
      { label: 'POS / New Sale', icon: Store, href: '/pos' },
      { label: 'Returns & Refunds', icon: ClipboardList, href: '/returns' },
    ],
  },
  {
    label: 'Purchasing',
    items: [
      { label: 'Purchases', icon: ClipboardList, href: '/purchases' },
      { label: 'Suppliers', icon: Truck, href: '/suppliers' },
    ],
  },
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
    label: 'Finance',
    items: [
      { label: 'Payments', icon: BarChart3, href: '/payments' },
      { label: 'Reports', icon: FileBarChart, href: '/reports' },
    ],
  },
  {
    label: 'Administration',
    items: [
      { label: 'Users & Roles', icon: Users, href: '/users' },
      { label: 'Settings', icon: Settings, href: '/settings' },
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
  return (
    <aside
      className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'sidebar-mobile-open' : ''}`}
      aria-label="Primary navigation"
    >
      <div className="brand">
        <span className="brand-mark">
          <Store size={19} />
        </span>
        <span className="brand-copy">
          <strong>Perikanan</strong>
          <small>INDONESIA</small>
        </span>
      </div>

      <button
        className="collapse-button"
        onClick={onCollapse}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
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
                {/* Optional badge, e.g., for Sales count */}
                {item.label === 'Sales' && <em>12</em>}
              </a>
            ))}
          </div>
        ))}
      </nav>

      <div className="sidebar-bottom">
        <div className="user-card">
          <span className="avatar">AD</span>
          <span>
            <strong>Andi Darmawan</strong>
            <small>Owner</small>
          </span>
        </div>
      </div>
    </aside>
  )
}