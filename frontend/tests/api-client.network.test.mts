/**
 * Focused tests for `rawClient`'s network-vs-HTTP error handling.
 *
 * Run: `node --experimental-strip-types --test tests/`
 *
 * `node:test` + `node:assert` are Node built-ins, so this adds no dependency
 * and no test framework. The `.mts` extension keeps the file out of
 * tsconfig.json's `.ts` include pattern, so `npm run typecheck` is unaffected.
 *
 * Node's ESM resolver cannot follow the extensionless `./constants`
 * specifier that the app's bundler (`moduleResolution: bundler`) resolves.
 * Rather than add a build step, we copy the real `api-client.ts` bytes to a
 * temp dir and rewrite only that one specifier — every other byte, and all of
 * the logic under test, is exercised exactly as shipped.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const libDir = fileURLToPath(new URL('../lib/', import.meta.url))

// --- build a loadable copy of the real module -------------------------------
const source = readFileSync(join(libDir, 'api-client.ts'), 'utf8')
const transformed = source.replace("from './constants'", "from './constants.ts'")
assert.equal(
  transformed.replace("from './constants.ts'", "from './constants'"),
  source,
  'expected to rewrite exactly one specifier',
)

const stage = mkdtempSync(join(tmpdir(), 'pos-apiclient-'))
writeFileSync(join(stage, 'api-client.ts'), transformed)
writeFileSync(
  join(stage, 'constants.ts'),
  readFileSync(join(libDir, 'constants.ts'), 'utf8'),
)

// --- minimal browser shims --------------------------------------------------
const store = new Map<string, string>()
const localStorageShim = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, String(v)),
  removeItem: (k: string) => void store.delete(k),
  clear: () => store.clear(),
}
;(globalThis as any).localStorage = localStorageShim
;(globalThis as any).window = { localStorage: localStorageShim }

let fetchImpl: (url: string, init?: any) => Promise<Response>
;(globalThis as any).fetch = (url: any, init?: any) =>
  fetchImpl(String(url), init)

const { api, ApiError } = (await import(
  pathToFileURL(join(stage, 'api-client.ts')).href
)) as typeof import('../lib/api-client.ts')

const EN = 'Cannot reach the server. Check your connection.'
const ID = 'Tidak dapat menghubungi server. Periksa koneksi Anda.'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

test.beforeEach(() => {
  store.clear()
})

// --- network-level failures -------------------------------------------------

test('network failure surfaces a clear message, not "Failed to fetch"', async () => {
  fetchImpl = async () => {
    throw new TypeError('Failed to fetch')
  }
  await assert.rejects(api.get('/purchases'), (err: any) => {
    assert.ok(err instanceof ApiError, 'should be an ApiError')
    assert.equal(err.message, EN)
    assert.equal(err.code, 'network_error')
    assert.equal(err.status, 0)
    assert.doesNotMatch(err.message, /Failed to fetch/)
    return true
  })
})

test('network message follows the stored language', async () => {
  store.set('pos-language', 'id')
  fetchImpl = async () => {
    throw new TypeError('Failed to fetch')
  }
  await assert.rejects(api.post('/purchases', {}), (err: any) => {
    assert.equal(err.message, ID)
    return true
  })
})

test('an intentional abort is rethrown untouched', async () => {
  const abort = new Error('aborted')
  abort.name = 'AbortError'
  fetchImpl = async () => {
    throw abort
  }
  await assert.rejects(api.get('/purchases'), (err: any) => {
    assert.equal(err.name, 'AbortError')
    assert.ok(!(err instanceof ApiError))
    return true
  })
})

// --- ordinary HTTP responses must be preserved ------------------------------

test('readable 4xx envelope keeps the server message and code', async () => {
  fetchImpl = async () =>
    jsonResponse(403, {
      error: {
        code: 'origin_not_allowed',
        message: 'Origin not allowed.',
        details: null,
        request_id: 'req-1',
      },
    })
  await assert.rejects(api.post('/purchases', {}), (err: any) => {
    assert.equal(err.status, 403)
    assert.equal(err.code, 'origin_not_allowed')
    assert.equal(err.message, 'Origin not allowed.')
    assert.equal(err.requestId, 'req-1')
    return true
  })
})

test('readable 500 envelope keeps the server message and code', async () => {
  fetchImpl = async () =>
    jsonResponse(500, {
      error: {
        code: 'internal_error',
        message: 'An internal error occurred.',
        details: null,
        request_id: 'req-2',
      },
    })
  await assert.rejects(api.get('/purchases'), (err: any) => {
    assert.equal(err.status, 500)
    assert.equal(err.code, 'internal_error')
    assert.equal(err.message, 'An internal error occurred.')
    assert.doesNotMatch(err.message, /Cannot reach the server/)
    return true
  })
})

test('non-JSON error response still reports the status', async () => {
  fetchImpl = async () => new Response('nope', { status: 502 })
  await assert.rejects(api.get('/purchases'), (err: any) => {
    assert.equal(err.status, 502)
    assert.equal(err.message, 'HTTP 502')
    return true
  })
})

test('successful requests are unchanged', async () => {
  fetchImpl = async () => jsonResponse(200, { id: 7 })
  const body = await api.get('/purchases/7')
  assert.deepEqual(body, { id: 7 })
})

// --- Bearer-only session handling -------------------------------------------

test('requests authenticate with Authorization and no cookie credentials', async () => {
  store.set('access_token', 'tok-1')
  let seen: any
  fetchImpl = async (_url, init) => {
    seen = init
    return jsonResponse(200, { ok: true })
  }
  await api.get('/purchases')
  assert.equal(seen.headers.Authorization, 'Bearer tok-1')
  assert.equal(seen.credentials, undefined)
})

test('401 refreshes via the request body, then retries with the new Bearer', async () => {
  store.set('access_token', 'old-access')
  store.set('refresh_token', 'old-refresh')
  const calls: Array<{ url: string; init: any }> = []
  fetchImpl = async (url, init) => {
    calls.push({ url, init })
    if (url.endsWith('/auth/refresh')) {
      return jsonResponse(200, {
        access_token: 'new-access',
        refresh_token: 'new-refresh',
      })
    }
    return init?.headers?.Authorization === 'Bearer new-access'
      ? jsonResponse(200, { id: 1 })
      : jsonResponse(401, { error: { code: 'unauthorized' } })
  }

  const body = await api.get('/purchases')
  assert.deepEqual(body, { id: 1 })
  assert.equal(calls.length, 3, 'original, refresh, retry')

  const refresh = calls[1]
  assert.match(refresh.url, /\/auth\/refresh$/)
  assert.equal(refresh.init.method, 'POST')
  assert.equal(refresh.init.headers.Authorization, undefined)
  assert.deepEqual(JSON.parse(refresh.init.body), { refresh_token: 'old-refresh' })

  assert.equal(store.get('access_token'), 'new-access')
  assert.equal(store.get('refresh_token'), 'new-refresh')
  assert.equal(calls[2].init.headers.Authorization, 'Bearer new-access')
})

test('refresh failure clears the session and does not retry', async () => {
  store.set('access_token', 'dead-access')
  store.set('refresh_token', 'dead-refresh')
  let calls = 0
  fetchImpl = async (url) => {
    calls += 1
    if (url.endsWith('/auth/refresh')) {
      return jsonResponse(401, { error: { code: 'session_revoked' } })
    }
    return jsonResponse(401, { error: { code: 'unauthorized' } })
  }

  await assert.rejects(api.get('/purchases'), (err: any) => {
    assert.equal(err.status, 401)
    assert.equal(err.code, 'unauthorized')
    return true
  })
  assert.equal(store.has('access_token'), false)
  assert.equal(store.has('refresh_token'), false)
  assert.equal(calls, 2, 'no retry after a failed refresh')
})

test('concurrent 401s share a single refresh call', async () => {
  store.set('access_token', 'old-access')
  store.set('refresh_token', 'old-refresh')
  let refreshes = 0
  fetchImpl = async (url, init) => {
    if (url.endsWith('/auth/refresh')) {
      refreshes += 1
      await new Promise((resolve) => setTimeout(resolve, 5))
      return jsonResponse(200, {
        access_token: 'new-access',
        refresh_token: 'new-refresh',
      })
    }
    return init?.headers?.Authorization === 'Bearer new-access'
      ? jsonResponse(200, { ok: true })
      : jsonResponse(401, { error: { code: 'unauthorized' } })
  }

  const [a, b] = await Promise.all([api.get('/a'), api.get('/b')])
  assert.deepEqual(a, { ok: true })
  assert.deepEqual(b, { ok: true })
  assert.equal(refreshes, 1)
})