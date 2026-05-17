# Discord Formatting Guide

## Release Notes Format
- Use `## 🚀 Title` for the first line of the first post only
- Use `**Bold Text**` for section headers (not `##` or `###`)
- Every section header should include a relevant emoji
- Use `•` (bullet character) for list items — Discord mangles `-` dashes into indented sublists
- Keep each post under 2000 characters
- First post should include `@here`
- No blank line needed between header and first bullet

## Discord Markdown Quirks
- `##` works for headings but only use it for the main title
- `-` as list items causes inconsistent indentation — second and subsequent items get indented as sublists. Always use `•` instead
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

### v0.17.1

```
@here

## 🚀 ECM v0.17.1

**🆕 Plex + Jellyfin User Attribution + Multi-Viewer**
• Connected Clients now shows usernames for Plex and Jellyfin streams (was: Emby only)
• When multiple users watch the same channel through the same media server, all their names are listed (was: only the most-recent user's name)
• Configure under Settings → Integrations → Plex Integration / Jellyfin Integration
• No re-migration required; new columns in session_telemetry are populated as new sessions arrive

**🔒 Security**
• SSRF mitigation on test-connection endpoints for Emby, Plex, and Jellyfin (scheme allowlist + netloc-only URL reconstruction)

**📝 Documentation**
• New Integrations operator guide covering Emby, Plex, and Jellyfin side-by-side (Settings → Integrations)
```
