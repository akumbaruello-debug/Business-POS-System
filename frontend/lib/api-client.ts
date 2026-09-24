import { API_URL } from './constants'

export interface ApiErrorDetails {
  code?: string
  message?: string
  details?: unknown
  request_id?: string
  errors?: Array<{ loc: (string | number)[]; msg: string; type: string }>
}

export class ApiError extends Error {
  status: number
  code?: string
  details?: ApiErrorDetails
  requestId?: string
  validationErrors?: ApiErrorDetails['errors']

  constructor(status: number, message: string, details?: ApiErrorDetails) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = details?.code
    this.details = details
    this.requestId = details?.request_id
    this.validationErrors = details?.errors
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  params?: Record<string, string | number | boolean | undefined | null>
  ifMatch?: string
  idempotencyKey?: string | true
}

export interface ApiResult<T = any> {
  data: T
  status: number
  headers: Headers
}

function generateIdempotencyKey(): string {
  return crypto.randomUUID()
}

function buildQueryParams(
  params: RequestOptions['params']
): URLSearchParams | undefined {
  if (!params) return undefined
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null) return
    qs.set(key, String(value))
  })
  return qs.size > 0 ? qs : undefined
}

async function rawClient<T = any>(
  endpoint: string,
  options: RequestOptions = {}
): Promise<ApiResult<T>> {
  const {
    body,
    params,
    headers: customHeaders,
    ifMatch,
    idempotencyKey,
    ...rest
  } = options

  const url = new URL(`${API_URL}${endpoint}`)
  const qs = buildQueryParams(params)
  if (qs) {
    qs.forEach((value, key) => url.searchParams.set(key, value))
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }

  if (customHeaders) {
    const normalized = customHeaders as Record<string, string>
    Object.entries(normalized).forEach(([key, value]) => {
      if (value !== undefined) headers[key] = value
    })
  }

  if (ifMatch) {
    headers['If-Match'] = ifMatch
  }

  const isMutation = rest.method && rest.method !== 'GET' && rest.method !== 'HEAD'
  if (isMutation && idempotencyKey) {
    headers['Idempotency-Key'] =
      idempotencyKey === true ? generateIdempotencyKey() : idempotencyKey
  }

  const token = localStorage.getItem('access_token')
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
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

  if (response.status === 401) {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    throw new ApiError(401, 'Unauthorized', { code: 'unauthorized' })
  }

  const contentType = response.headers.get('content-type')
  const etag = response.headers.get('etag') ?? undefined

  if (contentType?.includes('application/json')) {
    const data = await response.json()
    if (!response.ok) {
      const errBody = data?.error ?? data
      const message =
        (typeof errBody?.message === 'string' && errBody.message) ||
        (typeof data?.detail === 'string' && data.detail) ||
        (typeof data?.message === 'string' && data.message) ||
        `Request failed with status ${response.status}`
      const details: ApiErrorDetails = {
        code: typeof errBody?.code === 'string' ? errBody.code : undefined,
        message: typeof errBody?.message === 'string' ? errBody.message : undefined,
        details: errBody?.details,
        request_id: typeof errBody?.request_id === 'string' ? errBody.request_id : undefined,
        errors: Array.isArray(errBody?.errors) ? errBody.errors : undefined,
      }
      throw new ApiError(response.status, message, details)
    }
    return {
      data: data as T,
      status: response.status,
      headers: response.headers,
      ...(etag ? { etag } : {}),
    } as ApiResult<T>
  }

  if (!response.ok) {
    throw new ApiError(response.status, `HTTP ${response.status}`)
  }

  return {
    data: (await response.text()) as unknown as T,
    status: response.status,
    headers: response.headers,
    ...(etag ? { etag } : {}),
  } as ApiResult<T>
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
