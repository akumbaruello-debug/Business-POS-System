import { RouteGuard } from '@/components/route-guard'
import NewUserPageClient from './_client'

export default function NewUserPage() {
  return (
    <RouteGuard required="user.manage">
      <NewUserPageClient />
    </RouteGuard>
  )
}
