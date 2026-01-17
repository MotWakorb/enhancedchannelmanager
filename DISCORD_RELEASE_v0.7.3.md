# Discord Release Notes - v0.7.3

## Post 1 - Main Announcement (1847 chars)

**🎉 Enhanced Channel Manager v0.7.3 Released!**

📦 **Docker Image:** `ghcr.io/motwakorb/enhancedchannelmanager:latest`
🔗 **Release:** https://github.com/MotWakorb/enhancedchannelmanager/releases/tag/v0.7.3

**Major Features:**

**🔄 Stream Probing Enhancements**
• Auto-reorder streams by quality/status after scheduled probes
• Persistent probe history saved to `/config/probe_history.json`
• Auto-detect scheduled probe progress in UI
• Improved HDHomeRun support with tuned parallelism
• VLC User-Agent added for better stream compatibility
• View detailed error messages for failed streams
• Force reset endpoint for stuck probe states

**🗑️ Delete Orphaned Channel Groups**
• Detect and remove groups with no streams, channels, or M3U association
• Selective deletion - choose all or specific groups
• New API endpoints: `GET/DELETE /api/channel-groups/orphaned`
• Available in Settings → Maintenance section

**🔧 Utility Scripts**
• New `scripts/search-stream.sh` - CLI tool for searching Dispatcharr streams
• Auto-authentication, URL encoding, pretty JSON output
• Example: `./scripts/search-stream.sh http://dispatcharr:9191 admin pass "ESPN"`

**🐛 Critical Fixes**
• Fixed Docker cache bug preventing code updates from deploying
• Fixed 422 error on delete orphaned groups (FastAPI route ordering)
• Fixed scheduler not calling auto-reorder when enabled
• Fixed multiple settings not persisting correctly

---

## Post 2 - Improvements & Technical (1956 chars)

**Improvements in v0.7.3:**

**📺 EPG & Guide**
• Fixed timeout with large channel counts (using correct Dispatcharr endpoint)
• Improved EPG program matching via epg_data_id indirection

**🔀 Stream Auto-Reorder**
• Now uses configured sort priority settings (not hardcoded)
• Reorder results modal shows which sort config was used

**📡 M3U Manager**
• Auto-detect M3U account refresh status and display in UI

**⚙️ Settings & Configuration**
• Fixed settings persistence (missing model fields)
• Clarified timezone affects stats collection AND scheduler
• Restart notifications when probe schedule changes

**🔍 Probing System**
• Unified probe operations to `/probe/all` endpoint
• Debug logging for channel group filter inclusion/exclusion
• Fixed scheduler stopping after probe cancellation
• Improved ffprobe error messages

**🐳 Docker & Deployment**
• Fixed critical Docker layer caching issue
• Added GIT_COMMIT build arg to frontend and backend stages
• Ensures fresh builds on every git commit

**📚 API Changes**
New endpoints:
• `GET /api/channel-groups/orphaned` - List orphaned groups
• `DELETE /api/channel-groups/orphaned` - Delete orphaned groups
• `POST /api/stream-stats/probe/reset` - Force reset stuck probe

Enhanced endpoints:
• Probe endpoints support auto-reorder integration
• More detailed error information in probe results

**📖 Documentation**
• Updated README with all new features
• Added Utility Scripts section
• Updated API endpoints reference

---

## Post 3 - Upgrade Notes (743 chars)

**Upgrade Notes:**

**No Breaking Changes** ✅

**Recommended Actions:**
1. Review auto-reorder settings if using scheduled probes
2. Check for orphaned groups and clean up using new deletion feature
3. Update automation scripts to use new orphaned groups API if needed

**Config Volume:**
The `/config` directory now contains:
• `settings.json` - Application settings (existing)
• `probe_history.json` - Persistent probe results (NEW)

**Docker Update:**
```bash
docker pull ghcr.io/motwakorb/enhancedchannelmanager:latest
docker-compose up -d
```

**Full Changelog:** https://github.com/MotWakorb/enhancedchannelmanager/compare/v0.7.2...v0.7.3

Built with collaboration from Claude Sonnet 4.5 🤖
