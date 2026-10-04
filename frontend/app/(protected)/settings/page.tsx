'use client'

import Link from 'next/link'
import {
  ArrowRight,
  Boxes,
  CircleDollarSign,
  CreditCard,
  Languages,
  Package,
  Ruler,
  ScrollText,
  ShieldCheck,
  Tags,
  Truck,
  Users,
  UsersRound,
  Wrench,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useSession } from '@/lib/session'
import { useLanguage, type Language } from '@/lib/i18n'

type SettingsLink = {
  title: string
  description: string
  href: string
  capability: string
  icon: LucideIcon
}

export default function SettingsPage() {
  const { language, setLanguage, t } = useLanguage()
  const user = useSession()
  const can = (capability: string) => user.capabilities.includes(capability)

  const masterData: SettingsLink[] = [
    { title: t('settings.products'), description: t('settings.productsDescription'), href: '/products', capability: 'product.view', icon: Package },
    { title: t('settings.customers'), description: t('settings.customersDescription'), href: '/customers', capability: 'contact.view', icon: UsersRound },
    { title: t('settings.suppliers'), description: t('settings.suppliersDescription'), href: '/suppliers', capability: 'contact.view', icon: Truck },
    { title: t('settings.inventory'), description: t('settings.inventoryDescription'), href: '/inventory', capability: 'inventory.view', icon: Boxes },
    { title: t('settings.units'), description: t('settings.unitsDescription'), href: '/settings/units', capability: 'unit.view', icon: Ruler },
    { title: t('settings.paymentMethods'), description: t('settings.paymentMethodsDescription'), href: '/settings/payment-methods', capability: 'payment_method.view', icon: CreditCard },
  ].filter((item) => can(item.capability))

  const businessSetup: SettingsLink[] = [
    { title: t('costTypes.title'), description: t('settings.costTypesDescription'), href: '/settings/cost-types', capability: 'cost_type.view', icon: Wrench },
    { title: t('settings.financialCategories'), description: t('settings.financialCategoriesDescription'), href: '/settings/financial-categories', capability: 'financial_category.view', icon: Tags },
    { title: t('settings.cashLedger'), description: t('settings.cashLedgerDescription'), href: '/finance/cash', capability: 'finance.view_cash', icon: CircleDollarSign },
    { title: t('settings.incomeExpenses'), description: t('settings.incomeExpensesDescription'), href: '/finance/manual-entries', capability: 'manual_entry.view', icon: CircleDollarSign },
  ].filter((item) => can(item.capability))

  const accessControl: SettingsLink[] = [
    { title: t('users.title'), description: t('settings.usersDescription'), href: '/users', capability: 'user.view', icon: Users },
    { title: t('roles.title'), description: t('settings.rolesDescription'), href: '/roles', capability: 'role.view', icon: ShieldCheck },
    { title: t('audit.title'), description: t('settings.auditDescription'), href: '/audit', capability: 'audit.view', icon: ScrollText },
  ].filter((item) => can(item.capability))

  return (
    <main className="content settings-page">
      <header className="settings-hero">
        <div className="eyebrow">{t('settings.workspaceSettings')}</div>
        <h1>{t('settings.title')}</h1>
        <p>{t('settings.description')}</p>
      </header>

      <section className="settings-section" aria-labelledby="settings-general-title">
        <div className="settings-section-heading">
          <div>
            <h2 id="settings-general-title">{t('settings.general')}</h2>
            <p>{t('settings.generalDescription')}</p>
          </div>
        </div>
        <div className="settings-language-card">
          <span className="settings-card-icon"><Languages size={19} /></span>
          <div className="settings-language-copy">
            <h3>{t('settings.language')}</h3>
            <p>{t('settings.chooseLanguage')}</p>
          </div>
          <label className="settings-language-control">
            <span>{t('settings.language')}</span>
            <select
              value={language}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) => {
                void setLanguage(e.target.value as Language)
              }}
            >
              <option value="en">{t('settings.language.english')}</option>
              <option value="id">{t('settings.language.indonesian')}</option>
            </select>
          </label>
        </div>
      </section>

      <SettingsSection title={t('settings.masterData')} description={t('settings.masterDataDescription')} items={masterData} emptyText={t('settings.noAccessiblePages')} />
      <SettingsSection title={t('settings.businessSetup')} description={t('settings.businessSetupDescription')} items={businessSetup} emptyText={t('settings.noAccessiblePages')} />
      <SettingsSection title={t('settings.accessControl')} description={t('settings.accessControlDescription')} items={accessControl} emptyText={t('settings.noAccessiblePages')} />
    </main>
  )
}

function SettingsSection({ title, description, items, emptyText }: { title: string; description: string; items: SettingsLink[]; emptyText: string }) {
  return (
    <section className="settings-section" aria-label={title}>
      <div className="settings-section-heading">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
      </div>
      {items.length > 0 ? (
        <div className="settings-card-grid">
          {items.map(({ title: itemTitle, description: itemDescription, href, icon: Icon }) => (
            <Link className="settings-link-card" href={href} key={href}>
              <span className="settings-card-icon"><Icon size={19} /></span>
              <span className="settings-link-copy">
                <strong>{itemTitle}</strong>
                <small>{itemDescription}</small>
              </span>
              <ArrowRight className="settings-link-arrow" size={17} />
            </Link>
          ))}
        </div>
      ) : (
        <p className="settings-empty">{emptyText}</p>
      )}
    </section>
  )
}
