import { test, expect, type Page } from './fixtures/base'

async function dismissFirstRunPromptIfPresent(page: Page) {
  const close = page.getByRole('button', { name: 'Close' })
  if (await close.isVisible().catch(() => false)) await close.click()
}

async function shellMetrics(page: Page) {
  return page.evaluate(() => {
    const sidebar = document.querySelector<HTMLElement>('.primary-sidebar')!
    const main = document.querySelector<HTMLElement>('#main-content')!
    const links = [...sidebar.querySelectorAll<HTMLElement>('.navigation-destination')]
    const sidebarRect = sidebar.getBoundingClientRect()
    const mainRect = main.getBoundingClientRect()
    return {
      width: sidebarRect.width,
      noSidebarXOverflow: sidebar.clientWidth === sidebar.scrollWidth,
      mainClear: mainRect.left >= sidebarRect.right,
      noDocumentXOverflow: document.documentElement.clientWidth === document.documentElement.scrollWidth,
      targetsPractical: links.every((link) => link.getBoundingClientRect().height >= 40),
      labelsHidden: [...sidebar.querySelectorAll<HTMLElement>('.navigation-label, .navigation-group h2')]
        .every((label) => getComputedStyle(label).display === 'none'),
      iconsCentered: links.every((link) => {
        const icon = link.querySelector<HTMLElement>('.material-icons')!
        const linkRect = link.getBoundingClientRect()
        const iconRect = icon.getBoundingClientRect()
        return Math.abs((linkRect.left + linkRect.width / 2) - (iconRect.left + iconRect.width / 2)) <= 1
      }),
    }
  })
}

async function expectContentWithinMain(page: Page, route: 'channel-manager' | 'guide') {
  const selectors = ['.route-page-header', '#main-content h1', ...(route === 'channel-manager'
    ? ['.route-page-header .enter-edit-mode-btn', '.channel-manager-tab', '.split-pane']
    : ['.guide-tab', '.guide-controls', 'button[title="Refresh program data"]', 'button[title="Print channel guide"]', '.guide-container', '.guide-footer'])]

  for (const selector of selectors) {
    const item = page.locator(selector).first()
    await expect(item).toBeVisible()
    await item.scrollIntoViewIfNeeded()
    const bounds = await item.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      const main = document.querySelector<HTMLElement>('#main-content')!.getBoundingClientRect()
      return {
        withinMainX: rect.left >= main.left - 1 && rect.right <= main.right + 1,
        intersectsViewportY: rect.bottom > 0 && rect.top < window.innerHeight,
      }
    })
    expect(bounds.withinMainX, `${selector} must stay within usable main width`).toBe(true)
    expect(bounds.intersectsViewportY, `${selector} must remain vertically reachable`).toBe(true)
  }
}

const routeConsumers = [
  { name: 'Dashboard', heading: 'OVERVIEW / DASHBOARD', settled: '.dashboard-route' },
  { name: 'Channel Manager', heading: 'OPERATIONS / CHANNEL MANAGER', settled: '.enter-edit-mode-btn' },
  { name: 'Guide', heading: 'OPERATIONS / GUIDE', settled: 'button[title="Refresh program data"]' },
  { name: 'M3U Manager', heading: 'OPERATIONS / M3U MANAGER', settled: 'button:has-text("Add M3U Account")' },
  { name: 'EPG Manager', heading: 'OPERATIONS / EPG MANAGER', settled: 'button:has-text("Add Standard EPG")' },
  { name: 'Logo Manager', heading: 'OPERATIONS / LOGO MANAGER', settled: 'button:has-text("Add Logo")' },
  { name: 'Channel Pipeline', heading: 'AUTOMATION / CHANNEL PIPELINE', settled: 'button[aria-label="Create rule"]' },
  { name: 'M3U Changes', heading: 'AUTOMATION / M3U CHANGES', settled: 'button:has-text("Refresh")' },
  { name: 'Stats', heading: 'INSIGHTS / STATS', settled: 'button:has-text("Refresh")' },
  { name: 'Journal', heading: 'INSIGHTS / JOURNAL', settled: 'button:has-text("Refresh")' },
  { name: 'Settings', heading: 'SYSTEM / SETTINGS', settled: 'button:has-text("Save Settings")' },
] as const

async function expectSettledRoute(page: Page, consumer: typeof routeConsumers[number]) {
  const heading = page.locator('#main-content h1')
  await expect(heading).toHaveText(consumer.heading)
  await expect(heading).toHaveCount(1)
  await expect(page.locator('#main-content .tab-loading')).toHaveCount(0)
  const settled = page.locator(consumer.settled).first()
  await expect(settled).toBeVisible()
  return settled
}

async function openShellWithPipelineFixture(
  page: Page,
  rulesStatus = 200,
  providers: Array<Record<string, unknown>> = [],
  epgSources: Array<Record<string, unknown>> = [],
) {
  await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      configured: true,
      default_channel_profile_ids: [],
      stream_sort_priority: [],
      stream_sort_enabled: {},
      m3u_account_priorities: {},
      custom_network_prefixes: [],
      hide_auto_sync_groups: false,
    }),
  }))
  await page.route(/\/api\/providers(?:\/|\?|$)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(providers) }))
  await page.route(/\/api\/m3u\/server-groups(?:\/|\?|$)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }))
  await page.route(/\/api\/epg\/sources(?:\/|\?|$)/, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(epgSources) }))
  await page.route(/\/api\/channels\/logos(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        count: 1,
        next: null,
        previous: null,
        results: [{ id: 9, name: 'Persisted fixture artwork', url: '/persisted-channel-artwork.png', cache_url: '/persisted-channel-artwork.png' }],
      }),
    }))
  await page.route(/\/api\/channel-pipeline\/rules(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: rulesStatus,
      contentType: 'application/json',
      body: rulesStatus === 200 ? JSON.stringify({ rules: [] }) : JSON.stringify({ detail: 'Pipeline unavailable' }),
    }))
  await page.route(/\/api\/channel-pipeline\/executions(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ executions: [], total: 0, page: 1, page_size: 25 }),
    }))
  await page.route(/\/api\/channel-pipeline\/circuit-breaker(?:\/|\?|$)/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ disabled: false, reason: null }),
    }))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('.tab-navigation')).toBeVisible()
}

async function seedChannelWorkspace(page: Page, populated: boolean, channelCount = 1) {
  const channel = {
    id: 41,
    name: 'A deliberately long channel identity that must remain inside the Channels pane',
    channel_number: 101,
    channel_group_id: 7,
    streams: [501],
    logo_id: 9,
    _stagedLogoUrl: '/staged-channel-artwork.png',
    tvg_id: 'espn.us',
    epg_data_id: 88,
  }
  const channels = channelCount >= 2
    ? [
        channel,
        {
          ...channel,
          id: 42,
          uuid: 'channel-42',
          name: 'Second fixture channel',
          channel_number: 102,
          streams: [],
          tvg_id: null,
          epg_data_id: null,
          _stagedLogoUrl: undefined,
        },
      ]
    : [channel]
  const stream = {
    id: 501,
    name: 'A deliberately long source stream identity that must ellipsize before inventory actions',
    url: `https://example.invalid/${'very-long-path/'.repeat(12)}playlist.m3u8`,
    channel_group_name: 'Provider Sports',
    m3u_account: 3,
    logo_url: null,
  }
  await page.route(/\/api\/channel-groups(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ id: 7, name: 'Sports' }] : []),
  }))
  await page.route(/\/api\/channels(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      count: populated ? channels.length : 0,
      next: null,
      previous: null,
      results: populated ? channels : [],
    }),
  }))
  await page.route(/\/api\/stream-groups(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ name: 'Provider Sports', count: 1 }] : []),
  }))
  await page.route(/\/api\/streams(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      count: populated ? 1 : 0,
      next: null,
      previous: null,
      results: populated ? [stream] : [],
    }),
  }))
  await page.route(/\/api\/stream-stats\/by-ids(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? {
      501: {
        stream_id: 501,
        stream_name: stream.name,
        resolution: null,
        fps: null,
        video_codec: null,
        audio_codec: null,
        audio_channels: null,
        stream_type: null,
        bitrate: null,
        video_bitrate: null,
        probe_status: 'timeout',
        error_message: 'Probe exceeded the configured 30 second deadline while waiting for the upstream provider response',
        last_probed: null,
        created_at: '2026-07-27T00:00:00Z',
        consecutive_failures: 3,
        is_black_screen: false,
        is_low_fps: false,
      },
    } : {}),
  }))
  await page.route(/\/api\/stream-stats\/probe\/bulk(?:\?|$)/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ probed: 1, results: [] }),
    })
  })
  await page.route(/\/api\/epg\/sources(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ id: 5, name: 'Schedules Direct' }] : []),
  }))
  await page.route(/\/api\/epg\/data(?:\?|$)/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(populated ? [{ id: 88, tvg_id: 'espn.us', name: 'ESPN', epg_source: 5, icon_url: null }] : []),
  }))
}

for (const viewport of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
  test.describe(`operator shell geometry at ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, serviceWorkers: 'block' })

    for (const route of ['channel-manager', 'guide']) {
      test(`${route} clears expanded and collapsed navigation`, async ({ appPage }) => {
        await dismissFirstRunPromptIfPresent(appPage)
        await appPage.getByRole('link', { name: route === 'guide' ? 'Guide' : 'Channel Manager' }).click()

        const expanded = await shellMetrics(appPage)
        expect(expanded).toMatchObject({
          width: 244,
          noSidebarXOverflow: true,
          mainClear: true,
          noDocumentXOverflow: true,
          targetsPractical: true,
          labelsHidden: false,
        })
        await expectContentWithinMain(appPage, route as 'channel-manager' | 'guide')

        await appPage.getByRole('button', { name: 'Collapse navigation' }).click()
        await expect(appPage.locator('.primary-sidebar')).toHaveCSS('width', '68px')
        const collapsed = await shellMetrics(appPage)
        expect(collapsed).toMatchObject({
          width: 68,
          noSidebarXOverflow: true,
          mainClear: true,
          noDocumentXOverflow: true,
          targetsPractical: true,
          labelsHidden: true,
          iconsCentered: true,
        })
        await expectContentWithinMain(appPage, route as 'channel-manager' | 'guide')
      })
    }

    test('all primary route consumers preserve hierarchy and exact healthy controls', async ({ page }) => {
      await openShellWithPipelineFixture(page)
      await dismissFirstRunPromptIfPresent(page)
      for (const consumer of routeConsumers) {
        await page.getByRole('link', { name: consumer.name }).click()
        const settled = await expectSettledRoute(page, consumer)
        await settled.scrollIntoViewIfNeeded()
        expect(await settled.evaluate((element) => {
          const rect = element.getBoundingClientRect()
          const main = document.querySelector<HTMLElement>('#main-content')!.getBoundingClientRect()
          return rect.left >= main.left - 1 && rect.right <= main.right + 1
            && document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
        })).toBe(true)
      }
    })

    test('Channel Manager keeps the deterministic two-pane workspace usable with both navigation widths', async ({ page }, testInfo) => {
      await seedChannelWorkspace(page, true, 2)
      await openShellWithPipelineFixture(
        page,
        200,
        [{ id: 3, name: 'Fixture Provider' }],
        [{ id: 5, name: 'Schedules Direct' }],
      )
      await dismissFirstRunPromptIfPresent(page)

      await expect(page.locator('#main-content h1')).toHaveText('OPERATIONS / CHANNEL MANAGER')
      await expect(page.locator('#main-content h1')).toHaveCount(1)
      await expect(page.getByRole('region', { name: 'Channels' })).toBeVisible()
      await expect(page.getByRole('region', { name: 'Streams' })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Channels', level: 2 })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Streams', level: 2 })).toBeVisible()
      await expect(page.getByLabel('2 channels')).toBeVisible()
      await expect(page.getByLabel('1 total stream')).toBeVisible()
      const separator = page.getByRole('separator', { name: 'Resize Channels and Streams panes' })
      await expect(separator).toBeVisible()

      const separatorBox = await separator.boundingBox()
      if (!separatorBox) throw new Error('splitter has no geometry')
      const initialSplit = Number(await separator.getAttribute('aria-valuenow'))
      await page.mouse.move(separatorBox.x + separatorBox.width / 2, separatorBox.y + separatorBox.height / 2)
      await page.mouse.down()
      await page.mouse.move(separatorBox.x - 80, separatorBox.y + separatorBox.height / 2, { steps: 5 })
      await page.mouse.up()
      const draggedSplit = Number(await separator.getAttribute('aria-valuenow'))
      expect(draggedSplit).toBeGreaterThanOrEqual(35)
      expect(draggedSplit).toBeLessThanOrEqual(70)
      expect(draggedSplit).toBeLessThan(initialSplit)

      const channelSearch = page.getByRole('textbox', { name: 'Search channels' })
      await channelSearch.fill('deliberately long')
      await expect(channelSearch).toHaveValue('deliberately long')
      await page.locator('.channels-pane').getByRole('button', { name: 'Clear search' }).click()

      const streamSearch = page.getByRole('textbox', { name: 'Search streams' })
      await streamSearch.fill('source stream')
      await expect(streamSearch).toHaveValue('source stream')
      await expect(page.getByLabel('1 matching stream')).toBeVisible()
      await page.locator('.streams-pane').getByRole('button', { name: 'Clear search' }).click()

      const providerFilter = page.locator('.streams-pane').getByRole('button', { name: /All Providers/ })
      await providerFilter.click()
      await page.getByRole('checkbox', { name: 'Fixture Provider' }).check()
      await expect(page.locator('.streams-pane').getByRole('button', { name: /1 provider/ })).toBeVisible()
      const groupFilter = page.locator('.streams-pane').getByRole('button', { name: /All Groups/ })
      await groupFilter.press('Enter')
      await page.getByRole('checkbox', { name: 'Provider Sports' }).check()
      await expect(page.getByLabel('1 filtered stream')).toBeVisible()
      await page.getByRole('button', { name: 'Clear all filters' }).click()

      const moreActions = page.locator('.channels-pane').getByRole('button', { name: 'More actions' })
      await moreActions.focus()
      await moreActions.press('Enter')
      const paneMenu = page.getByRole('menu', { name: 'Channel pane actions' })
      await expect(paneMenu).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Channel Profiles' })).toBeFocused()
      await expect(page.getByRole('menuitem', { name: 'Channel Profiles' }).locator('.material-icons')).toHaveText('group')
      await page.keyboard.press('End')
      await expect(page.getByRole('menuitem', { name: 'Export CSV' })).toBeFocused()
      await page.keyboard.press('Home')
      await expect(page.getByRole('menuitem', { name: 'Channel Profiles' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(paneMenu).toHaveCount(0)
      await expect(moreActions).toBeFocused()

      const category = page.getByRole('button', { name: /^Other/ })
      await category.click()
      const providerGroup = page.getByRole('button', { name: /Provider Sports/ })
      await providerGroup.press('Enter')
      const inventoryIdentity = page.locator('.streams-pane .stream-name').filter({
        hasText: 'A deliberately long source stream identity that must ellipsize before inventory actions',
      })
      await expect(inventoryIdentity).toBeVisible()
      await expect(page.locator('.streams-pane .drag-handle')).toHaveCount(0)
      await expect(page.locator('.streams-pane .group-drag-handle')).toHaveCount(0)
      await expect(page.locator('.channels-pane .group-drag-handle')).toHaveCount(0)
      const previewAction = page.locator('.streams-pane').getByRole('button', { name: 'Preview stream in browser' })
      await previewAction.click()
      await page.getByRole('button', { name: 'Close', exact: true }).first().click()
      const copyAction = page.locator('.streams-pane').getByRole('button', { name: 'Copy stream URL' })
      await copyAction.focus()
      await copyAction.press('Enter')
      await expect(page.locator('.streams-pane .copy-feedback')).toBeVisible()
      const inventoryVlc = page.locator('.streams-pane').getByRole('button', { name: 'Open in VLC' })
      await inventoryVlc.focus()
      await expect(inventoryVlc).toBeFocused()
      await expect(inventoryVlc).toHaveCSS('opacity', '1')
      const inventoryPreview = page.locator('.streams-pane').getByRole('button', { name: 'Preview stream in browser' })
      await inventoryPreview.focus()
      await expect(inventoryPreview).toHaveCSS('opacity', '1')
      await inventoryPreview.press('Enter')
      await expect(page.getByRole('button', { name: 'Close', exact: true }).first()).toBeVisible()
      await page.getByRole('button', { name: 'Close', exact: true }).first().click()

      await page.locator('.channels-pane').getByRole('button', { name: /Sports/ }).click()
      await expect(page.locator('.channel-column-headers')).toHaveText(/NumberChannel \/ GuideStreams/)
      await expect(page.locator('.channel-number-col').first()).toHaveText('101')
      await expect(page.locator('.channel-number-col').first()).not.toContainText('#')
      await expect(page.getByText('Schedules Direct – ESPN')).toBeVisible()
      await expect(page.getByLabel('1 stream; failed probe')).toBeVisible()
      const channelArtwork = page.locator('.channel-logo').first()
      await expect(channelArtwork).toHaveAttribute('src', '/persisted-channel-artwork.png')
      const expectChannelColumnsAligned = async () => {
        expect(await page.evaluate(() => {
          const center = (selector: string) => {
            const rect = document.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
            return rect.left + rect.width / 2
          }
          const left = (selector: string) => document.querySelector<HTMLElement>(selector)!.getBoundingClientRect().left
          return {
            number: Math.abs(center('.channel-column-number') - center('.channel-number-col')),
            identity: Math.abs(left('.channel-column-identity') - left('.channel-content')),
            streams: Math.abs(center('.channel-column-streams') - center('.channel-streams-count')),
          }
        })).toEqual({ number: 0, identity: 0, streams: 0 })
      }
      await expectChannelColumnsAligned()
      await expect(page.locator('.channels-pane .channel-drag-handle')).toHaveCount(0)
      await page.getByRole('button', { name: 'Edit Mode' }).click()
      await expectChannelColumnsAligned()
      await expect(channelArtwork).toHaveAttribute('src', '/staged-channel-artwork.png')
      const expectEditContainment = async () => {
        expect(await page.evaluate(() => {
          const paneContained = (selector: string) => {
            const pane = document.querySelector<HTMLElement>(selector)!
            const paneRect = pane.getBoundingClientRect()
            return [...pane.querySelectorAll<HTMLElement>('.channel-item, .stream-item, .inline-stream-item, button')]
              .filter((element) => element.offsetParent !== null)
              .every((element) => {
                const rect = element.getBoundingClientRect()
                return rect.left >= paneRect.left - 1 && rect.right <= paneRect.right + 1
              })
          }
          return {
            document: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            channels: paneContained('.channels-pane'),
            streams: paneContained('.streams-pane'),
          }
        })).toEqual({ document: true, channels: true, streams: true })
      }
      await expectEditContainment()
      await page.getByRole('button', { name: 'Collapse navigation' }).click()
      await expectEditContainment()
      await page.getByRole('button', { name: 'Expand navigation' }).click()
      await expectEditContainment()
      await expect(page.getByLabel('Drag channel group Sports to reorder')).toBeVisible()
      await expect(page.getByLabel(/^Drag channel A deliberately long channel identity .* to reorder$/)).toBeVisible()
      await expect(page.getByLabel('Drag stream group Provider Sports to Channels pane to create channels')).toBeVisible()
      await expect(page.getByLabel(/Drag inventory stream .* to assign it to a channel/)).toBeVisible()

      const inventoryDrag = page.getByLabel(/Drag inventory stream .* to assign it to a channel/)
      await inventoryDrag.focus()
      await inventoryDrag.press('Enter')
      const channelDestinations = page.getByRole('menu', { name: 'Choose channel destination' })
      await expect(channelDestinations.getByRole('menuitem', { name: /A deliberately long channel identity/ })).toBeFocused()
      await page.keyboard.press('ArrowDown')
      await expect(channelDestinations.getByRole('menuitem', { name: 'Second fixture channel' })).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(page.locator('.keyboard-drag-status')).toContainText(/Dropped inventory stream .* on channel Second fixture channel/)

      const groupDrag = page.getByLabel('Drag stream group Provider Sports to Channels pane to create channels')
      await groupDrag.focus()
      await groupDrag.press('Enter')
      const groupDestinations = page.getByRole('menu', { name: 'Choose channel group destination' })
      await expect(groupDestinations.getByRole('menuitem', { name: 'Sports' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(groupDestinations).toHaveCount(0)
      await expect(groupDrag).toBeFocused()
      await groupDrag.press('Enter')
      await expect(page.getByRole('menu', { name: 'Choose channel group destination' })
        .getByRole('menuitem', { name: 'Sports' })).toBeFocused()
      await page.keyboard.press('Enter')
      const createFromGroupDialog = page.getByRole('dialog', {
        name: 'Create Channels from "Provider Sports"',
      })
      await expect(createFromGroupDialog).toBeVisible()
      await expect(createFromGroupDialog.getByText('"Sports"')).toBeVisible()
      await expect(createFromGroupDialog.locator('input:focus')).toHaveCount(1)
      await createFromGroupDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
      await expect(groupDrag).toBeFocused()

      await expect(page.getByRole('toolbar', { name: 'Selection actions' })).toHaveCount(0)
      await page.getByRole('checkbox', { name: /Select channel A deliberately long channel identity/ }).click()
      const selectionBar = page.getByRole('toolbar', { name: 'Selection actions' })
      await expect(selectionBar).toBeVisible()
      await expect(selectionBar.getByRole('status', { name: '1 channel selected' })).toBeVisible()
      expect(await selectionBar.getByRole('button').allTextContents()).toEqual([
        'deleteDelete',
        'speedProbe',
        'manage_searchFind Duplicates',
        'tagRenumber',
        'live_tvAssign EPG',
        'more_vertMore',
        'closeClear',
      ])
      await expect(selectionBar.getByRole('button', { name: 'Merge' })).toHaveCount(0)
      await page.getByRole('checkbox', { name: 'Select channel Second fixture channel' }).click()
      await expect(selectionBar.getByRole('status', { name: '2 channels selected' })).toBeVisible()
      await expect(selectionBar.getByRole('button', { name: 'Merge' })).toBeVisible()
      const probeSelected = selectionBar.getByRole('button', { name: 'Probe' })
      await probeSelected.click()
      const probingSelected = selectionBar.getByRole('button', { name: 'Probing…' })
      await expect(probingSelected).toBeDisabled()
      await expect(probingSelected.locator('.material-icons')).toHaveText('sync')
      await expect(selectionBar.getByRole('button', { name: 'Probe' })).toBeEnabled()

      await page.clock.install()
      const assignEpg = selectionBar.getByRole('button', { name: 'Assign EPG' })
      await assignEpg.click()
      await expect(assignEpg).toBeDisabled()
      await expect(assignEpg.locator('.material-icons')).toHaveText('sync')
      await page.clock.runFor(60)
      await expect(page.getByRole('heading', { name: 'Bulk EPG Assignment' })).toBeVisible()
      await page.getByRole('button', { name: 'Cancel', exact: true }).click()
      await page.clock.resume()
      const selectionMore = selectionBar.getByRole('button', { name: 'More selection actions' })
      await selectionMore.focus()
      await selectionMore.press('Enter')
      const selectionMenu = page.getByRole('menu', { name: 'More selection actions' })
      await expect(selectionMenu).toBeVisible()
      await expect(selectionMenu.getByRole('menuitem', { name: /Move to group/ })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(selectionMenu).toHaveCount(0)
      await expect(selectionMore).toBeFocused()
      await selectionBar.getByRole('button', { name: 'Clear selection' }).click()
      await expect(selectionBar).toHaveCount(0)
      await moreActions.focus()
      await moreActions.press('Enter')
      const editPaneMenu = page.getByRole('menu', { name: 'Channel pane actions' })
      for (const name of [
        'Channel Profiles', 'Hidden Groups', 'Sort All Streams',
        'Renumber All Groups', 'CSV Template', 'Export CSV', 'Import CSV',
      ]) {
        await expect(editPaneMenu.getByRole('menuitem', { name })).toBeVisible()
      }
      await page.keyboard.press('ArrowDown')
      await expect(editPaneMenu.getByRole('menuitem', { name: 'Hidden Groups' })).toBeFocused()
      await page.keyboard.press('ArrowDown')
      const sortAll = editPaneMenu.getByRole('menuitem', { name: 'Sort All Streams' })
      await expect(sortAll).toBeFocused()
      await page.keyboard.press('ArrowRight')
      const sortMenu = page.getByRole('menu', { name: 'Sort all streams' })
      await expect(sortMenu.getByRole('menuitem', { name: 'Smart Sort' })).toBeFocused()
      await page.keyboard.press('End')
      await expect(sortMenu.getByRole('menuitem', { name: 'By Framerate' })).toBeFocused()
      await page.keyboard.press('ArrowLeft')
      await expect(sortAll).toBeFocused()
      await page.keyboard.press('ArrowRight')
      await expect(sortMenu.getByRole('menuitem', { name: 'Smart Sort' })).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(editPaneMenu).toHaveCount(0)
      await expect(moreActions).toBeFocused()
      await moreActions.press('Enter')
      await expect(page.getByRole('menu', { name: 'Channel pane actions' }).getByRole('menuitem', { name: 'Channel Profiles' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(editPaneMenu).toHaveCount(0)
      await expect(moreActions).toBeFocused()
      await page.getByRole('button', { name: 'Done' }).click()
      await expect(page.getByRole('heading', { name: 'Exit Edit Mode' })).toBeVisible()
      await page.getByRole('button', { name: 'Discard', exact: true }).click()
      await expect(channelArtwork).toHaveAttribute('src', '/persisted-channel-artwork.png')
      await expectEditContainment()
      await expect(page.locator('.channels-pane .channel-drag-handle')).toHaveCount(0)
      await expect(page.locator('.channels-pane .group-drag-handle')).toHaveCount(0)
      await expect(page.locator('.streams-pane .drag-handle')).toHaveCount(0)
      await expect(page.locator('.streams-pane .group-drag-handle')).toHaveCount(0)
      const channelActions = page.getByRole('button', { name: 'Channel actions' })
      await channelActions.focus()
      await channelActions.press('Enter')
      await expect(page.getByRole('menu', { name: 'Channel actions' })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Probe Channel' })).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(page.getByRole('menu', { name: 'Channel actions' })).toHaveCount(0)
      await expect(channelActions).toBeFocused()
      const channelIdentity = page.getByText('A deliberately long channel identity that must remain inside the Channels pane')
      await channelIdentity.click()
      await expect(page.locator('.inline-stream-name').filter({ hasText: 'A deliberately long source stream identity' })).toBeVisible()
      const timeoutWarning = page.getByLabel(
        'Probe timeout. Probe exceeded the configured 30 second deadline while waiting for the upstream provider response. 3 of 3 strikes.',
      )
      await expect(timeoutWarning).toHaveText(/Probe timeout\s*•\s*3\/3/)
      const assignedPreview = page.locator('.channels-pane').getByRole('button', { name: 'Preview stream in browser' })
      await assignedPreview.focus()
      await expect(assignedPreview).toHaveCSS('opacity', '1')
      await assignedPreview.press('Enter')
      await expect(page.getByRole('button', { name: 'Close', exact: true }).first()).toBeVisible()
      await page.getByRole('button', { name: 'Close', exact: true }).first().click()

      expect(await inventoryIdentity.evaluate((identity) => {
        const info = identity.closest<HTMLElement>('.stream-info')!
        const row = identity.closest<HTMLElement>('.stream-item')!
        const actionsGroup = row.querySelector<HTMLElement>('.stream-actions')!
        const actions = [...actionsGroup.querySelectorAll<HTMLElement>('button')]
        const infoRect = info.getBoundingClientRect()
        const direct = [...row.children]
        const identityStyle = getComputedStyle(identity)
        return {
          strictDomOrder: direct.indexOf(row.querySelector('.stream-artwork-slot')!) <
              direct.indexOf(info)
            && direct.indexOf(info) < direct.indexOf(actionsGroup)
            && direct[direct.length - 1] === actionsGroup,
          stableBlankArtwork: row.querySelector('.stream-artwork-slot')?.children.length === 0,
          usableIdentityWidth: infoRect.width >= 80,
          ellipsisContract: identityStyle.overflow === 'hidden'
            && identityStyle.textOverflow === 'ellipsis'
            && identityStyle.whiteSpace === 'nowrap',
          actionsVisible: actions.every((action) => action.getBoundingClientRect().right <= row.getBoundingClientRect().right + 1),
          noOverlap: actions.every((action) => action.getBoundingClientRect().left >= infoRect.right - 1),
        }
      })).toEqual({
        strictDomOrder: true,
        stableBlankArtwork: true,
        usableIdentityWidth: true,
        ellipsisContract: true,
        actionsVisible: true,
        noOverlap: true,
      })
      expect(await page.evaluate(() => {
        const pane = document.querySelector<HTMLElement>('.streams-pane')!.getBoundingClientRect()
        const streamUrl = document.querySelector<HTMLElement>('.streams-pane .stream-url')!.getBoundingClientRect()
        const channel = document.querySelector<HTMLElement>('.channels-pane .channel-name')!.getBoundingClientRect()
        const channelPane = document.querySelector<HTMLElement>('.channels-pane')!.getBoundingClientRect()
        const inlineName = document.querySelector<HTMLElement>('.inline-stream-name')!.getBoundingClientRect()
        const inlineRow = document.querySelector<HTMLElement>('.inline-stream-item')!
        const inlineInfo = inlineRow.querySelector<HTMLElement>('.inline-stream-info')!.getBoundingClientRect()
        const inlineActions = inlineRow.querySelector<HTMLElement>('.inline-stream-actions')!.getBoundingClientRect()
        const warning = inlineRow.querySelector<HTMLElement>('.probe-warning-summary')!.getBoundingClientRect()
        const inlineUrl = inlineRow.querySelector<HTMLElement>('.inline-stream-url')!.getBoundingClientRect()
        const provider = inlineRow.querySelector<HTMLElement>('.inline-stream-provider')!.getBoundingClientRect()
        return {
          urlContained: streamUrl.left >= pane.left && streamUrl.right <= pane.right + 1,
          channelContained: channel.left >= channelPane.left && channel.right <= channelPane.right + 1,
          inlineContained: inlineName.left >= channelPane.left && inlineName.right <= channelPane.right + 1,
          inlineIdentityUsable: inlineInfo.width >= 80,
          inlineActionsFixed: inlineActions.left >= inlineInfo.right - 1
            && inlineActions.right <= inlineRow.getBoundingClientRect().right + 1,
          timeoutDetailsUsable: warning.width >= 80 && inlineUrl.width >= 48 && provider.width >= 48,
          timeoutDetailsBeforeActions: [warning, inlineUrl, provider]
            .every((rect) => rect.right <= inlineActions.left + 1),
        }
      })).toEqual({
        urlContained: true,
        channelContained: true,
        inlineContained: true,
        inlineIdentityUsable: true,
        inlineActionsFixed: true,
        timeoutDetailsUsable: true,
        timeoutDetailsBeforeActions: true,
      })

      for (const collapsed of [false, true]) {
        if (collapsed) await page.getByRole('button', { name: 'Collapse navigation' }).click()
        const geometry = await page.evaluate(() => {
          const workspace = document.querySelector<HTMLElement>('.split-pane')!
          const channels = document.querySelector<HTMLElement>('.split-pane-left')!
          const streams = document.querySelector<HTMLElement>('.split-pane-right')!
          const main = document.querySelector<HTMLElement>('#main-content')!
          const mainRect = main.getBoundingClientRect()
          return {
            noDocumentOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
            workspaceInsideMain: workspace.getBoundingClientRect().left >= mainRect.left
              && workspace.getBoundingClientRect().right <= mainRect.right + 1,
            channelsUsable: channels.clientWidth >= 300,
            streamsUsable: streams.clientWidth >= 260,
          }
        })
        expect(geometry).toEqual({
          noDocumentOverflow: true,
          workspaceInsideMain: true,
          channelsUsable: true,
          streamsUsable: true,
        })
        await testInfo.attach(
          `channel-manager-${viewport.width}x${viewport.height}-${collapsed ? 'collapsed' : 'expanded'}`,
          { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' },
        )
      }
    })
  })
}

test.describe('Channel Manager dnd-kit keyboard path', () => {
  test.use({ viewport: { width: 1280, height: 720 }, serviceWorkers: 'block' })

  test('moves a channel drag overlay through a keyboard reorder destination', async ({ page }) => {
    await seedChannelWorkspace(page, true, 2)
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('button', { name: 'Edit Mode' }).click()
    await page.locator('.channels-pane .group-toggle-btn').filter({ hasText: 'Sports' }).click()

    const channelDrag = page.getByLabel(/^Drag channel A deliberately long channel identity .* to reorder$/)
    await channelDrag.focus()
    await channelDrag.press('Space')
    await expect(page.locator('.drag-overlay-item')).toBeVisible()
    await page.keyboard.press('ArrowDown')
    await expect(page.getByRole('status').filter({
      hasText: /Draggable item 41 was moved over droppable area/,
    })).toHaveCount(1)
  })
})

test.describe('operator shell at 200% equivalent', () => {
  test.use({ viewport: { width: 640, height: 360 }, serviceWorkers: 'block' })

  test('all primary routes settle with their exact control and remain horizontally usable', async ({ page }) => {
    await openShellWithPipelineFixture(page)
    await dismissFirstRunPromptIfPresent(page)
    for (const consumer of routeConsumers) {
      await page.getByRole('link', { name: consumer.name }).click()
      const settled = await expectSettledRoute(page, consumer)
      await settled.scrollIntoViewIfNeeded()
      const overflow = await page.evaluate(() => ({
        fits: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
        offenders: [...document.querySelectorAll<HTMLElement>('body *')]
          .filter((element) => element.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
          .slice(0, 8)
          .map((element) => `${element.tagName}.${element.className}`),
      }))
      expect(overflow.fits, `${consumer.name} must not cause document-level horizontal overflow; internal overflow: ${overflow.offenders.join(', ')}`).toBe(true)
    }
  })
})

test.describe('operator shell navigation behavior', () => {
  test.use({ serviceWorkers: 'block' })

  test('Channel Manager distinguishes loading and true-empty inventory', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    await page.route(/\/api\/channels(?:\?|$)/, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 400))
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
      })
    })
    await openShellWithPipelineFixture(page)
    await expect(page.getByText('Loading channels...')).toBeVisible()
    await expect(page.getByLabel('0 channels')).toBeVisible()
    await expect(page.getByLabel('0 total streams')).toBeVisible()
    await expect(page.getByText('No channels are configured.')).toBeVisible()
    await expect(page.getByText('No source streams are available.')).toBeVisible()
    await expect(page.getByRole('region', { name: 'Channels' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Streams' })).toBeVisible()
  })

  test('Channel Manager retries the exact failed stream search and recovers its matching total', async ({ page }) => {
    await seedChannelWorkspace(page, true)
    let searchAttempts = 0
    await page.route(/\/api\/streams(?:\?|$)/, (route) => {
      const url = new URL(route.request().url())
      if (url.searchParams.get('search') !== 'needle') return route.fallback()
      searchAttempts += 1
      return route.fulfill({
        status: searchAttempts === 1 ? 503 : 200,
        contentType: 'application/json',
        body: searchAttempts === 1
          ? JSON.stringify({ detail: 'Search unavailable' })
          : JSON.stringify({ count: 650, next: null, previous: null, results: [] }),
      })
    })
    await openShellWithPipelineFixture(page)
    await page.getByRole('textbox', { name: 'Search streams' }).fill('needle')
    await expect(page.getByText('Streams unavailable')).toBeVisible()
    await page.getByRole('button', { name: 'Retry loading streams' }).click()
    await expect(page.getByLabel('650 matching streams')).toBeVisible()
    expect(searchAttempts).toBe(2)
  })

  test('Channel Manager treats a lazy stream-group 403 as protected pane denial', async ({ page }) => {
    await seedChannelWorkspace(page, true)
    await page.route(/\/api\/streams(?:\?|$)/, (route) => {
      const url = new URL(route.request().url())
      if (url.searchParams.get('channel_group_name') !== 'Provider Sports') return route.fallback()
      return route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Forbidden' }),
      })
    })
    await openShellWithPipelineFixture(page)
    await page.getByRole('button', { name: /^Other/ }).click()
    await page.getByRole('button', { name: /Provider Sports/ }).click()
    await expect(page.getByText('Streams require administrator access')).toBeVisible()
    await expect(page.locator('.channels-pane')).toHaveCount(0)
    await expect(page.locator('.streams-pane')).toHaveCount(0)
  })

  test('Channel Manager retains group A while exact-query retry recovers failed group B', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    await page.route(/\/api\/stream-groups(?:\?|$)/, (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { name: 'Provider Group A', count: 1 },
        { name: 'Provider Group B', count: 1 },
      ]),
    }))
    let groupBAttempts = 0
    const seenGroupBQueries: string[] = []
    await page.route(/\/api\/streams(?:\?|$)/, (route) => {
      const url = new URL(route.request().url())
      const group = url.searchParams.get('channel_group_name')
      if (!group) return route.fallback()
      const result = {
        id: group === 'Provider Group A' ? 701 : 702,
        name: `${group} retained stream`,
        url: `https://example.invalid/${group}`,
        m3u_account: 3,
        channel_group: null,
        channel_group_name: group,
        is_custom: false,
      }
      if (group === 'Provider Group B') {
        groupBAttempts += 1
        seenGroupBQueries.push(url.search)
        if (groupBAttempts === 1) {
          return route.fulfill({
            status: 503,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Group B unavailable' }),
          })
        }
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ count: 1, next: null, previous: null, results: [result] }),
      })
    })
    await openShellWithPipelineFixture(page, 200, [{ id: 3, name: 'Fixture Provider' }])
    await page.locator('.streams-pane').getByRole('button', { name: /All Providers/ }).click()
    await page.getByRole('checkbox', { name: 'Fixture Provider' }).check()
    await page.getByRole('button', { name: /^Other/ }).click()
    await page.getByRole('button', { name: /Provider Group A/ }).click()
    await expect(page.getByText('Provider Group A retained stream')).toBeVisible()
    await page.getByRole('button', { name: /Provider Group B/ }).click()
    await expect(page.getByText('Streams unavailable — showing previously loaded data')).toBeVisible()
    await expect(page.getByText('Provider Group A retained stream')).toBeVisible()
    await page.getByRole('button', { name: 'Retry loading streams' }).click()
    await expect(page.getByText('Provider Group B retained stream')).toBeVisible()
    expect(groupBAttempts).toBe(2)
    for (const query of seenGroupBQueries) {
      expect(query).toContain('channel_group_name=Provider+Group+B')
      expect(query).toContain('m3u_account=3')
    }
  })

  test('Channel Manager gives a named scoped error and recovers through Retry', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    let attempts = 0
    await page.route(/\/api\/channels(?:\?|$)/, (route) => {
      attempts += 1
      return route.fulfill({
        status: attempts === 1 ? 503 : 200,
        contentType: 'application/json',
        body: attempts === 1
          ? JSON.stringify({ detail: 'Channels source unavailable' })
          : JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
      })
    })
    await openShellWithPipelineFixture(page)
    await expect(page.getByText('Channels unavailable')).toBeVisible()
    await page.getByRole('button', { name: 'Retry loading channels' }).click()
    await expect(page.getByText('Loading channels...')).toBeVisible()
    await expect(page.getByLabel('0 channels')).toBeVisible()
    expect(attempts).toBeGreaterThanOrEqual(2)
  })

  test('Channel Manager permission state exposes no protected panes or actions', async ({ page }) => {
    await seedChannelWorkspace(page, false)
    await page.route(/\/api\/channels(?:\?|$)/, (route) => route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Forbidden' }),
    }))
    await openShellWithPipelineFixture(page)
    await expect(page.getByText('Channels require administrator access')).toBeVisible()
    await expect(page.getByText('Streams require administrator access')).toBeVisible()
    await expect(page.locator('.channels-pane')).toHaveCount(0)
    await expect(page.locator('.streams-pane')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Edit Mode' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Retry loading/i })).toHaveCount(0)
  })

  test('Channel Pipeline exposes deterministic named recovery when rule loading fails', async ({ page }) => {
    await openShellWithPipelineFixture(page, 503)
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('link', { name: 'Channel Pipeline' }).click()
    await expect(page.getByText('Failed to load channel pipeline rules', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create rule' })).toHaveCount(0)
  })

  test('protected M3U source data explains permission denial without exposing data or actions', async ({ page }) => {
    await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        configured: true,
        default_channel_profile_ids: [],
        stream_sort_priority: [],
        stream_sort_enabled: {},
        m3u_account_priorities: {},
        custom_network_prefixes: [],
        hide_auto_sync_groups: false,
      }),
    }))
    await page.route(/\/api\/m3u\/server-groups(?:\/|\?|$)/, (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }))
    await page.route(/\/api\/providers(?:\/|\?|$)/, (route) => route.fulfill({
      status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'Administrator access required' }),
    }))
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.tab-navigation')).toBeVisible()
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('link', { name: 'M3U Manager' }).click()
    await expect(page.locator('.route-page-header').getByText('Provider accounts require administrator access', { exact: true })).toBeVisible()
    await expect(page.getByText(/0 provider accounts/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Add M3U Account' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Retry loading provider accounts/i })).toHaveCount(0)
  })

  test('M3U source loading announces failure and recovers through its scoped Retry', async ({ page }) => {
    let providerMode: 'healthy' | 'error' = 'healthy'
    await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        configured: true,
        default_channel_profile_ids: [],
        stream_sort_priority: [],
        stream_sort_enabled: {},
        m3u_account_priorities: {},
        custom_network_prefixes: [],
        hide_auto_sync_groups: false,
      }),
    }))
    await page.route(/\/api\/m3u\/server-groups(?:\/|\?|$)/, (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: '[]',
    }))
    await page.route(/\/api\/providers(?:\/|\?|$)/, (route) => route.fulfill({
      status: providerMode === 'error' ? 503 : 200,
      contentType: 'application/json',
      body: providerMode === 'error' ? JSON.stringify({ detail: 'Temporarily unavailable' }) : '[]',
    }))

    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.tab-navigation')).toBeVisible()
    await dismissFirstRunPromptIfPresent(page)
    await expect(page.locator('#main-content .tab-loading')).toHaveCount(0)

    providerMode = 'error'
    await page.getByRole('link', { name: 'M3U Manager' }).click()
    await expect(page.getByRole('status', { name: 'Provider accounts unavailable' })).toBeVisible()
    const retry = page.getByRole('button', { name: 'Retry loading provider accounts' })
    await expect(retry).toBeVisible()

    providerMode = 'healthy'
    await retry.click()
    await expect(page.getByRole('status', { name: 'Provider accounts loaded' })).toBeVisible()
    await expect(page.locator('.route-page-header').getByText('0 provider accounts', { exact: true })).toBeVisible()
    await expect(page.locator('.route-page-header').getByRole('button', { name: 'Add M3U Account' })).toBeVisible()
  })

  test('a later Logo permission denial removes cached protected content and actions', async ({ page }) => {
    let logoPermissionDenied = false
    await page.route(/\/api\/settings(?:\/|\?|$)/, (route) => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        configured: true,
        default_channel_profile_ids: [],
        stream_sort_priority: [],
        stream_sort_enabled: {},
        m3u_account_priorities: {},
        custom_network_prefixes: [],
        hide_auto_sync_groups: false,
      }),
    }))
    await page.route(/\/api\/channels\/logos(?:\/|\?|$)/, (route) => route.fulfill({
      status: logoPermissionDenied ? 403 : 200,
      contentType: 'application/json',
      body: logoPermissionDenied
        ? JSON.stringify({ detail: 'Administrator access required' })
        : JSON.stringify({
            count: 1,
            next: null,
            previous: null,
            results: [{
              id: 41,
              name: 'Private Sports Logo',
              url: 'https://example.test/private-sports.png',
              cache_url: '',
              channel_count: 1,
              is_used: true,
            }],
          }),
    }))

    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.tab-navigation')).toBeVisible()
    await dismissFirstRunPromptIfPresent(page)
    await page.getByRole('link', { name: 'Logo Manager' }).click()
    await expect(page.getByText('Private Sports Logo', { exact: true })).toBeVisible()

    logoPermissionDenied = true
    await page.getByPlaceholder('Search logos...').fill('private')

    await expect(page.getByRole('status', { name: 'Logos access denied' })).toBeVisible()
    await expect(page.getByText('Private Sports Logo', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Add Logo' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Retry loading logos/i })).toHaveCount(0)
  })

  test('staged contextual navigation keeps editing or discards before landing on the exact settings page', async ({ page }) => {
    await page.addInitScript(() => localStorage.clear())
    await page.route(/\/api\/settings(?:\/|\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          configured: true,
          default_channel_profile_ids: [],
          stream_sort_priority: [],
          stream_sort_enabled: {},
          m3u_account_priorities: {},
          custom_network_prefixes: [],
          hide_auto_sync_groups: false,
          theme: 'dark',
          date_format: 'en-US',
        }),
      })
    })
    await page.route(/\/api\/channel-groups(?:\/|\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 91, name: 'Seeded Group', channel_count: 1 }]),
      })
    })
    await page.route(/\/api\/channels(?:\/|\?|$)/, async (route) => {
      if (route.request().method() !== 'GET') {
        await route.continue()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          count: 1,
          next: null,
          previous: null,
          results: [{
            id: 701,
            channel_number: 7,
            name: 'Seeded News',
            channel_group_id: 91,
            tvg_id: null,
            tvc_guide_stationid: null,
            epg_data_id: null,
            streams: [],
            stream_profile_id: null,
            uuid: 'operator-shell-seeded-channel',
            logo_id: null,
            auto_created: false,
            auto_created_by: null,
            auto_created_by_name: null,
          }],
        }),
      })
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.tab-navigation')).toBeVisible()
    await dismissFirstRunPromptIfPresent(page)
    await page.getByText('Seeded Group', { exact: true }).click()
    await expect(page.locator('.channel-item', { hasText: 'Seeded News' })).toBeVisible()

    await page.getByRole('button', { name: 'Edit Mode' }).click()
    const channelName = page.locator('.channel-item', { hasText: 'Seeded News' }).locator('.channel-name')
    await channelName.dblclick()
    const nameInput = page.locator('.channel-name-input')
    await nameInput.fill('Seeded News Updated')
    await nameInput.press('Enter')
    await expect(page.getByText('1 change', { exact: true })).toBeVisible()

    await page.getByRole('link', { name: 'Channel default settings' }).click()
    await expect(page.getByRole('heading', { name: 'Exit Edit Mode' })).toBeVisible()
    await page.getByRole('button', { name: 'Keep Editing' }).click()
    await expect(page).toHaveURL(/#channel-manager$/)
    await expect(page.getByText('1 change', { exact: true })).toBeVisible()
    await expect(page.locator('.channel-item', { hasText: 'Seeded News Updated' })).toBeVisible()

    await page.getByRole('link', { name: 'Channel default settings' }).click()
    await page.getByRole('button', { name: 'Discard' }).click()
    await expect(page).toHaveURL(/#settings\/channel-defaults$/)
    await expect(page.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS')
    await expect(page.getByRole('heading', { name: 'Exit Edit Mode' })).toHaveCount(0)
  })

  test('enabled route links retain the browser new-tab affordance', async ({ appPage, context }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const [newTab] = await Promise.all([
      context.waitForEvent('page'),
      appPage.getByRole('link', { name: 'Guide' }).click({ modifiers: ['Control'] }),
    ])
    await newTab.waitForLoadState('domcontentloaded')
    await expect(newTab).toHaveURL(/#guide$/)
    await newTab.close()
    await expect(appPage).toHaveURL(/#channel-manager$/)
  })

  test('all primary routes are real links and keyboard reachable', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const expected = [
      ['Dashboard', '#dashboard'], ['Channel Manager', '#channel-manager'], ['Guide', '#guide'],
      ['M3U Manager', '#m3u-manager'], ['EPG Manager', '#epg-manager'], ['Logo Manager', '#logo-manager'],
      ['Channel Pipeline', '#channel-pipeline'], ['M3U Changes', '#m3u-changes'], ['Stats', '#stats'],
      ['Journal', '#journal'], ['Settings', '#settings'],
    ]
    for (const [name, hash] of expected) {
      const link = appPage.getByRole('link', { name })
      await expect(link).toHaveAttribute('href', hash)
      await link.focus()
      await expect(link).toBeFocused()
      await appPage.keyboard.press('Enter')
      await expect(appPage).toHaveURL(new RegExp(`${hash.replace('#', '#')}$`))
      await expect(appPage.locator('#main-content h1')).toBeFocused()
      await expect(appPage.locator('#main-content h1')).toHaveText(new RegExp(` / ${name.toUpperCase()}$`))
      await expect(appPage.locator('#main-content h1')).toHaveCount(1)
      await expect(appPage).toHaveTitle(`${name} | Enhanced Channel Manager`)
    }
  })

  test('contextual settings links use stable current hashes', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const link = appPage.getByRole('link', { name: 'Channel default settings' })
    await expect(link).toHaveAttribute('href', '#settings/channel-defaults')
    await link.click()
    await expect(appPage).toHaveURL(/#settings\/channel-defaults$/)
    await expect(appPage.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS')
  })

  test('contextual settings navigation cleanly exits an edit session with no staged changes', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    await appPage.getByRole('button', { name: 'Edit Mode' }).click()
    await expect(appPage.getByRole('button', { name: 'Done' })).toBeVisible()
    await appPage.getByRole('link', { name: 'Channel default settings' }).click()
    await expect(appPage).toHaveURL(/#settings\/channel-defaults$/)
    await expect(appPage.locator('#main-content h1')).toHaveText('SYSTEM / SETTINGS')
    await expect(appPage.getByRole('button', { name: 'Done' })).toHaveCount(0)
    await appPage.getByRole('link', { name: 'Channel Manager' }).click()
    await expect(appPage.getByRole('button', { name: 'Edit Mode' })).toBeVisible()
  })

  test('the Channel Manager primary action belongs to its page header', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    const action = appPage.locator('.route-page-header .enter-edit-mode-btn')
    await expect(action).toBeVisible()
    await expect(appPage.locator('header.header .enter-edit-mode-btn')).toHaveCount(0)
    const followsHeading = await appPage.evaluate(() => {
      const heading = document.querySelector('#main-content h1')
      const button = document.querySelector('.route-page-header .enter-edit-mode-btn')
      return Boolean(
        heading
        && button
        && (heading.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING),
      )
    })
    expect(followsHeading).toBe(true)
  })

  test('skip link focuses main without changing a non-default route or history', async ({ appPage }) => {
    await appPage.goto('/#guide', { waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    const before = await appPage.evaluate(() => ({ href: location.href, length: history.length }))
    await appPage.keyboard.press('Tab')
    await expect(appPage.getByRole('link', { name: 'Skip to main content' })).toBeFocused()
    await appPage.keyboard.press('Enter')
    await expect(appPage.locator('#main-content')).toBeFocused()
    expect(await appPage.evaluate(() => ({ href: location.href, length: history.length }))).toEqual(before)
  })

  test('persists collapse and safely restores icon-only mode', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    await appPage.getByRole('button', { name: 'Collapse navigation' }).click()
    await appPage.reload({ waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    await expect(appPage.locator('.primary-sidebar')).toHaveClass(/is-collapsed/)
    await expect(appPage.getByRole('button', { name: 'Expand navigation' })).toBeVisible()
  })

  test('canonicalizes aliases and does not hijack focus on browser history', async ({ appPage }) => {
    await appPage.goto('/#auto-creation', { waitUntil: 'domcontentloaded' })
    await dismissFirstRunPromptIfPresent(appPage)
    await expect(appPage).toHaveURL(/#channel-pipeline$/)
    await appPage.getByRole('link', { name: 'Stats' }).click()
    await appPage.getByRole('link', { name: 'Journal' }).click()
    await appPage.getByRole('button', { name: 'Notifications' }).focus()
    await appPage.goBack()
    await expect(appPage.getByRole('link', { name: 'Stats' })).toHaveAttribute('aria-current', 'page')
    await expect(appPage.getByRole('button', { name: 'Notifications' })).toBeFocused()
    await appPage.goForward()
    await expect(appPage.getByRole('link', { name: 'Journal' })).toHaveAttribute('aria-current', 'page')
  })

  test('remains reachable with reduced motion and at 200% zoom', async ({ appPage }) => {
    await dismissFirstRunPromptIfPresent(appPage)
    await appPage.emulateMedia({ reducedMotion: 'reduce' })
    await expect(appPage.locator('.primary-sidebar')).toHaveCSS('transition-duration', '0s')
    // A 1280x720 browser at 200% zoom exposes a 640x360 CSS layout viewport.
    await appPage.setViewportSize({ width: 640, height: 360 })
    for (const link of await appPage.getByRole('navigation', { name: 'Primary' }).getByRole('link').all()) {
      await link.scrollIntoViewIfNeeded()
      await expect(link).toBeVisible()
      await link.focus()
      await expect(link).toBeFocused()
    }
    const metrics = await shellMetrics(appPage)
    expect(metrics.noSidebarXOverflow).toBe(true)
    expect(metrics.noDocumentXOverflow).toBe(true)

    await appPage.getByRole('link', { name: 'Channel Manager' }).press('Enter')
    await expectContentWithinMain(appPage, 'channel-manager')
    await appPage.getByRole('link', { name: 'Guide' }).press('Enter')
    await expectContentWithinMain(appPage, 'guide')
  })
})
