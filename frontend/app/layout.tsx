import type { Metadata, Viewport } from 'next'
import './globals.css'
import { RootProviders } from '@/components/providers'

export const metadata: Metadata = {
  title: 'Perikanan Indonesia | Business Operations',
  description: 'Operational dashboard and design system foundation for Perikanan Indonesia.',
}

export const viewport: Viewport = {
  colorScheme: 'light',
  themeColor: '#f6f8fb',
  userScalable: false,
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="bg-background">
      <body className="antialiased">
        <RootProviders>{children}</RootProviders>
      </body>
    </html>
  )
}