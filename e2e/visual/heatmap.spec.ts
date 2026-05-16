/**
 * Visual regression — Heatmap component (bd-lln2x).
 *
 * Why this test exists:
 *
 *   - bd-yteek shipped a column-label rotation that worked in jsdom
 *     unit tests but rendered incorrectly in real browsers (text
 *     projected onto cells instead of into the label band). bd-wdpve
 *     fixed it. The PO observed the bug visually on dev and explicitly
 *     asked for a screenshot regression test before merge.
 *
 *   - The unit test in `Heatmap.test.tsx` locks the rotation
 *     ATTRIBUTE (rotate(45) vs rotate(-45)). This test locks the
 *     RENDERED PIXELS so any future regression that breaks the visual
 *     output without changing the attribute (e.g., wrong textAnchor,
 *     wrong COLUMN_LABEL_HEIGHT, CSS that hides labels) still trips a
 *     gate.
 *
 * How it works:
 *
 *   - The fixture page (`frontend/visual-fixture-heatmap.html` +
 *     `frontend/src/visual-fixture/heatmap-fixture.tsx`) mounts the
 *     Heatmap with a deterministic dataset modelled on the GH-59
 *     Stats v2 Providers panel — same shape that exposed bd-wdpve.
 *
 *   - Playwright loads the fixture from the Vite dev server,
 *     waits for the heatmap root to mount, and screenshots the
 *     `[data-testid="heatmap-root"]` element.
 *
 *   - The baseline PNG lives in
 *     `e2e/visual/__screenshots__/heatmap.spec.ts/heatmap-providers-panel.png`.
 *     To update intentionally:
 *       npx playwright test --config=playwright.visual.config.ts \\
 *         --update-snapshots
 *
 * Tolerance:
 *
 *   - maxDiffPixelRatio: 0.01 (1%) — defined in
 *     `playwright.visual.config.ts`. Catches the bd-wdpve-class
 *     regression (~5-15% of pixels affected) without flaking on
 *     cross-OS font hinting.
 */
import { test, expect } from '@playwright/test'

test.describe('Heatmap visual regression', () => {
  test('renders the Stats v2 Providers heatmap fixture identically to the baseline', async ({ page }) => {
    // Load the fixture page directly — no auth, no API calls, no
    // app shell. The fixture mounts only the Heatmap with hardcoded
    // data so the rendered pixels are deterministic.
    await page.goto('/visual-fixture-heatmap.html', {
      waitUntil: 'networkidle',
    })

    // Wait for React to finish mounting and the SVG to attach.
    const heatmapRoot = page.locator('[data-testid="heatmap-root"]')
    await heatmapRoot.waitFor({ state: 'visible', timeout: 15_000 })

    // Belt-and-suspenders: the Material Icons font link in the main
    // index.html is intentionally absent here, but give layout one
    // animation frame to settle so any deferred font metric
    // recalculation is done before the screenshot.
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(() => resolve(null))),
    )

    await expect(heatmapRoot).toHaveScreenshot('heatmap-providers-panel.png')
  })
})
