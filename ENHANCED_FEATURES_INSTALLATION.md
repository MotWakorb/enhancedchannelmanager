# Enhanced Features Installation für ECM

## ✅ Was wurde portiert?

Folgende Features aus Enhanced-mod wurden erfolgreich nach ECM portiert:

### 1. Provider Diversification ✅
- **Round Robin Mode:** Alphabetische Provider-Rotation
- **Priority Weighted Mode:** M3U-Prioritäts-basierte Rotation
- **Datei:** `ECM/backend/stream_diversification.py`

### 2. Account Stream Limits ✅
- **Pro-Channel Limits:** Limitierung pro M3U Account pro Channel
- **Global + Per-Account:** Flexible Konfiguration
- **Datei:** `ECM/backend/account_stream_limits.py`

### 3. Erweiterte M3U Priority Modi ✅
- **3 Modi:** Disabled, Same Resolution, All Streams
- **Integration:** Funktioniert mit Diversification
- **Datei:** `ECM/backend/enhanced_features_config.py`

---

## 📦 Installierte Dateien

### Backend-Module:
```
ECM/backend/
├── stream_diversification.py          # Provider Diversification Logik
├── account_stream_limits.py           # Account Stream Limits Logik
├── enhanced_features_config.py      # Konfigurations-Management
└── routers/
    └── enhanced_features.py         # API-Endpunkte
```

### Dokumentation:
```
ECM/
├── ENHANCED_FEATURES_README.md      # Feature-Dokumentation
└── ENHANCED_FEATURES_INSTALLATION.md # Diese Datei
```

### Konfiguration:
```
/config/enhanced_features.json       # Wird automatisch erstellt
```

---

## 🚀 Schnellstart

### 1. Backend starten

Die Features sind bereits integriert! Starte einfach ECM:

```bash
cd ECM
docker-compose up -d
```

Oder lokal:

```bash
cd ECM/backend
python main.py
```

### 2. API testen

```bash
# Konfiguration abrufen
curl http://localhost:8000/api/enhanced-features/config

# Provider Diversification aktivieren
curl -X PUT http://localhost:8000/api/enhanced-features/provider-diversification \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "mode": "round_robin"}'

# Account Stream Limits aktivieren
curl -X PUT http://localhost:8000/api/enhanced-features/account-stream-limits \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "global_limit": 2}'
```

### 3. Swagger UI öffnen

Öffne http://localhost:8000/api/docs und navigiere zu "Enhanced Features"

---

## 🔧 Konfiguration

### Über API

```bash
# Komplette Konfiguration aktualisieren
curl -X PUT http://localhost:8000/api/enhanced-features/config \
  -H "Content-Type: application/json" \
  -d '{
    "provider_diversification": {
      "enabled": true,
      "mode": "priority_weighted"
    },
    "account_stream_limits": {
      "enabled": true,
      "global_limit": 2,
      "account_limits": {
        "1": 5,
        "2": 1
      }
    },
    "m3u_priority": {
      "mode": "all_streams",
      "account_priorities": {
        "1": 100,
        "2": 50,
        "3": 10
      }
    }
  }'
```

### Über Konfigurationsdatei

Bearbeite `/config/enhanced_features.json`:

```json
{
  "provider_diversification": {
    "enabled": true,
    "mode": "round_robin"
  },
  "account_stream_limits": {
    "enabled": true,
    "global_limit": 2,
    "account_limits": {
      "1": 5
    }
  },
  "m3u_priority": {
    "mode": "all_streams",
    "account_priorities": {
      "1": 100,
      "2": 50
    }
  }
}
```

---

## 🔌 Integration

### In Stream Prober

Die Features können im Stream Prober verwendet werden:

```python
from stream_diversification import apply_provider_diversification
from account_stream_limits import apply_account_stream_limits
from enhanced_features_config import get_enhanced_features_config

# In _smart_sort_streams oder nach dem Sorting:
config = get_enhanced_features_config()

# 1. Provider Diversification anwenden
if config.provider_diversification.enabled:
    sorted_ids = apply_provider_diversification(
        stream_ids=sorted_ids,
        stream_m3u_map=stream_m3u_map,
        enabled=True,
        mode=config.provider_diversification.mode,
        m3u_account_priorities=config.m3u_priority.account_priorities,
        channel_name=channel_name
    )

# 2. Account Stream Limits anwenden
if config.account_stream_limits.enabled:
    sorted_ids = apply_account_stream_limits(
        stream_ids=sorted_ids,
        stream_m3u_map=stream_m3u_map,
        enabled=True,
        global_limit=config.account_stream_limits.global_limit,
        account_limits=config.account_stream_limits.account_limits,
        channel_name=channel_name
    )
```

### In Auto-Creation Pipeline

Die Features können in der Auto-Creation Pipeline verwendet werden:

```python
from stream_diversification import apply_provider_diversification
from account_stream_limits import apply_account_stream_limits
from enhanced_features_config import get_enhanced_features_config

# Nach dem Stream Matching und vor dem Assignment:
config = get_enhanced_features_config()

for channel_id, stream_ids in matched_streams.items():
    # Provider Diversification
    if config.provider_diversification.enabled:
        stream_ids = apply_provider_diversification(
            stream_ids=stream_ids,
            stream_m3u_map=stream_m3u_map,
            enabled=True,
            mode=config.provider_diversification.mode,
            m3u_account_priorities=config.m3u_priority.account_priorities,
            channel_name=f"Channel {channel_id}"
        )
    
    # Account Stream Limits
    if config.account_stream_limits.enabled:
        stream_ids = apply_account_stream_limits(
            stream_ids=stream_ids,
            stream_m3u_map=stream_m3u_map,
            enabled=True,
            global_limit=config.account_stream_limits.global_limit,
            account_limits=config.account_stream_limits.account_limits,
            channel_name=f"Channel {channel_id}"
        )
    
    matched_streams[channel_id] = stream_ids
```

---

## 📊 API-Endpunkte

### Übersicht

| Endpunkt | Methode | Beschreibung |
|----------|---------|--------------|
| `/api/enhanced-features/config` | GET | Komplette Konfiguration abrufen |
| `/api/enhanced-features/config` | PUT | Konfiguration aktualisieren |
| `/api/enhanced-features/config/reset` | POST | Auf Defaults zurücksetzen |
| `/api/enhanced-features/provider-diversification` | GET | Provider Diversification Config |
| `/api/enhanced-features/provider-diversification` | PUT | Provider Diversification Update |
| `/api/enhanced-features/account-stream-limits` | GET | Account Stream Limits Config |
| `/api/enhanced-features/account-stream-limits` | PUT | Account Stream Limits Update |
| `/api/enhanced-features/m3u-priority` | GET | M3U Priority Config |
| `/api/enhanced-features/m3u-priority` | PUT | M3U Priority Update |

---

## 🧪 Testing

### 1. Provider Diversification testen

```bash
# Aktivieren
curl -X PUT http://localhost:8000/api/enhanced-features/provider-diversification \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "mode": "round_robin"}'

# Prüfen
curl http://localhost:8000/api/enhanced-features/provider-diversification
```

### 2. Account Stream Limits testen

```bash
# Konfigurieren
curl -X PUT http://localhost:8000/api/enhanced-features/account-stream-limits \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "global_limit": 2,
    "account_limits": {
      "1": 5,
      "2": 1
    }
  }'

# Prüfen
curl http://localhost:8000/api/enhanced-features/account-stream-limits
```

### 3. M3U Priority testen

```bash
# Konfigurieren
curl -X PUT http://localhost:8000/api/enhanced-features/m3u-priority \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "all_streams",
    "account_priorities": {
      "1": 100,
      "2": 50,
      "3": 10
    }
  }'

# Prüfen
curl http://localhost:8000/api/enhanced-features/m3u-priority
```

---

## 📝 Logging

Die Features loggen detailliert:

```
[ENHANCED-CONFIG] Configuration loaded successfully
[DIVERSIFICATION] Channel 'ARD': Applying round_robin diversification to 9 streams from 3 providers
[ACCOUNT-LIMITS] Channel 'ZDF': Applying limits to 12 streams
[ACCOUNT-LIMITS] Channel 'ZDF': Excluded 3 streams due to account limits
```

Log-Level anpassen in ECM Settings oder via Environment Variable:
```bash
export LOG_LEVEL=DEBUG
```

---

## ⚠️ Bekannte Einschränkungen

### Nicht portierte Features

Folgende Enhanced-Features wurden **NICHT** portiert, da sie FFmpeg Stream Analysis benötigen:

- ❌ **Profile Failover** (Phase 1+2 mit Intelligent Polling)
- ❌ **Dead Stream Removal** (Automatische Erkennung)
- ❌ **Quality Weights** (Konfigurierbare Gewichtung)
- ❌ **Channel Quality Preferences** (Pro-Channel Präferenzen)
- ❌ **Quality Check Exclusions** (Deaktivierte M3U Accounts)
- ❌ **Fallback Score Fix** (Streams ohne Bitrate)

Diese Features benötigen eine komplette FFmpeg-Integration, die in ECM nicht vorhanden ist.

### Workarounds

- **Stream Quality:** ECM hat bereits Stream Probing mit FFprobe
- **Dead Streams:** Manuell über UI entfernen oder Auto-Creation Rules verwenden
- **Quality Preferences:** Über Auto-Creation Rules und Conditions konfigurieren

---

## 🔄 Updates

### Version 1.0.0 (2026-02-23)
- ✅ Initiale Portierung aus enhanced-mod
- ✅ Provider Diversification (2 Modi)
- ✅ Account Stream Limits (Pro Channel)
- ✅ Erweiterte M3U Priority Modi (3 Modi)
- ✅ API-Endpunkte
- ✅ Konfigurations-Management
- ✅ Dokumentation

---

## 🤝 Support

Bei Fragen oder Problemen:

1. Prüfe die Logs: `docker logs enhancedchannelmanager`
2. Prüfe die Konfiguration: `cat /config/enhanced_features.json`
3. Teste die API: `curl http://localhost:8000/api/enhanced-features/config`
4. Öffne ein Issue auf GitHub

---

## 📚 Weitere Dokumentation

- [ENHANCED_FEATURES_README.md](ENHANCED_FEATURES_README.md) - Detaillierte Feature-Dokumentation
- [ECM README.md](README.md) - ECM Hauptdokumentation
- [ECM USER_GUIDE.md](USER_GUIDE.md) - ECM Benutzerhandbuch

---

**Version:** 1.0.0  
**Datum:** 2026-02-23  
**Status:** ✅ Backend komplett, Frontend ausstehend  
**Portiert von:** Enhanced-mod  
**Portiert nach:** ECM (Enhanced Channel Manager)
