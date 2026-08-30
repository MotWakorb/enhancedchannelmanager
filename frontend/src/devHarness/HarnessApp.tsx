import { Component, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from 'react'
import { AuthProvider } from '../hooks/useAuth'
import { NotificationProvider } from '../contexts/NotificationContext'
import { BackupDestinationPromptProvider } from '../contexts/BackupDestinationPromptContext'
import { DIALOG_CATALOG, catalogEntry, type DialogCatalogEntry } from './dialogCatalog'
import { DIALOG_RENDERERS } from './dialogRenderers'
import { discoverDialogFiles } from './discoverDialogs'
import { stubState } from './apiStub'
import type { OpenStep } from './harnessTypes'

const DEFAULT_EXPECT = '.modal-container, [role="dialog"], [role="alertdialog"]'

export type HarnessStatus = 'pending' | 'rendered' | 'not-rendered' | 'crashed' | 'gap' | 'unknown-id'

declare global {
  interface Window {
    __MODAL_HARNESS__?: {
      catalog: readonly DialogCatalogEntry[]
      discoveredFiles: string[]
      status: HarnessStatus
      dialogId: string | null
      error: string | null
      unstubbedCalls: string[]
      apiCalls: string[]
    }
    __MODAL_HARNESS_READY__?: boolean
  }
}

function publish(patch: Partial<NonNullable<Window['__MODAL_HARNESS__']>>): void {
  window.__MODAL_HARNESS__ = {
    catalog: DIALOG_CATALOG,
    discoveredFiles: discoverDialogFiles(),
    status: 'pending',
    dialogId: null,
    error: null,
    unstubbedCalls: [],
    apiCalls: [],
    ...window.__MODAL_HARNESS__,
    ...patch,
  }
}

/** ------------------------------------------------------------------ */
/** Error boundary — a crash is a reported outcome, not a blank page.   */
/** ------------------------------------------------------------------ */

class HarnessBoundary extends Component<
  { children: ReactNode; onError: (message: string) => void },
  { crashed: boolean }
> {
  state = { crashed: false }

  static getDerivedStateFromError() {
    return { crashed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError(`${error.message}\n${info.componentStack ?? ''}`.trim())
  }

  render() {
    if (this.state.crashed) return null
    return this.props.children
  }
}

/** ------------------------------------------------------------------ */
/** Open-step driver                                                    */
/** ------------------------------------------------------------------ */

function isVisible(el: Element): boolean {
  const rect = (el as HTMLElement).getBoundingClientRect()
  return rect.width > 0 && rect.height > 0
}

/**
 * Accessible-name-ish text for matching. Many openers in this codebase are
 * icon-only buttons whose only label is `title` / `aria-label`, so matching
 * on `textContent` alone finds nothing (that was the first cause of a
 * "not-rendered" result here that had nothing to do with the dialog).
 */
function clickableLabel(el: HTMLElement): string {
  return [el.textContent ?? '', el.getAttribute('title') ?? '', el.getAttribute('aria-label') ?? '']
    .join(' ')
    .toLowerCase()
}

function findClickable(step: Extract<OpenStep, { kind: 'click' }>): HTMLElement | null {
  const selector =
    step.selector ?? 'button, [role="button"], [role="menuitem"], a, summary, .settings-nav-item'
  const candidates = Array.from(document.querySelectorAll<HTMLElement>(selector)).filter(isVisible)
  const matches = step.text
    ? candidates.filter((el) => clickableLabel(el).includes(step.text!.toLowerCase()))
    : candidates
  return matches[step.nth ?? 0] ?? null
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** Wait until no stubbed request is in flight, then let React flush. */
async function settle(): Promise<void> {
  const deadline = Date.now() + 4000
  while (stubState().pending > 0 && Date.now() < deadline) await sleep(20)
  await sleep(60)
}

async function runOpenSteps(steps: OpenStep[]): Promise<void> {
  for (const step of steps) {
    if (step.kind === 'wait') {
      await sleep(step.ms)
      continue
    }
    const deadline = Date.now() + 4000
    let target = findClickable(step)
    while (!target && Date.now() < deadline) {
      await sleep(40)
      target = findClickable(step)
    }
    if (!target) {
      throw new Error(
        `open step failed: no visible clickable matching ${JSON.stringify({
          selector: step.selector,
          text: step.text,
          nth: step.nth,
        })}`
      )
    }
    target.click()
    await settle()
  }
}

/** ------------------------------------------------------------------ */
/** Single-dialog page                                                  */
/** ------------------------------------------------------------------ */

function DialogPage({ entry }: { entry: DialogCatalogEntry }) {
  const [crash, setCrash] = useState<string | null>(null)
  const driven = useRef(false)
  const renderer = DIALOG_RENDERERS[entry.id as keyof typeof DIALOG_RENDERERS]

  useEffect(() => {
    if (driven.current) return
    driven.current = true

    void (async () => {
      try {
        await settle()
        if (renderer?.open) await runOpenSteps(renderer.open)
        await settle()
        const expect = renderer?.expect ?? DEFAULT_EXPECT
        const found = Array.from(document.querySelectorAll(expect)).some(isVisible)
        publish({
          status: found ? 'rendered' : 'not-rendered',
          unstubbedCalls: [...stubState().unstubbedCalls],
          apiCalls: [...stubState().calls],
        })
      } catch (err) {
        publish({
          status: 'not-rendered',
          error: err instanceof Error ? err.message : String(err),
          unstubbedCalls: [...stubState().unstubbedCalls],
          apiCalls: [...stubState().calls],
        })
      } finally {
        window.__MODAL_HARNESS_READY__ = true
      }
    })()
  }, [renderer])

  useEffect(() => {
    if (!crash) return
    publish({
      status: 'crashed',
      error: crash,
      unstubbedCalls: [...stubState().unstubbedCalls],
      apiCalls: [...stubState().calls],
    })
    window.__MODAL_HARNESS_READY__ = true
  }, [crash])

  if (!renderer) {
    return null
  }

  return (
    <div data-harness-dialog-root>
      <HarnessBoundary onError={setCrash}>{renderer.render()}</HarnessBoundary>
    </div>
  )
}

/** ------------------------------------------------------------------ */
/** Index page (chrome — excluded from measurement)                     */
/** ------------------------------------------------------------------ */

function IndexPage() {
  const discovered = discoverDialogFiles()
  const declared = new Set<string>(DIALOG_CATALOG.map((e) => e.file))
  const missing = discovered.filter((f) => !declared.has(f))
  const stale = [...declared].filter((f) => !discovered.includes(f))
  const stubbed = DIALOG_CATALOG.filter((e) => e.status === 'stubbed')
  const gaps = DIALOG_CATALOG.filter((e) => e.status === 'gap')

  useEffect(() => {
    publish({ status: 'rendered', dialogId: null })
    window.__MODAL_HARNESS_READY__ = true
  }, [])

  const mono = { fontFamily: 'ui-monospace, monospace', fontSize: '13px' } as const

  return (
    <div data-harness-chrome style={{ padding: '24px', ...mono, lineHeight: 1.6 }}>
      <h1 style={{ fontSize: '20px', margin: '0 0 4px' }}>ECM modal harness — dev only</h1>
      <p style={{ margin: '0 0 16px', opacity: 0.75 }}>
        {discovered.length} source files match the dialog markers · {DIALOG_CATALOG.length} dialogs
        catalogued · {stubbed.length} force-renderable · {gaps.length} declared gaps
      </p>

      {(missing.length > 0 || stale.length > 0) && (
        <div style={{ border: '2px solid #c00', padding: '12px', margin: '0 0 16px' }}>
          <strong>Catalog drift</strong>
          {missing.length > 0 && <div>Discovered but not catalogued: {missing.join(', ')}</div>}
          {stale.length > 0 && <div>Catalogued but no longer found: {stale.join(', ')}</div>}
        </div>
      )}

      <h2 style={{ fontSize: '15px' }}>Force-renderable ({stubbed.length})</h2>
      <ol>
        {stubbed.map((e) => (
          <li key={e.id}>
            <a href={`?dialog=${e.id}`}>{e.id}</a> — {e.label}{' '}
            <span style={{ opacity: 0.6 }}>({e.via})</span>
          </li>
        ))}
      </ol>

      <h2 style={{ fontSize: '15px' }}>Declared gaps ({gaps.length})</h2>
      <ol>
        {gaps.map((e) => (
          <li key={e.id}>
            <strong>{e.id}</strong> — {e.label}
            <div style={{ opacity: 0.75 }}>{e.reason}</div>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** ------------------------------------------------------------------ */

export function HarnessApp() {
  const dialogId = new URLSearchParams(window.location.search).get('dialog')

  useEffect(() => {
    publish({ dialogId })
  }, [dialogId])

  useEffect(() => {
    if (!dialogId) return
    const entry = catalogEntry(dialogId)
    if (!entry) {
      publish({ status: 'unknown-id' })
      window.__MODAL_HARNESS_READY__ = true
    } else if (entry.status === 'gap') {
      publish({ status: 'gap', error: entry.reason ?? null })
      window.__MODAL_HARNESS_READY__ = true
    }
  }, [dialogId])

  if (!dialogId) return <IndexPage />

  const entry = catalogEntry(dialogId)
  if (!entry || entry.status === 'gap') return null

  return (
    <AuthProvider>
      <NotificationProvider>
        <BackupDestinationPromptProvider>
          <DialogPage entry={entry} />
        </BackupDestinationPromptProvider>
      </NotificationProvider>
    </AuthProvider>
  )
}
