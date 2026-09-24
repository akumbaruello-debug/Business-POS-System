'use client'

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from 'react'
import { en } from './en'
import { id } from './id'

export type Language = 'en' | 'id'

const STORAGE_KEY = 'pos-language'
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

const dictionaries: Record<Language, Record<string, string>> = { en, id }

interface I18nContextValue {
  language: Language
  setLanguage: (lang: Language) => Promise<void>
  t: (key: string, params?: Record<string, string | number>) => string
  isLoading: boolean
}

const I18nContext = createContext<I18nContextValue | undefined>(undefined)

function getStoredLanguage(): Language | null {
  if (typeof window === 'undefined') return null
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (raw === 'en' || raw === 'id') return raw
  return null
}

function setStoredLanguage(lang: Language) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(STORAGE_KEY, lang)
}

async function fetchSettings(token: string | null): Promise<{
  settings: Record<string, unknown>
  etag: string | null
} | null> {
  if (!token) return null
  try {
    const res = await fetch(`${API_URL}/settings`, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json',
      },
      credentials: 'include',
    })
    if (!res.ok) return null
    const data = await res.json()
    const etag = res.headers.get('etag')
    return { settings: data.settings ?? {}, etag }
  } catch {
    return null
  }
}

async function patchLanguage(
  lang: Language,
  token: string | null,
  etag: string | null,
): Promise<string | null> {
  if (!token) return null
  const idempotencyKey = crypto.randomUUID()
  try {
    const res = await fetch(`${API_URL}/settings`, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
        'Idempotency-Key': idempotencyKey,
        ...(etag ? { 'If-Match': etag } : {}),
      },
      credentials: 'include',
      body: JSON.stringify({ language: lang }),
    })
    if (!res.ok) return null
    return res.headers.get('etag')
  } catch {
    return null
  }
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>('en')
  const [etag, setEtag] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    document.documentElement.lang = language
  }, [language])

  useEffect(() => {
    let cancelled = false
    async function init() {
      const stored = getStoredLanguage()
      const token =
        typeof window !== 'undefined'
          ? window.localStorage.getItem('access_token')
          : null

      if (stored && !cancelled) {
        setLanguageState(stored)
      }

      const result = await fetchSettings(token)
      if (cancelled || !result) {
        setIsLoading(false)
        return
      }

      const serverLang = result.settings.language
      if (typeof serverLang === 'string' && (serverLang === 'en' || serverLang === 'id')) {
        setLanguageState(serverLang)
        setStoredLanguage(serverLang)
      }
      setEtag(result.etag)
      setIsLoading(false)
    }
    void init()
    return () => {
      cancelled = true
    }
  }, [])

  const setLanguage = useCallback(
    async (lang: Language) => {
      setLanguageState(lang)
      setStoredLanguage(lang)
      const token =
        typeof window !== 'undefined'
          ? window.localStorage.getItem('access_token')
          : null
      const newEtag = await patchLanguage(lang, token, etag)
      if (newEtag) {
        setEtag(newEtag)
      }
    },
    [etag],
  )

  const t = useCallback(
    (key: string, params?: Record<string, string | number>) => {
      const dict = dictionaries[language]
      let text = dict[key] ?? en[key] ?? key
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          text = text.replaceAll(`{${k}}`, String(v))
        }
      }
      return text
    },
    [language],
  )

  return (
    <I18nContext.Provider value={{ language, setLanguage, t, isLoading }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useLanguage() {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useLanguage must be used inside LanguageProvider')
  return ctx
}