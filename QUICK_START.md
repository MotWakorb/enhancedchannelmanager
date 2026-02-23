# Enhanced Features - Quick Start Guide

## 🚀 Get Started in 3 Steps

### Step 1: Access Settings
1. Open ECM in your browser
2. Click the **Settings** tab
3. Click **Enhanced Features** in the left sidebar

### Step 2: Enable Features
Choose which features you want:

#### 🔀 Provider Diversification
- **Toggle**: ON
- **Mode**: Round Robin or Priority Weighted
- **Purpose**: Distribute streams across providers

#### 🎯 Account Stream Limits
- **Toggle**: ON
- **Global Limit**: 2 (or your preferred number)
- **Per-Account**: Set custom limits for specific accounts
- **Purpose**: Limit streams per account per channel

#### ⭐ M3U Priority
- **Mode**: Same Resolution Only or All Streams
- **Priorities**: Set priority values for each account
- **Purpose**: Boost scores for premium accounts

### Step 3: Save & Test
1. Click **Save Configuration**
2. Run a Stream Probe or Auto-Creation rule
3. Verify streams are reordered/limited correctly

---

## 📖 Quick Examples

### Example 1: Basic Setup
```
Provider Diversification: ON (Round Robin)
Account Stream Limits: ON (Global: 2)
M3U Priority: Same Resolution Only
```
**Result**: Each channel gets max 2 streams per account, distributed across providers, with premium accounts prioritized.

### Example 2: Premium Focus
```
Provider Diversification: ON (Priority Weighted)
Account Stream Limits: OFF
M3U Priority: All Streams
Account Priorities: Premium=100, Basic=10
```
**Result**: Premium account streams always come first, distributed by priority weight.

### Example 3: Balanced Distribution
```
Provider Diversification: ON (Round Robin)
Account Stream Limits: ON (Global: 3, Premium: 5, Free: 1)
M3U Priority: Disabled
```
**Result**: Streams distributed evenly, with custom limits per account type.

---

## 🔧 API Quick Reference

```bash
# Get current config
curl http://localhost:9191/api/enhanced-features/config

# Enable Provider Diversification
curl -X PUT http://localhost:9191/api/enhanced-features/provider-diversification \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "mode": "round_robin"}'

# Set Account Limits (2 per channel)
curl -X PUT http://localhost:9191/api/enhanced-features/account-stream-limits \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "global_limit": 2}'

# Set M3U Priorities
curl -X PUT http://localhost:9191/api/enhanced-features/m3u-priority \
  -H "Content-Type: application/json" \
  -d '{"mode": "same_resolution", "account_priorities": {"1": 100, "2": 10}}'
```

---

## ❓ FAQ

**Q: Are limits per-channel or global?**
A: Per-channel. Each channel can have up to N streams from each account.

**Q: What order are features applied?**
A: 1) Quality Sort → 2) Provider Diversification → 3) Account Stream Limits

**Q: Do features work with Auto-Creation?**
A: Yes! Features are applied both in Stream Prober and Auto-Creation.

**Q: Can I disable features temporarily?**
A: Yes, just toggle them OFF in the UI or via API.

**Q: Where is configuration stored?**
A: `/config/enhanced_features.json`

---

## 📚 More Information

- **Full Documentation**: `ENHANCED_FEATURES_README.md`
- **Installation Guide**: `ENHANCED_FEATURES_INSTALLATION.md`
- **German Guide**: `ENHANCED_FEATURES_DE.md`
- **Integration Details**: `ENHANCED_INTEGRATION_COMPLETE.md`

---

**Ready to optimize your streams? Start now!** 🎉
