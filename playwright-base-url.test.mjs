import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveE2EBaseURL } from './playwright-base-url.mjs'

test('exact-build mode ignores an inherited external base URL', () => {
  assert.equal(resolveE2EBaseURL(true, 'http://stale.example:9999'), 'http://127.0.0.1:4173')
})

test('exact-build mode does not require E2E_BASE_URL at all', () => {
  assert.equal(resolveE2EBaseURL(true, undefined), 'http://127.0.0.1:4173')
})

test('normal mode preserves an explicit base URL', () => {
  assert.equal(resolveE2EBaseURL(false, 'http://ecm.test:6100'), 'http://ecm.test:6100')
})

test('normal mode with no E2E_BASE_URL fails loudly instead of defaulting to the live instance', () => {
  assert.throws(() => resolveE2EBaseURL(false, undefined), /E2E_BASE_URL/)
  assert.throws(() => resolveE2EBaseURL(false, undefined), /localhost:6100/)
})

test('normal mode treats an empty-string E2E_BASE_URL the same as unset', () => {
  assert.throws(() => resolveE2EBaseURL(false, ''), /E2E_BASE_URL/)
})
