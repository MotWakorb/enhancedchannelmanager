# Discord Formatting Guide

## Release Notes Format
- Use `## 🚀 Title` for the first line of the first post only
- Use `**Bold Text**` for section headers (not `##` or `###`)
- Every section header should include a relevant emoji
- Use `•` (bullet character) for list items. Discord mangles `-` dashes into indented sublists
- Keep each post under 2000 characters
- First post should include `@here`
- No blank line needed between header and first bullet

## Discord Markdown Quirks
- `##` works for headings but only use it for the main title
- `-` as list items causes inconsistent indentation: second and subsequent items get indented as sublists. Always use `•` instead
- `**bold**` works, `*italic*` works, `~~strikethrough~~` works
- ``` for code blocks works
- No support for standard markdown links `[text](url)` in regular messages
- Blank lines between sections help readability

## Example Release Post Structure
```
@everyone

## 🚀 Project vX.Y.Z Released

**🆕 New Feature Name**
• First item
• Second item
• Third item

**🐛 Bug Fixes**
• Fix description one
• Fix description two

**🎨 UI/UX Improvements**
• Improvement one
• Improvement two

**⚙️ Backend**
• Backend change one
• Backend change two
```

## Common Section Emojis
- 🚀 Release title
- 🆕 New features
- 🐛 Bug fixes
- 🎨 UI/UX / CSS / styling
- ⚙️ Backend / infrastructure
- 🧪 Testing
- 📝 Documentation
- ⚡ Performance
- 🔒 Security
- 💥 Breaking changes

---

## Pending release notes (copy-paste to Discord when cutting the release)

### v0.17.2

```
@here

## 🚀 ECM v0.17.2

**🆕 Manage ECM through Claude: full Stats v2 coverage**
• 8 new MCP tools so Claude can answer questions about your data: provider performance, per-user watch time, trending & popularity, the activity feed, and channel bandwidth
• "Who's watching channel X?" Claude can now read media-server attribution (Emby/Plex/Jellyfin usernames, client IPs, provider) on active channels
• 124 MCP tools total: the MCP surface now covers the Stats v2 + attribution features that were previously UI-only

**🐛 MCP reliability: a big correctness pass (30+ fixes)**
• Static API-key auth now works across every tool (dedup, add-stream merge modes, and backups were previously rejected)
• Fixed reorder / bulk-commit silently dropping streams from a channel
• get_journal returns entries again; the EPG grid shows real channel names; stream provider/group show names instead of raw IDs
• Channel numbers display as whole numbers (no more "#10440.0"); clearer "nothing to show" messages for probes
• Found and fixed via a live sweep of every MCP tool

**🔒 Security**
• Constant-time comparison for the MCP API key (timing-attack hardening)
• The MCP service key can no longer modify ECM user accounts (clean 403 instead of a 500)

**⚠️ Deprecation (still works, removed in v0.18.0)**
• `ECM_TELEMETRY_EXCLUDE_USERS` is deprecated: the Stats attribution bug it worked around is now fixed, so you no longer need it. If it's set, you'll see a one-time log warning.
```
