import { RouteGuard } from '@/components/route-guard'
import UsersPageClient from './_client'

export default function UsersPage() {
  return (
    <RouteGuard required="user.view">
      <UsersPageClient />
    </RouteGuard>
  )
}
