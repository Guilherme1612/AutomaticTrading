# ADR-004: Five Specialized Storage Backends

## Status

Accepted

## Context

PMACS needs to store and query several distinct data types: transactional state (cycles, holdings, queue items), graph relationships (holding-evidence-resolution-lesson lineage), vector embeddings (thesis similarity search), analytical aggregates (rolling metrics, resolution history), and an immutable audit trail.

SQLite could theoretically handle all of these via extensions (sqlite-vss for vectors, recursive CTEs for graphs, OLAP via careful indexing). The question is whether a single-store approach would degrade as data accumulates over months of operation.

PMACS runs on a single machine. Each store handles roughly 50-200MB of write traffic over a year. This is well within the capacity of any individual store, and well within the 64GB RAM for caching.

## Decision

Use five specialized storage backends, each handling the workload it is designed for:

| Store | Purpose | Access Pattern |
|---|---|---|
| **SQLite** | OLTP: cycles, queue, holdings, stops, mutations, config | Read/write by nervous; read-only by dashboard |
| **KuzuDB** | Graph: Holding-Evidence-Resolution-Lesson-FailedAssumption lineage | Read/write by nervous + engines |
| **Qdrant** | Vector: thesis embeddings, memo embeddings, lesson embeddings | Read/write by nervous + engines |
| **DuckDB** | Analytics: resolution history, rolling metrics, persona affinity | Read/write by engines; read by dashboard |
| **audit.log** | Hash-chained immutable record (see ADR-005) | Append-only by nervous; verify by cortex |

## Consequences

**Positive:**

- Each store is used for its core strength. SQLite handles transactional OLTP (ACID guarantees for cycle state). KuzuDB handles multi-hop graph traversals natively (holding -> evidence -> resolution -> lesson -> failed assumption). Qdrant handles ANN search for thesis similarity. DuckDB handles columnar analytics on resolution history without OLTP overhead.
- No single store is overloaded or misused. Recursive CTEs in SQLite for graph queries are brittle at depth and hard to maintain. sqlite-vss is less battle-tested than Qdrant for vector search. Columnar analytics on a year of resolutions in SQLite would degrade.
- Each store can be independently backed up, migrated, or replaced without affecting the others.

**Negative:**

- Five stores to install, configure, back up, and version-pin. Operational complexity is higher than a single SQLite file.
- Cross-store queries require application-level joins (e.g., resolving a holding's graph lineage + vector similarity + analytical metrics). No single SQL query spans all stores.
- Disk usage is higher due to each store's internal indexing and metadata structures.

**References:** spec/Architecture.md §6 (storage layer), §8 (SQLite schema), §9 (engine schemas), spec/Source.md §4.1 (trust contract).
