'use client'

import { type ReactNode } from 'react'
import { LanguageProvider } from '@/lib/i18n'

export function RootProviders({ children }: { children: ReactNode }) {
  return <LanguageProvider>{children}</LanguageProvider>
}