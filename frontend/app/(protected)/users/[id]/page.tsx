import { RouteGuard } from '@/components/route-guard'
import UserDetailPageClient from './_client'

export default function UserDetailPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <RouteGuard required="user.view">
      <UserDetailPageClient params={params} />
    </RouteGuard>
  )
}
