# PMACS Operator Runbook

## System Startup

PMACS uses macOS launchd to manage 8 processes in dependency order.

### Dependency Order

```
1. pmacs-inference    (llama-server on :8080, pf-blocked from internet)
2. pmacs-cortex       (health monitor, kill switch, boot detection)
3. pmacs-cortex-self-check (meta-monitor, pings cortex every 60s)
4. pmacs-execution    (Ed25519 signing + broker, Unix Domain Socket)
5. pmacs-nervous      (orchestration on :8000, SSE, write API)
6. pmacs-stoploss     (RTH position monitoring every 30 min)
7. pmacs-mutation     (active flywheel, dormant first 50 cycles)
8. pmacs-dashboard    (read-only web UI on :8001, loopback only)
```

### Startup Procedure

1. Open MacBook — launchd starts all 8 processes automatically
2. Wait ~10s for inference model to load (Qwen3.6-35B-A3B, ~21GB)
3. Open browser: `http://localhost:8001`
4. Check Cortex page: audit chain status (green) + process heartbeats (all green) + disk + clock + sources
5. If gap > 24h since last cycle: cycle auto-initiates

### Manual Operations

```bash
# Install launchd services
python ops/install_launchd.sh

# Install pf rules (inference pf-blocked from internet)
sudo ops/install_pf_rules.sh

# Start inference manually (for testing)
ops/start_inference.sh

# Verify audit chain
python ops/audit_chain_verify.py

# Backup all data stores
python ops/backup_verify.py backup

# Restore from backup
python ops/backup_verify.py restore --backup-dir <backup-dir>
```

## Daily Workflow

### Pre-Market Inspection (Source.md §21.7)

1. Open `http://localhost:8001`
2. **Cortex page**: verify audit chain (green), process heartbeats (all green), disk free, clock drift, source connectivity
3. **Dashboard**: check active positions, last cycle status, mode badge, cycle timing
4. If no cycle ran in 24h: click "Run cycle now" or let auto-cycle start

### During Market Hours

- System runs autonomously in PAPER mode
- Monitor toast notifications for:
  - Trade fills (blue info toast, 5s)
  - Stop-loss triggers (amber warning, persistent until clicked)
  - Kill switch events (red modal, must acknowledge)
- Use Agents page to watch live cycle progress (Sankey diagram, persona outputs)
- Use Pipeline page to review verdicts per ticker

### End of Day

- Close MacBook — PMACS goes quiet
- Catastrophe-net broker-side stops (15% below entry) remain active at Alpaca
- No action needed unless kill switch is engaged

## First-Run Wizard

On first launch, PMACS runs an 11-step wizard that configures:

1. **Welcome** — system overview and prerequisites
2. **Inference** — llama-server connection test (:8080)
3. **Model** — Qwen3.6-35B-A3B hash verification
4. **Keychain** — Ed25519 signing key generation
5. **Embedding** — bge-base-en-v1.5 availability check
6. **Database init** — SQLite + DuckDB + KuzuDB + Qdrant creation
7. **Universe** — select initial ticker universe from 11 templates
8. **Risk** — confirm $5K paper capital, position limits
9. **Broker** — Alpaca paper trading API key entry
10. **TOTP** — set up authenticator for operator actions
11. **Review** — confirm all settings, run smoke-test cycle

After wizard completes, the dashboard opens with pre-first-cycle state showing "Run smoke-test cycle".

## Alpaca Paper Trading

PMACS connects to Alpaca's paper trading API for order execution in PAPER mode.

### Setup

1. Create Alpaca paper account at `https://app.alpaca.markets/paper/dashboard`
2. Get API Key ID and Secret from the dashboard
3. Enter credentials during first-run wizard (Step 9) or via Settings page

### Connection Details

- **Base URL**: `https://paper-api.alpaca.markets` (paper trading)
- **Data URL**: `https://data.alpaca.markets`
- Orders submitted via `alpaca-py` with `asyncio.to_thread` wrapper
- All orders logged in audit chain before submission

### Order Flow

1. Cycle produces verdict (BUY/SELL/HOLD/SKIP)
2. Arbitration engine confirms position sizing
3. ExecutionService signs with Ed25519
4. BrokerAdapter submits to Alpaca
5. Stop poller monitors for fills
6. Catastrophe-net stop (15% below entry) set at broker level

### Verification

```bash
# Check broker connection
python3 -c "from pmacs.broker.alpaca import AlpacaPaperAdapter; print('OK')"

# Run broker integration tests
python3 -m pytest tests/unit/test_broker_adapter.py -v
```

## Notification Configuration

### Notification Levels

Settings page (§13.5) provides per-event notification level control:

| Event | Default Level | Can Disable? |
|---|---|---|
| Cycle complete | Toast | Yes |
| Trade filled (paper) | Toast | Yes |
| Trade filled (live) | Modal | No |
| Stop-loss triggered | Modal | No |
| Kill switch engaged | Modal | **Never** |
| Audit chain broken | Modal | **Never** |
| Mutation candidate | Toast | Yes |
| Mode promotion eligible | Toast | Yes |
| Process heartbeat stale | Toast | Yes |
| Model integrity failure | Modal | No |
| Disk space warning | Toast | Yes |
| Crucible attack complete | Toast | Yes |

**Non-disableable events** (kill switch, audit chain) always show modal regardless of settings. This is enforced in both backend and frontend.

### Configuration

1. Navigate to Settings page
2. Scroll to "Notifications" section
3. Use dropdown per event: Toast / Modal / Silent
4. Changes persist immediately (no save button needed)
5. Levels survive page reload (stored in SQLite settings table)

## Dark Mode

PMACS supports three appearance modes:

- **System** (default) — follows macOS appearance setting
- **Light** — always light theme
- **Dark** — always dark theme

Toggle via Settings page → "Appearance" → dropdown.

Colors use CSS custom properties that swap between light/dark tokens (Source.md §13.1).

## Keyboard Shortcuts

All shortcuts work from any page. Shortcuts are suppressed when a text input is focused (except Escape).

| Shortcut | Action |
|---|---|
| `Cmd-K` | Open command palette (search tickers, actions, audit entries) |
| `Cmd-1` through `Cmd-7` | Navigate to page (Dashboard, Agents, Pipeline, Universe, Cortex, Debug, Settings) |
| `Cmd-R` | Refresh current page data |
| `Cmd-/` | Show keyboard shortcuts overlay |
| `/` | Focus search/filter on current page |
| `Esc` | Close modal, drawer, or dismiss toast |
| `Cmd-Shift-K` | Open kill switch confirmation (any page) |
| `Cmd-T` | Open TOTP input modal |
| `?` | Show contextual help for current page |

## Cycle Timing

The dashboard shows the last cycle duration next to the Day Change metric. This updates in real-time via SSE when a cycle completes.

Typical cycle times:
- **Empty queue**: < 1s (no tickers to process)
- **Single ticker**: 10-30s (personas + arbitration)
- **Full 16-ticker cycle**: 3-8 minutes

If cycle time exceeds 10 minutes, check the Debug page for slow persona responses or inference timeouts.

## Cycle Compare

Compare two cycles side-by-side from the Dashboard:

1. Click "Compare" button on recent decisions list
2. Select two cycle IDs from dropdowns
3. Side-by-side view shows: verdicts, conviction scores, persona outputs, catalysts
4. Differences highlighted in accent color

Useful for understanding why a thesis changed between cycles.

## TOTP Setup

TOTP is required for all destructive/promotion actions. Setup via:

1. Generate TOTP secret: `python -m pmacs.auth.totp_setup`
2. Scan QR code with authenticator app (Google Authenticator, Authy, etc.)
3. Verify with: `python -m pmacs.auth.totp_verify --code <6-digit-code>`

TOTP is required for:
- Mode promotion (SHADOW+PAPER → PAPER_VALIDATED → LIVE_EARLY → ...)
- Kill switch disengagement
- Mutation approval
- Settings changes (threshold overrides, universe edits)

TOTP is NOT required for:
- Kill switch engagement (it's the safer option)

## Mode Promotion

### Mode Ladder

```
INSTALLING → SHADOW + PAPER → PAPER_VALIDATED → LIVE_EARLY → LIVE_STANDARD → LIVE_EXPANDED
```

### Promotion Gates (Phases.md §3.1)

| From → To | Min Cycles | Min Trades | Brier ≤ | Sharpe ≥ | Drawdown ≤ |
|---|---|---|---|---|---|
| SHADOW+PAPER → PAPER_VALIDATED | 90 | 200 | 0.30 | 0.0 | 15% |
| PAPER_VALIDATED → LIVE_EARLY | 90 | 200 | 0.28 | 0.5 | 12% |
| LIVE_EARLY → LIVE_STANDARD | 90 | 200 | 0.27 | 0.7 | 10% |
| LIVE_STANDARD → LIVE_EXPANDED | 120 | 300 | 0.25 | 0.8 | 8% |

### Promotion Procedure (Source.md §21.5)

1. Dashboard → mode badge → click → mode management modal
2. Modal shows current gate status: cycles, trades, Brier, Sharpe, drawdown
3. All gates must pass simultaneously
4. Click **Promote** → TOTP modal → Submit
5. Audit log records mode change. Mode badge updates.

**Important:** Even when all gates pass, promotion does not happen automatically. The operator decides when to promote.

## Kill Switch Procedures

### Engaging (Source.md §21.6)

1. Top bar → Kill Switch button → click
2. Confirmation modal: "Engage kill switch? All trading halts. Stop-loss continues."
3. Click **Engage** (no TOTP needed)
4. Top bar button turns red. Toast confirms.

**Triggers (automatic):**
- Daily loss > 5%
- Rolling 5-day loss > 10%
- Audit chain broken
- Disk space < 2GB
- Crash loop detected (5 restarts in 60s)
- Any process unable to heartbeat

### Disengaging

1. Cortex page → Kill Switch panel
2. Verify trigger condition resolved
3. Click **Disengage** → modal asks for typed reason + TOTP
4. Submit → audit logs disengagement with operator identity

## Mutation Engine

### Overview

The Mutation Engine proposes improvements to system parameters based on failure pattern analysis. It is an **advisor** — it never auto-applies mutations. All mutations require operator TOTP approval.

### Activation

- Dormant for first 50 PAPER cycles
- Dashboard shows: "Activates after 50 PAPER cycles (current: N)"

### Review Workflow (Source.md §21.4)

1. Dashboard → Mutation Engine card → "N pending operator review" → click
2. Settings → Mutation Engine → Pending candidates
3. Review: dimension, target, diff, sample size, effect size, p-value
4. Click **Promote** → TOTP → Submit
5. 30-cycle probation period. Auto-rollback if regression exceeds baseline.

### Safety Levels

1. **Structural separation**: mutation process cannot write production config
2. **Baseline snapshot**: saved before every promotion
3. **Atomic promotion**: single-config change per mutation
4. **Auto-rollback**: detects regression within 50 cycles
5. **Kill switch review**: flagged mutations require kill switch check

## Backup and Restore

### Backup

```bash
python ops/backup_verify.py backup --data-dir data/ --output backups/
```

Creates timestamped directory with copies of all 5 stores:
- SQLite (`pmacs.db`)
- KuzuDB (`pmacs_graph.kuzu/`)
- Qdrant (`qdrant_storage/`)
- DuckDB (`pmacs_analytics.duckdb`)
- Audit log (`audit.log`)

### Restore

```bash
python ops/backup_verify.py restore --backup-dir backups/pmacs_backup_<timestamp> --data-dir data/
```

1. Wipes current data directory
2. Restores all stores from backup
3. Verify: `python ops/audit_chain_verify.py`

### Full E2E Test

```bash
python ops/backup_verify.py e2e --data-dir data/
```

Runs: backup → wipe → restore → verify → report

## Operator Workflows Quick Reference

| Task | Steps | Page |
|---|---|---|
| Add ticker (§21.1) | Cmd-K → "add ticker" → type symbol → TOTP | Universe |
| Override SKIP (§21.2) | Pipeline → SKIP column → "Run again now" | Pipeline |
| Investigate stop-out (§21.3) | Pipeline → search ticker → detail drawer → failure history | Pipeline |
| Review mutation (§21.4) | Dashboard → Mutation card → review → Promote → TOTP | Settings |
| Promote mode (§21.5) | Dashboard → mode badge → check gates → Promote → TOTP | Dashboard |
| Engage kill switch (§21.6) | Top bar → Kill Switch → Engage (no TOTP) | Any |
| Pre-market check (§21.7) | Cortex → audit chain + heartbeats → Dashboard | Cortex |
| Tag tickers (§21.8) | Universe → group-by → select → bulk tag | Universe |

## Troubleshooting

| Issue | Check | Fix |
|---|---|---|
| Dashboard not loading | `curl localhost:8001` | Restart: `launchctl kickstart -k gui/$(id -u)/pmacs-dashboard` |
| SSE not streaming | `curl localhost:8000/events` | Check pmacs-nervous is running |
| Inference down | Cortex page → heartbeats | `ops/start_inference.sh` |
| Audit chain broken | `python ops/audit_chain_verify.py` | Check output for line number, investigate tampering |
| High memory | `ops/profile_memory.py` | Check for leaky processes, restart if needed |
| Slow cycle | `ops/profile_cycle.py` | Compare against Architecture.md §20.1 budgets |

## Key URLs

| Service | URL |
|---|---|
| Dashboard | `http://localhost:8001` |
| Nervous API | `http://localhost:8000` |
| Inference | `http://localhost:8080` (pf-blocked) |
| SSE stream | `http://localhost:8000/events` |

## Key Constraints

- Paper capital: $5,000
- Max single position: 20% ($1,000)
- Max concurrent positions: 5
- Catastrophe-net stop: 15% below entry (broker-side)
- Crucible time budget: 90s per attack cycle, 2 cycles max
- Mutation activation: after 50 PAPER cycles
- All mutations require operator TOTP
