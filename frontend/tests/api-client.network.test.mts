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