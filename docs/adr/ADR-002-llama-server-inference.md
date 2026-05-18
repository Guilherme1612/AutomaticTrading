# ADR-002: llama-server Primary, Ollama Secondary Inference Backend

## Status

Accepted

## Context

PMACS runs local LLM inference on an Apple M1 Max with 64GB unified memory. The system uses Qwen3.6-35B-A3B as its base model (GGUF format). Two local inference servers can serve this model: llama-server (from llama.cpp) and Ollama.

The operator initially preferred Ollama for its ease of use (one-command install, automatic model management). However, PMACS has specific requirements: structured output via grammars, multi-slot parallelism (up to 3 concurrent persona calls), thinking-mode control, and GGUF hash verification for model integrity.

The inference process (`pmacs-inference`) runs on port :8080 and is pf-firewall-blocked from internet egress (spec/Architecture.md §18.2). This is a non-negotiable security requirement: inference must never phone home.

## Decision

Use llama-server as the primary inference backend. Ollama is supported as a secondary fallback, selectable via `config/model_registry.json`.

llama-server is started with the configured GGUF and serves on :8080. Model integrity is verified on startup by comparing the GGUF file's SHA256 against `config/model_hashes.toml`. If the hash mismatches, cortex aborts startup.

## Consequences

**Positive:**

- llama-server provides native GBNF grammars, which are strictly more expressive than Ollama's JSON Schema. GBNF allows token-level constraints (character-class regexes on evidence_id format, enum-only strings for verdict fields). This catches structural violations before the LLM finishes generating.
- True 3-slot parallelism on 64GB M1 Max. Three persona calls can run concurrently within a single cycle, reducing cycle time from ~9 minutes sequential to ~3-4 minutes parallel.
- Direct `enable_thinking` control for per-persona thinking-mode toggling without wrapper layers.
- No third-party repackaging of GGUF files. llama-server loads standard upstream Qwen GGUF directly, giving the operator full control over quantization choice (UD-Q4_K_XL selected for balance of quality and memory).
- pf firewall rules block the inference process from all outbound network connections, enforcing the local-only execution requirement.

**Negative:**

- llama-server requires manual GGUF management (download, verify SHA256, configure path). Ollama handles this automatically.
- GBNF grammar development is iterative and persona-specific. Each of the 9 personas needs its own grammar file in `pmacs/agents/grammars/`. JSON Schema equivalents must also be maintained in `pmacs/agents/schemas_json/` for Ollama compatibility.
- llama-server has fewer convenience features than Ollama (no auto-download, no model listing API). This is acceptable for a single-model deployment.

**References:** spec/Architecture.md §3.2 (pmacs-inference), §18.2 (network isolation), spec/Phases.md Phase 3 (exit test), spec/Agents.md §3 (three-layer contract).
