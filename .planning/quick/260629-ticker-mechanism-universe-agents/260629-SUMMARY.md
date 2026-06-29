---
status: complete
commit: 4a452df
date: 2026-06-29
---

Wrapped the Ticker and Name cells in the /universe table with `<a href="/ticker/{symbol}">` (hover:underline + hover:text-accent), and wrapped the hero `current_ticker` / `last_analyzed_ticker` / `next_ticker` text on /agents with the same link — preserving the existing `id` and `class` attributes on the outer `<span>` so JS selectors (`#current-ticker`, `#next-ticker`) and styles keep working. Pure template edits, no backend or JS changes. Actions column on /universe was already linked and was not touched.
