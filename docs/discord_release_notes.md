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
