import { RouteGuard } from '@/components/route-guard'
import RolesPageClient from './_client'

export default function RolesPage() {
  return (
    <RouteGuard required="role.view">
      <RolesPageClient />
    </RouteGuard>
  )
}
