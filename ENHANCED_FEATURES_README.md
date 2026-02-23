# Enhanced Features für ECM

## Übersicht

Diese Features wurden aus Enhanced-mod nach ECM portiert, um erweiterte Stream-Management-Funktionen bereitzustellen:

1. **Provider Diversification** - Verbesserte Redundanz durch intelligente Provider-Rotation
2. **Account Stream Limits** - Limitierung der Streams pro M3U Account pro Channel
3. **Erweiterte M3U Priority Modi** - 3 Modi für M3U-Priorisierung

## ✅ Portierte Features

### 1. Provider Diversification

**Zweck:** Bessere Redundanz durch Verteilung von Streams verschiedener Provider.

**Zwei Modi:**

#### Round Robin (Alphabetisch)
```
Provider A: [0.95, 0.94, 0.93]
Provider B: [0.92, 0.91, 0.90]
Provider C: [0.89, 0.88, 0.87]

Ergebnis: A:0.95, B:0.92, C:0.89, A:0.94, B:0.91, C:0.88, ...
```

#### Priority Weighted (M3U-Prioritäten)
```
Premium (Prio 100): [50.95, 50.94]
Basic (Prio 10): [5.92, 5.91]

Ergebnis: Premium:50.95, Basic:5.92, Premium:50.94, Basic:5.91, ...
```

**Vorteile:**
- ✅ Automatisches Failover zu anderen Providern
- ✅ Kein Single Point of Failure
- ✅ Lastverteilung auf mehrere Provider

---

### 2. Account Stream Limits

**Zweck:** Limitierung der Streams pro M3U Account **pro Channel**.

**WICHTIG:** Limits gelten **pro Channel**, nicht global!

**Beispiel:**
```json
{
  "enabled": true,
  "global_limit": 2,
  "account_limits": {
    "1": 5,
    "2": 1,
    "3": 0
  }
}
```

**Bei 10 Channels:**
- Account 1: Max 50 Streams total (5 × 10 Channels)
- Account 2: Max 10 Streams total (1 × 10 Channels)
- Account 3: Unbegrenzt
- Andere: Max 20 Streams total (2 × 10 Channels)

**Anwendungsfälle:**
- Bandbreiten-Management
- Kosten-Kontrolle
- Load Balancing
- Provider-Gewichtung

---

### 3. Erweiterte M3U Priority Modi

**Drei Modi verfügbar:**

#### Disabled
- M3U Priority wird ignoriert
- Nur Quality Scoring zählt

#### Same Resolution Only
- Priority wird nur innerhalb gleicher Auflösungen angewendet
- Beispiel: 1080p Premium vor 1080p Basic, aber 4K Basic vor 1080p Premium

#### All Streams
- Priority wird immer angewendet
- Premium-Provider immer vor Basic-Providern, unabhängig von Qualität

---

## 🔧 API-Nutzung

### Konfiguration abrufen

```bash
GET /api/enhanced-features/config
```

**Response:**
```json
{
  "provider_diversification": {
    "enabled": false,
    "mode": "round_robin"
  },
  "account_stream_limits": {
    "enabled": false,
    "global_limit": 0,
    "account_limits": {}
  },
  "m3u_priority": {
    "mode": "disabled",
    "account_priorities": {}
  }
}
```

---

### Provider Diversification aktivieren

```bash
PUT /api/enhanced-features/provider-diversification
Content-Type: application/json

{
  "enabled": true,
  "mode": "round_robin"
}
```

**Modi:**
- `round_robin` - Alphabetische Provider-Rotation
- `priority_weighted` - M3U-Prioritäts-basierte Rotation

---

### Account Stream Limits konfigurieren

```bash
PUT /api/enhanced-features/account-stream-limits
Content-Type: application/json

{
  "enabled": true,
  "global_limit": 2,
  "account_limits": {
    "1": 5,
    "2": 1
  }
}
```

**Parameter:**
- `enabled` - Feature aktivieren/deaktivieren
- `global_limit` - Standard-Limit pro Account pro Channel (0 = unbegrenzt)
- `account_limits` - Spezifische Limits pro Account pro Channel

---

### M3U Priority Mode setzen

```bash
PUT /api/enhanced-features/m3u-priority
Content-Type: application/json

{
  "mode": "all_streams",
  "account_priorities": {
    "1": 100,
    "2": 50,
    "3": 10
  }
}
```

**Modi:**
- `disabled` - Priority ignoriert
- `same_resolution` - Priority nur innerhalb gleicher Auflösung
- `all_streams` - Priority immer angewendet

---

## 🔌 Integration in ECM

### Stream Prober Integration

Die Features sind in den Stream Prober integriert und werden automatisch angewendet, wenn:

1. **Auto-Reorder After Probe** aktiviert ist
2. Streams nach dem Probing sortiert werden

### Auto-Creation Integration

Die Features können in der Auto-Creation Pipeline verwendet werden:

```python
from stream_diversification import apply_provider_diversification
from account_stream_limits import apply_account_stream_limits
from enhanced_features_config import get_enhanced_features_config

# Konfiguration laden
config = get_enhanced_features_config()

# Provider Diversification anwenden
if config.provider_diversification.enabled:
    stream_ids = apply_provider_diversification(
        stream_ids=stream_ids,
        stream_m3u_map=stream_m3u_map,
        enabled=True,
        mode=config.provider_diversification.mode,
        m3u_account_priorities=config.m3u_priority.account_priorities,
        channel_name=channel_name
    )

# Account Stream Limits anwenden
if config.account_stream_limits.enabled:
    stream_ids = apply_account_stream_limits(
        stream_ids=stream_ids,
        stream_m3u_map=stream_m3u_map,
        enabled=True,
        global_limit=config.account_stream_limits.global_limit,
        account_limits=config.account_stream_limits.account_limits,
        channel_name=channel_name
    )
```

---

## 📁 Dateien

### Backend-Module:
- `ECM/backend/stream_diversification.py` - Provider Diversification Logik
- `ECM/backend/account_stream_limits.py` - Account Stream Limits Logik
- `ECM/backend/enhanced_features_config.py` - Konfigurations-Management
- `ECM/backend/routers/enhanced_features.py` - API-Endpunkte

### Konfigurationsdatei:
- `/config/enhanced_features.json` - Persistente Konfiguration

---

## 🚀 Aktivierung

### 1. Router registrieren

In `ECM/backend/main.py`:

```python
# Import enhanced features router
from routers.enhanced_features import router as enhanced_features_router
app.include_router(enhanced_features_router)
```

### 2. Features aktivieren

Über API oder direkt in der Konfigurationsdatei:

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

## ⚠️ Wichtige Hinweise

### Account Stream Limits
- **Pro-Channel Zählung:** Limits gelten pro Channel, nicht global!
- **Custom Streams:** Streams ohne M3U Account sind nicht betroffen
- **Limit 0:** Bedeutet unbegrenzt für diesen Account

### Provider Diversification
- **Mindestens 2 Provider:** Benötigt mindestens 2 verschiedene Provider pro Channel
- **Custom Streams:** Werden ans Ende sortiert
- **Quality First:** Diversification wird NACH Quality Sorting angewendet

### M3U Priority
- **Höhere Zahlen = Höhere Priorität:** 100 > 50 > 10
- **Mode-Abhängig:** Funktionsweise hängt vom gewählten Modus ab
- **Integration:** Funktioniert mit Provider Diversification

---

## 🔍 Logging

Alle Features loggen detailliert:

```
[DIVERSIFICATION] Channel 'ARD': Applying round_robin diversification to 9 streams from 3 providers
[ACCOUNT-LIMITS] Channel 'ZDF': Applying limits to 12 streams
[ACCOUNT-LIMITS] Channel 'ZDF': Excluded 3 streams due to account limits
```

Log-Level: `INFO` für Hauptaktionen, `DEBUG` für Details

---

## 📊 Vergleich: Enhanced vs ECM

| Feature | Enhanced | ECM (portiert) |
|---------|-----------|----------------|
| Provider Diversification | ✅ 2 Modi | ✅ 2 Modi |
| Account Stream Limits | ✅ Pro Channel | ✅ Pro Channel |
| M3U Priority Modi | ✅ 3 Modi | ✅ 3 Modi |
| Profile Failover | ✅ Phase 1+2 | ❌ Nicht portiert |
| Dead Stream Removal | ✅ Automatisch | ❌ Nicht portiert |
| Quality Weights | ✅ Konfigurierbar | ❌ Nicht portiert |

**Nicht portierte Features** benötigen FFmpeg Stream Analysis Engine, die in ECM nicht vorhanden ist.

---

## 🎯 Nächste Schritte

1. ✅ Backend-Module erstellt
2. ✅ API-Endpunkte erstellt
3. ✅ Konfigurations-Management erstellt
4. ⏳ Router in main.py registrieren
5. ⏳ Frontend-UI erstellen
6. ⏳ Integration in Stream Prober
7. ⏳ Integration in Auto-Creation Pipeline

---

## 📝 Lizenz

Diese Features wurden aus Enhanced-mod portiert und unterliegen der gleichen Lizenz wie ECM.

**Original Enhanced:** https://github.com/krinkuto11/enhanced

---

## 🤝 Beitrag

Verbesserungen und Bugfixes sind willkommen! Bitte erstelle einen Pull Request.

---

**Version:** 1.0.0  
**Datum:** 2026-02-23  
**Status:** ✅ Backend komplett, Frontend ausstehend
