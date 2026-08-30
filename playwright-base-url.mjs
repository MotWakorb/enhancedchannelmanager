export function resolveE2EBaseURL(exactBuild, inheritedBaseURL) {
  // E2E_EXACT_BUILD builds and serves the checked-out source itself on an
  // isolated preview port (see playwright.config.ts's webServer block), so
  // this mode supplies its own base URL and never touches E2E_BASE_URL.
  if (exactBuild) return 'http://127.0.0.1:4173'

  if (!inheritedBaseURL) {
    throw new Error(
      'E2E_BASE_URL is not set.\n\n' +
        'The e2e suite no longer defaults E2E_BASE_URL to http://localhost:6100. ' +
        'That address is the product owner\'s LIVE ECM instance holding real channel ' +
        'data, and specs that stage and apply a channel-number push-down, a cross-group ' +
        'move, a merge, a delete, or any other write would have mutated it for anyone ' +
        'who ran the suite locally without overriding the variable. The default has been ' +
        'removed so this can never happen silently.\n\n' +
        'Set E2E_BASE_URL to the instance you actually intend to test against, e.g.\n' +
        '  E2E_BASE_URL=http://localhost:6100 npx playwright test   # explicit opt-in to the live instance\n' +
        '  E2E_BASE_URL=http://localhost:5173 npx playwright test   # a dev server you started yourself\n\n' +
        'Or, to have Playwright build and serve the checked-out source on its own isolated ' +
        'preview port with no live instance involved:\n' +
        '  E2E_START_SERVER=true E2E_EXACT_BUILD=true npx playwright test\n\n' +
        'See docs/testing.md for details.'
    )
  }

  return inheritedBaseURL
}
