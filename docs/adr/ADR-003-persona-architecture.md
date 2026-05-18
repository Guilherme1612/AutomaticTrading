# ADR-003: Multi-Persona Architecture on a Single Base Model

## Status

Accepted

## Context

PMACS employs multiple specialized analysis "personas" that examine evidence from different analytical perspectives: technical analyst, sentiment analyst, macro analyst, sector analyst, growth hunter, insider activity, sleep watch, memo writer, and the Crucible adversarial persona. Previous versions ranged from 3 personas (v3.5) to 9 (v6).

Running separate models per persona would provide maximum specialization but would multiply RAM consumption (each model instance at Q4_K_XL uses ~20GB). On a 64GB M1 Max with 3-slot parallelism, this is infeasible beyond 2-3 models simultaneously.

The alternative is to run one base model and differentiate personas through system prompts, GBNF grammars (constraining output structure), and sanity validators (enforcing semantic rules). This is the standard approach for multi-agent LLM systems.

## Decision

Use a single base model (Qwen3.6-35B-A3B) for all personas. Differentiation is achieved through three layers:

1. **Grammar layer** -- GBNF files in `pmacs/agents/grammars/<persona>.gbnf` constrain token-level output. The LLM physically cannot produce tokens outside the grammar. JSON Schema equivalents maintained in `pmacs/agents/schemas_json/` for Ollama compatibility.
2. **Pydantic layer** -- `model_validate()` enforces structural shape. Catches anything that slips past the grammar.
3. **Sanity validator** -- `pmacs/agents/sanity/<persona>.py` enforces semantic rules (e.g., probabilities sum to 1.0 within 1e-6, evidence_ids reference real evidence, citations resolve).

System prompts live in `pmacs/agents/prompts/<persona>.md`. Temperature is 0.2 for analysis personas, 0.1 for Crucible, 0.3 for MemoWriter.

On any layer failure: retry 2x with +0.05 temperature, then abort that persona for the cycle.

## Consequences

**Positive:**

- All 9 personas share the same 20GB model in memory. With 3-slot parallelism, the full persona roster can run in 3 batches per cycle.
- Persona outputs are meaningfully different because system prompts, grammars, and sanity rules differ substantially. The Growth Hunter produces structured catalyst assessments; the Sentiment Analyst produces probability-weighted narrative readings; the Crucible produces adversarial challenges.
- The Mutation Engine's persona-affinity dimension (Architecture.md §10.2) compensates for any base-model uniformity bias by adjusting per-persona-per-ticker weights based on historical track record.
- Adding a new persona requires four files: runner, prompt, grammar, sanity validator. No model training or fine-tuning.

**Negative:**

- All personas share the same base model's limitations (knowledge cutoff, reasoning patterns). If the base model has a systematic bias (e.g., always optimistic on tech), all personas inherit it.
- Persona differentiation relies entirely on prompt engineering and output constraints. Poorly written prompts produce similar outputs across personas.
- Three-layer validation adds complexity. Each persona needs a grammar file, a Pydantic schema, and a sanity validator that must stay in sync.

**References:** spec/Architecture.md §1.7 (structured output + sanity), §5 (persona pipeline), spec/Agents.md §3 (three-layer contract), §4-13 (per-persona specifications).
