import { API_URL } from './constants'

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown
  params?: Record<string, string>
}

async function client<T = any>(
  endpoint: string,
  options: RequestOptions = {}
): Promise<T> {
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
      // Throw error with response body for detailed messages
      throw new Error(data.message || data.detail || 'API error')
    }
    return data as T
  } else {
    // Non-JSON response (e.g., file download)
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    return (await response.text()) as unknown as T
  }
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
}