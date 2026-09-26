import { RouteGuard } from '@/components/route-guard'
import RoleDetailPageClient from './_client'

export default function RoleDetailPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <RouteGuard required="role.view">
      <RoleDetailPageClient params={params} />
    </RouteGuard>
  )
}
