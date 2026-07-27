import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { checkOperatorDocs } from './check-operator-docs.mjs'

const images = [
  ['1-channel-manager-1280-collapsed.png', 1280, 720],
  ['2-channel-manager-1920-health-expanded.png', 1920, 1080],
  ['3-channel-manager-1280-edit-actions.png', 1280, 720],
  ['4-channel-manager-1920-edit-collapsed.png', 1920, 1080],
]

function png(width, height) {
  const buffer = Buffer.alloc(24)
  Buffer.from('\x89PNG\r\n\x1a\n', 'binary').copy(buffer)
  buffer.writeUInt32BE(width, 16)
  buffer.writeUInt32BE(height, 20)
  return buffer
}

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'operator-docs-'))
  fs.mkdirSync(path.join(root, 'docs/user_guide'), { recursive: true })
  fs.mkdirSync(path.join(root, 'docs/images/user_guide/operator-workspace'), { recursive: true })
  fs.writeFileSync(path.join(root, 'USER_GUIDE.md'), '# Guide\n')
  fs.writeFileSync(path.join(root, 'docs/index.md'), '# Docs\n')
  fs.writeFileSync(path.join(root, 'docs/user_guide/index.md'), '# User guide\n')
  for (const [name, width, height] of images) {
    fs.writeFileSync(path.join(root, 'docs/images/user_guide/operator-workspace', name), png(width, height))
  }
  return root
}

test('accepts current navigation language, links, and required image dimensions', () => {
  const root = fixture()
  fs.writeFileSync(path.join(root, 'docs/user_guide/index.md'), '[Guide](../../USER_GUIDE.md)\n')
  assert.deepEqual(checkOperatorDocs(root), [])
})

test('rejects stale navigation language', () => {
  const root = fixture()
  fs.writeFileSync(path.join(root, 'docs/user_guide/index.md'), 'Open the **Channel Manager**\n tab.\n')
  assert.match(checkOperatorDocs(root).join('\n'), /stale navigation term/)
})

test('rejects retired operator controls and selection workflows', () => {
  const staleExamples = [
    'Click **Exit Edit Mode**, then click **Commit**.',
    'Right-click on selected channels.',
    'Use **Ctrl+Click** or **Ctrl+A**.',
    'In Channel Pipeline, click **Add Rule**.',
    'Click **Add Source**.',
    'Use **Bulk Remove**.',
    'Click the **X** on a stream.',
    'Click the **copy icon** on any channel.',
    'Click the **play icon** on any stream.',
    'In EPG Manager, select Add Standard EPG, enter the URL, then click **Save**.',
    'In Channel Pipeline, configure the actions, then click **Save**.',
    'Click **Add Method** to configure an alert.',
    'Access the notification center from the **bell icon**.',
    'Click **Login with Dispatcharr**.',
    'Set **Primary Auth Mode** to Local.',
    'Enable **Dispatcharr Authentication**.',
    'Manage accounts in **Settings** → **Users**.',
    'Users can link multiple authentication methods.',
  ]
  for (const example of staleExamples) {
    const root = fixture()
    fs.writeFileSync(path.join(root, 'docs/user_guide/index.md'), `${example}\n`)
    assert.match(checkOperatorDocs(root).join('\n'), /stale navigation term/)
  }
})

test('rejects missing links and incorrect screenshot dimensions', () => {
  const root = fixture()
  fs.writeFileSync(path.join(root, 'docs/user_guide/index.md'), '[Missing](missing.md)\n')
  fs.writeFileSync(
    path.join(root, 'docs/images/user_guide/operator-workspace/1-channel-manager-1280-collapsed.png'),
    png(800, 600),
  )
  const errors = checkOperatorDocs(root).join('\n')
  assert.match(errors, /missing local target/)
  assert.match(errors, /expected 1280x720, got 800x600/)
})

test('validates Markdown fragments', () => {
  const root = fixture()
  fs.writeFileSync(path.join(root, 'docs/user_guide/target.md'), '# Existing Heading\n')
  fs.writeFileSync(
    path.join(root, 'docs/user_guide/index.md'),
    '[Valid](target.md#existing-heading)\n[Invalid](target.md#missing-heading)\n',
  )
  const errors = checkOperatorDocs(root).join('\n')
  assert.doesNotMatch(errors, /existing-heading/)
  assert.match(errors, /missing fragment #missing-heading/)
})
