import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveE2EBaseURL } from './playwright-base-url.mjs'

test('exact-build mode ignores an inherited external base URL', () => {
  assert.equal(resolveE2EBaseURL(true, 'http://stale.example:9999'), 'http://127.0.0.1:4173')
})

test('normal mode preserves an explicit base URL and its default', () => {
  assert.equal(resolveE2EBaseURL(false, 'http://ecm.test:6100'), 'http://ecm.test:6100')
  assert.equal(resolveE2EBaseURL(false, undefined), 'http://localhost:6100')
})
