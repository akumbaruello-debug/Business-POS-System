'use client'

import { RouteGuard } from '@/components/route-guard'
import NewRolePageClient from './_client'

export default function NewRolePage() {
  return (
    <RouteGuard required="role.manage">
      <NewRolePageClient />
    </RouteGuard>
  )
}
