import { expect } from '@playwright/test'
import { test } from './fixtures/task-notifications'
import { navigateToTab } from './fixtures/base'
import recorded from '../backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json'
import type { BulkCommitResponse } from '../frontend/src/services/api'
import { STAGED_LEDGER_STORAGE_KEY } from '../frontend/src/utils/stagedLedgerStorage'

for (const initialFault of ['accepted-unapplied', 'rejected', 'read-failed', 'lost-response', 'scope-changed', 'channel-refresh-failed']) {
  test(`post-create profile intent: ${initialFault}, membership-only retry and reload`, async ({ privateTask }, testInfo) => {
    const { page } = privateTask
    let fault = initialFault
    let created = false
    let commits = 0
    let wrote = false
    let refused = 0
    let replaced = false
    let channelRefreshUnavailable = initialFault === 'channel-refresh-failed'
    let failedChannelReads = 0
    let finalReads = 0
    let commitResult: BulkCommitResponse | undefined
    const writes: Array<{ channelIds: number[]; enabled: boolean }> = []
    const binding = 'f'.repeat(64)
    const profile = { ...recorded.profiles.find(item => item.id === 6)!, channels: [] as number[] }
    const channel = {
      id: 50000, name: 'Profile intent browser fixture', channel_number: 100,
      channel_group_id: null, tvg_id: null, tvc_guide_stationid: null, epg_data_id: null,
      streams: [], stream_profile_id: null, uuid: 'synthetic-created-channel',
      logo_id: null, auto_created: false, auto_created_by: null, auto_created_by_name: null,
    }
    await page.route('**/api/settings', route => route.fulfill({ json: {
      configured: true, url: 'https://fixture.invalid', theme: 'dark',
      default_channel_profile_ids: [6], default_normalize_on_create: false,
    } }))
    await page.route('**/api/channels?*', route => {
      if (created && channelRefreshUnavailable) {
        failedChannelReads++
        return route.fulfill({ status: 503, json: { detail: 'Synthetic channel refresh unavailable' } })
      }
      return route.fulfill({ json: {
        results: created ? [channel] : [], count: created ? 1 : 0, next: null, previous: null,
      } })
    })
    await page.route('**/api/normalization/mappings/resolve', route => {
      const { texts } = route.request().postDataJSON()
      return route.fulfill({ json: { results: texts.map((original: string) => ({ original, preferred_name: null })) } })
    })
    await page.route('**/api/channels/normalize-preview-batch', route => route.fulfill({ json: { results: [] } }))
    await page.route('**/api/channels/bulk-commit/private-profile-create', route => route.fulfill({
      json: { job_id: 'private-profile-create', status: 'completed', result: commitResult },
    }))
    await page.route('**/api/channels/bulk-commit', async route => {
      const body = route.request().postDataJSON()
      expect(body.validateOnly).not.toBe(true)
      expect(body.profileRecoveryOperator).toBe('anonymous')
      const creates = body.operations.filter((operation: { type: string }) => operation.type === 'createChannel')
      expect(creates).toHaveLength(1)
      expect(creates[0].name).toBe(channel.name)
      if (initialFault === 'rejected') expect(creates[0].channelNumber).toBeUndefined()
      commits++
      created = true
      commitResult = {
        success: true, operationsApplied: body.operations.length, operationsFailed: 0, errors: [],
        tempIdMap: { [creates[0].tempId]: channel.id }, groupIdMap: {}, profileRecoveryBinding: binding,
      }
      return route.fulfill({ status: 202, json: { job_id: 'private-profile-create', status: 'running' } })
    })
    await page.route('**/api/channel-profiles', route => route.fulfill({ json: [profile] }))
    await page.route('**/api/created-channel-profiles**', async route => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      expect(request.headers()['x-ecm-profile-recovery']).toBe(binding)
      if (fault === 'scope-changed' && request.method() === 'PATCH') {
        // The browser URL and receipt are unchanged; switch the private peer's
        // accepted scope between the fresh detail read and first mutation.
        replaced = true
        refused++
      }
      if (replaced) return route.fulfill({ status: 409, json: { detail: 'Different Dispatcharr connection' } })
      if (request.method() === 'GET') {
        if (wrote && path === '/api/created-channel-profiles') finalReads++
        if (wrote && fault === 'read-failed') return route.fulfill({ status: 503, json: { detail: 'Synthetic read unavailable' } })
        return route.fulfill({ json: path === '/api/created-channel-profiles' ? [profile] : profile })
      }
      expect(request.method()).toBe('PATCH')
      const body = request.postDataJSON()
      const ids = body.channel_ids ?? [Number(path.split('/').at(-1))]
      expect(ids).toEqual([channel.id])
      writes.push({ channelIds: ids, enabled: body.enabled })
      wrote = true
      if (fault === 'rejected' || fault === 'channel-refresh-failed') {
        return route.fulfill({ status: 400, json: { detail: 'Synthetic write rejected' } })
      }
      if (fault !== 'accepted-unapplied') profile.channels = body.enabled ? [...ids] : []
      if (fault === 'lost-response') return route.abort('connectionreset')
      return route.fulfill({ json: { success: true } })
    })

    // Reload after installing overrides: settings/default profile intent must
    // come from this test's private peer, not the fixture's earlier mount.
    await page.reload()
    await navigateToTab(page, 'channel-manager')
    await page.getByRole('button', { name: /Edit Mode/ }).click()
    await page.getByRole('button', { name: 'Create new channel', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Create Channel', exact: true })
    await dialog.getByPlaceholder('Enter channel name').fill(channel.name)
    if (initialFault !== 'rejected') await dialog.getByPlaceholder('e.g., 100 or 38.1').fill('100')
    await dialog.getByRole('button', { name: /Create Channel$/ }).click()
    await expect(dialog).not.toBeVisible()
    expect(writes).toEqual([])
    expect(commits).toBe(0)
    await page.getByTitle('Apply changes', { exact: true }).click()
    await page.getByRole('button', { name: /Apply All/ }).click()
    await expect.poll(() => commits).toBe(1)
    await expect.poll(() => wrote || refused > 0).toBe(true)
    const notice = page.locator('.created-channel-profiles-notice')

    if (initialFault === 'lost-response') {
      await expect.poll(() => finalReads).toBeGreaterThan(0)
      await expect(notice).toHaveCount(0)
      expect(profile.channels).toEqual([channel.id])
      expect(writes).toHaveLength(1)
    } else {
      await expect(notice).toContainText('Profile assignments unresolved')
      if (initialFault === 'scope-changed') {
        await expect(notice).toContainText('original ECM sign-in and Dispatcharr connection')
        expect(refused).toBe(1)
        expect(writes).toEqual([])
      }
      if (initialFault === 'channel-refresh-failed') {
        expect(failedChannelReads).toBeGreaterThan(0)
        await expect(page.getByRole('button', { name: /Apply All/ })).toHaveCount(0)
        expect(await page.evaluate(key => sessionStorage.getItem(key), STAGED_LEDGER_STORAGE_KEY)).toBeNull()
      }
      await expect(notice.getByRole('button', { name: 'Retry profile assignments' })).toBeEnabled()
      const layout = await notice.evaluate(element => ({
        height: element.getBoundingClientRect().height,
        grow: getComputedStyle(element).flexGrow,
        minimum: getComputedStyle(element).minHeight,
      }))
      expect(layout.grow).toBe('0')
      expect(layout.minimum).toBe('0px')
      expect(layout.height).toBeLessThan(page.viewportSize()!.height / 3)
      await page.screenshot({ path: testInfo.outputPath('unresolved-profile-intent.png'), fullPage: true })
      const previousWrites = writes.length
      channelRefreshUnavailable = false
      await page.reload()
      await navigateToTab(page, 'channel-manager')
      await expect(notice).toContainText('Profile assignments unresolved')
      expect(writes).toHaveLength(previousWrites)
      fault = 'healthy'
      replaced = false
      await notice.getByRole('button', { name: 'Retry profile assignments' }).click()
      await expect(notice).toHaveCount(0)
      expect(profile.channels).toEqual([channel.id])
    }
    expect(commits).toBe(1)
  })
}
