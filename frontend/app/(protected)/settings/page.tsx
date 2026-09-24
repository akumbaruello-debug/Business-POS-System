'use client'

import { useLanguage, type Language } from '@/lib/i18n'

const LANGUAGES: { value: Language; labelKey: string }[] = [
  { value: 'en', labelKey: 'settings.language.english' },
  { value: 'id', labelKey: 'settings.language.indonesian' },
]

export default function SettingsPage() {
  const { language, setLanguage, t } = useLanguage()

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-semibold">{t('settings.title')}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t('settings.description')}
        </p>
      </div>

      <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
        <div className="flex flex-col space-y-1.5 p-6 pb-3">
          <h3 className="text-base font-semibold leading-none tracking-tight">
            {t('settings.language')}
          </h3>
        </div>
        <div className="p-6 pt-0 space-y-4">
          <p className="text-sm text-muted-foreground">
            {t('settings.chooseLanguage')}
          </p>
          <select
            value={language}
            onChange={(e: React.ChangeEvent<HTMLSelectElement>) => {
              void setLanguage(e.target.value as Language)
            }}
            className="flex h-10 w-60 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            {LANGUAGES.map((lang) => (
              <option key={lang.value} value={lang.value}>
                {t(lang.labelKey)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
        <div className="flex flex-col space-y-1.5 p-6 pb-3">
          <h3 className="text-base font-semibold leading-none tracking-tight">
            {t('common.status')}
          </h3>
        </div>
        <div className="p-6 pt-0">
          <p className="text-sm text-muted-foreground">
            {t('settings.noOtherOptions')}
          </p>
        </div>
      </div>
    </div>
  )
}
