import { test, expect, type Page } from '@playwright/test'
import { navigateToTab } from './fixtures/base'

function navigationControl(options: { missing?: boolean; blank?: boolean; recovers?: boolean; loading?: boolean } = {}) {
  const calls = { reloads: 0, clicks: 0, sleeps: [] as number[] }
  const original = new Error('original content failure')
  const page = {
    waitForLoadState: async () => {},
    waitForTimeout: async (ms: number) => { calls.sleeps.push(ms) },
    reload: async () => { calls.reloads++ },
    waitForFunction: async () => {},
    waitForSelector: async (selector: string) => {
      if (selector === '.tab-loading' && options.loading) throw original
      if (selector === '.settings-tab' && options.missing && !(options.recovers && calls.reloads)) throw original
    },
    locator: (selector: string) => ({
      waitFor: async () => { if (selector.includes('input')) throw new Error('no login form') },
      isVisible: async () => selector === '.primary-navigation' ? !options.blank || calls.reloads > 0 : false,
      click: async () => { calls.clicks++ },
    }),
  } as unknown as Page
  return { page, calls, original }
}

test('healthy requested content resolves without recovery or sleep', async () => {
  const { page, calls } = navigationControl()
  await navigateToTab(page, 'settings')
  expect(calls).toEqual({ reloads: 0, clicks: 1, sleeps: [] })
})

test('missing content with navigation visible rejects with original context', async () => {
  const { page, calls, original } = navigationControl({ missing: true })
  await expect(navigateToTab(page, 'settings')).rejects.toThrow(/original content failure/)
  await expect(navigateToTab(page, 'settings')).rejects.toHaveProperty('cause', original)
  expect(calls.reloads).toBe(0)
})

test('blank app recovers exactly once', async () => {
  const { page, calls } = navigationControl({ missing: true, blank: true, recovers: true })
  await navigateToTab(page, 'settings')
  expect(calls.reloads).toBe(1)
  expect(calls.clicks).toBe(2)
})

test('repeated absence rejects after one recovery and retains first cause', async () => {
  const { page, calls } = navigationControl({ missing: true, blank: true })
  await expect(navigateToTab(page, 'settings')).rejects.toThrow(/original content failure/)
  expect(calls.reloads).toBe(1)
})

test('stuck loading cannot be mistaken for content readiness', async () => {
  const { page } = navigationControl({ loading: true })
  await expect(navigateToTab(page, 'settings')).rejects.toThrow(/original content failure/)
})

test('unknown route fails before browser actions', async () => {
  const { page, calls } = navigationControl()
  await expect(navigateToTab(page, 'not-a-route')).rejects.toThrow(/Unsupported tab/)
  expect(calls).toEqual({ reloads: 0, clicks: 0, sleeps: [] })
})

test('recovery failure keeps both the first error and the recovery diagnostic', async () => {
  const { page, original } = navigationControl({ missing: true, blank: true })
  page.reload = async () => { throw new Error('recovery transport failure') }
  const error = await navigateToTab(page, 'settings').catch(error => error)
  expect(error.message).toMatch(/original content failure.*recovery transport failure/)
  expect(error.cause).toBe(original)
})

test('current primary routes require exact content roots, never logo/EPG/M3U chrome', async () => {
  for (const [route, root] of Object.entries({
    dashboard: '.operator-dashboard', 'channel-manager': '.channels-pane', settings: '.settings-tab',
    stats: '.stats-tab', 'm3u-manager': '.m3u-manager-tab', 'm3u-changes': '.m3u-changes-tab',
    'epg-manager': '.epg-manager-tab', 'logo-manager': '.logo-manager-tab', guide: '.guide-tab',
    journal: '.journal-tab', 'channel-pipeline': '.channel-pipeline-tab',
  })) {
    const { page } = navigationControl()
    const roots: string[] = []
    page.waitForSelector = (async (selector: string) => { roots.push(selector); return null }) as unknown as Page['waitForSelector']
    await navigateToTab(page, route)
    expect(roots).toEqual(['.primary-navigation', root, '.tab-loading', root])
  }
})
