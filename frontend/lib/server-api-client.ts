import type { ApiError } from './api-client'
import type { SessionUser } from './auth'
import { cookies } from 'next/headers'

const API_BASE = 'http://localhost:8000/api/v1'

interface ServerRequestOptions {
  params?: Record<string, string | number | boolean | undefined | null>
  headers?: Record<string, string>
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  body?: unknown
}

const AUTH_COOKIE = 'access_token'

async function getAuthHeaders(): Promise<Record<string, string>> {
  const token = (await cookies()).get(AUTH_COOKIE)?.value
  return token ? { Cookie: `${AUTH_COOKIE}=${token}` } : {}
}

function buildUrl(path: string, params?: Record<string, string | number | boolean | undefined | null>): string {
  // Ensure path starts with /
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  const url = new URL(`${API_BASE}${normalizedPath}`)
  if (params) {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        qs.set(key, String(value))
      }
    })
    if (qs.size > 0) {
      url.search = qs.toString()
    }
  }
  return url.toString()
}

async function doFetch<T>(path: string, options: ServerRequestOptions): Promise<T> {
  const { params, headers, body } = options
  const url = buildUrl(path, params)
  
  const res = await fetch(url, {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
      ...(await getAuthHeaders()),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  
  if (!res.ok) {
    const error: ApiError = new Error(`API error: ${res.status}`) as ApiError
    error.status = res.status
    throw error
  }
  return res.json()
}

export const serverApi = {
  get<T>(path: string, options?: Omit<ServerRequestOptions, 'body'>): Promise<T> {
    return doFetch<T>(path, { ...options, method: 'GET' })
  },

  post<T>(path: string, body: unknown, options?: ServerRequestOptions): Promise<T> {
    return doFetch<T>(path, { ...options, method: 'POST', body })
  },

  put<T>(path: string, body: unknown, options?: ServerRequestOptions): Promise<T> {
    return doFetch<T>(path, { ...options, method: 'PUT', body })
  },

  delete<T>(path: string, options?: ServerRequestOptions): Promise<T> {
    return doFetch<T>(path, { ...options, method: 'DELETE' })
  },

  patch<T>(path: string, body: unknown, options?: ServerRequestOptions): Promise<T> {
    return doFetch<T>(path, { ...options, method: 'PATCH', body })
  },
}