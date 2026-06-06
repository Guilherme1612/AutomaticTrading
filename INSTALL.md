# PMACS Installation Guide

## Prerequisites

- Python 3.11+
- Git
- An Anthropic API key (or OpenAI / OpenRouter)

## Quick Start

```bash
# 1. Clone
git clone <repo-url> && cd AutomaticTrading

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 5. Initialize databases (creates ./data/)
pmacs init

# 6. Run setup wizard
pmacs setup

# 7. Start the dashboard
pmacs start
```

Dashboard runs at http://127.0.0.1:8001

## Data Directory

All runtime data lives in `./data/` relative to the project root:

```
data/
  pmacs.db                  # SQLite (cycles, holdings, queue)
  pmacs_analytics.duckdb    # DuckDB (analytics, metrics)
  heartbeats/               # Process heartbeat files
  pids/                     # PID files
  audit.log                 # Hash-chained audit trail
  debug.jsonl               # Debug event log
```

Override with: `export PMACS_DATA_DIR=/custom/path`

## LLM Backend

Default: **Anthropic API** (no local model needed).

Configure in `config/model_registry.json` → `"active"` field:
- `"anthropic"` — Claude (default)
- `"openai"` — GPT-4o
- `"openrouter"` — OpenRouter
- `"llama_server"` — Local GGUF via llama.cpp
- `"ollama"` — Local via Ollama

### API Keys

Store in environment variables or macOS Keychain:
```bash
# Environment variable
export ANTHROPIC_API_KEY=sk-ant-...

# Or macOS Keychain
security add-generic-password -a "pmacs" -s "pmacs.credentials.anthropic_api_key" -w "sk-ant-..."
```

### Local Model (Optional)

If switching to `llama_server`:
1. Download a GGUF model (~21GB for Qwen3.6-35B)
2. Set `config/resources.toml` → `gguf_path` to the model path
3. Set `"active": "llama_server"` in `config/model_registry.json`
4. Run `ops/start_inference.sh` to start llama-server

## Running Tests

```bash
# All tests
pytest tests/

# Unit only
pytest tests/unit/

# Skip e2e (requires full stack)
pytest tests/ -k "not e2e"
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError` | Run `pip install -e ".[dev]"` |
| `data/` missing | Run `pmacs init` |
| API key errors | Set `ANTHROPIC_API_KEY` env var |
| Port 8001 in use | `lsof -i :8001` then kill the process |
