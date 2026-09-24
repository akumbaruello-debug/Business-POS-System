'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useSession } from '@/lib/session'
import { useLanguage } from '@/lib/i18n'

export function RouteGuard({ required, children }: { required: string | string[]; children: React.ReactNode }) {
  const session = useSession()
  const router = useRouter()
  const { t } = useLanguage()

  const caps = session?.capabilities || []
  const requiredArr = Array.isArray(required) ? required : [required]
  const hasAll = requiredArr.every((c) => caps.includes(c))

  useEffect(() => {
    if (!hasAll) router.replace('/')
  }, [hasAll, router])

  if (!hasAll) {
    return (
      <div className="page">
        <div className="notice error">
          {t('common.accessRestricted')}
        </div>
      </div>
    )
  }

  return <>{children}</>
}
