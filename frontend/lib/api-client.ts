import { API_URL } from './constants'

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown
  params?: Record<string, string>
}

export interface ApiResult<T = any> {
  data: T
  status: number
  headers: Headers
}

async function rawClient<T = any>(
  endpoint: string,
  options: RequestOptions = {}
): Promise<ApiResult<T>> {
  const { body, params, headers: customHeaders, ...rest } = options

  const url = new URL(`${API_URL}${endpoint}`)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, value)
      }
    })
  }

  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...customHeaders,
  }

  // Attach access token from localStorage if present
  const token = localStorage.getItem('access_token')
  if (token) {
    ;(headers as Record<string, string>)['Authorization'] = `Bearer ${token}`
  }

  const config: RequestInit = {
    ...rest,
    headers,
    credentials: 'include',
  }

  if (body !== undefined) {
    config.body = JSON.stringify(body)
  }

  const response = await fetch(url.toString(), config)

  // Handle 401 Unauthorized — could trigger logout
  if (response.status === 401) {
    // Clear invalid token
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    // Let the caller handle redirect
    throw new Error('Unauthorized')
  }

  const contentType = response.headers.get('content-type')
  if (contentType?.includes('application/json')) {
    const data = await response.json()
    if (!response.ok) {
      // Backend envelope: { error: { code, message, details, request_id } }.
      const errBody = data?.error ?? data
      const message =
        (typeof errBody?.message === 'string' && errBody.message) ||
        (typeof data?.detail === 'string' && data.detail) ||
        (typeof data?.message === 'string' && data.message) ||
        `Request failed with status ${response.status}`
      const err = new Error(message)
      // Preserve the backend error code (e.g. version_mismatch,
      // lifecycle_state_invalid, idempotency_violation) so callers can
      // branch on it without parsing the message text.
      ;(err as Error & { code?: string }).code =
        typeof errBody?.code === 'string' ? errBody.code : undefined
      throw err
    }
    return { data: data as T, status: response.status, headers: response.headers }
  } else {
    // Non-JSON response (e.g. file download / 204 No Content)
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    return {
      data: (await response.text()) as unknown as T,
      status: response.status,
      headers: response.headers,
    }
  }
}

async function client<T = any>(
  endpoint: string,
  options: RequestOptions = {}
): Promise<T> {
  const { data } = await rawClient<T>(endpoint, options)
  return data
}

export const api = {
  get: <T = any>(endpoint: string, options?: Omit<RequestOptions, 'body' | 'method'>) =>
    client<T>(endpoint, { ...options, method: 'GET' }),
  post: <T = any>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, 'body' | 'method'>) =>
    client<T>(endpoint, { ...options, method: 'POST', body }),
  put: <T = any>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, 'body' | 'method'>) =>
    client<T>(endpoint, { ...options, method: 'PUT', body }),
  patch: <T = any>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, 'body' | 'method'>) =>
    client<T>(endpoint, { ...options, method: 'PATCH', body }),
  delete: <T = any>(endpoint: string, options?: Omit<RequestOptions, 'body' | 'method'>) =>
    client<T>(endpoint, { ...options, method: 'DELETE' }),

  /**
   * Same verbs as `api`, but resolves `{ data, status, headers }` so callers
   * can read response headers (notably `ETag`) and status codes. Used by the
   * Purchasing draft-mutation flow, which needs the server's canonical ETag
   * for the next `If-Match` without a second GET.
   */
  headers: {
    get: <T = any>(endpoint: string, options?: Omit<RequestOptions, 'body' | 'method'>) =>
      rawClient<T>(endpoint, { ...options, method: 'GET' }),
    post: <T = any>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, 'body' | 'method'>) =>
      rawClient<T>(endpoint, { ...options, method: 'POST', body }),
    put: <T = any>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, 'body' | 'method'>) =>
      rawClient<T>(endpoint, { ...options, method: 'PUT', body }),
    patch: <T = any>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, 'body' | 'method'>) =>
      rawClient<T>(endpoint, { ...options, method: 'PATCH', body }),
    delete: <T = any>(endpoint: string, options?: Omit<RequestOptions, 'body' | 'method'>) =>
      rawClient<T>(endpoint, { ...options, method: 'DELETE' }),
  },
}
