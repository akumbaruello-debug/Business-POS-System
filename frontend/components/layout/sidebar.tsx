'use client'

import {
  Boxes,
  ClipboardList,
  Factory,
  FileText,
  LayoutDashboard,
  Package,
  PieChart,
  Settings,
  Shield,
  Store,
  Truck,
  UserCog,
  Users,
} from 'lucide-react'
import { useSession, initialsFor } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'
import { useCan } from '@/lib/authz'

interface NavItem {
  label: string
  icon: React.ComponentType<{ size?: number }>
  href: string
  required?: string
}

interface NavGroup {
  label: string
  items: NavItem[]
}

function useNavigation(): NavGroup[] {
  const { t } = useLanguage()
  const { can } = useCan()
  const groups: NavGroup[] = [
    { label: t('nav.overview'), items: [{ label: t('nav.dashboard'), icon: LayoutDashboard, href: '/dashboard' }] },
    {
      label: t('nav.inventory'),
      items: [
        { label: t('nav.products'), icon: Package, href: '/products' },
        { label: t('nav.inventory'), icon: Boxes, href: '/inventory' },
      ],
    },
    {
      label: t('nav.customers'),
      items: [{ label: t('nav.customers'), icon: Users, href: '/customers' }],
    },
    {
      label: t('nav.sales'),
      items: [
        { label: t('nav.sales'), icon: FileText, href: '/sales' },
        { label: t('nav.returnsRefunds'), icon: ClipboardList, href: '/sales/returns' },
      ],
    },
    {
      label: t('nav.purchasing'),
      items: [
        { label: t('nav.purchases'), icon: ClipboardList, href: '/purchases' },
        { label: t('nav.suppliers'), icon: Truck, href: '/suppliers' },
      ],
    },
    {
      label: t('nav.reports'),
      items: [{ label: t('nav.reports'), icon: PieChart, href: '/reports' }],
    },
    {
      label: t('nav.audit'),
      items: [{ label: t('nav.audit'), icon: FileText, href: '/audit', required: 'audit.view' }],
    },
    {
      label: t('nav.production'),
      items: [{ label: t('nav.production'), icon: Factory, href: '/production', required: 'production.view' }],
    },
    {
      label: t('nav.administration'),
      items: [
        { label: t('nav.users'), icon: UserCog, href: '/users', required: 'user.view' },
        { label: t('nav.roles'), icon: Shield, href: '/roles', required: 'role.view' },
      ],
    },
    {
      label: t('nav.settings'),
      items: [{ label: t('nav.settings'), icon: Settings, href: '/settings' }],
    },
  ]

  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => (item.required ? can(item.required) : true)),
    }))
    .filter((group) => group.items.length > 0)
}

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
  const navigation = useNavigation()
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
