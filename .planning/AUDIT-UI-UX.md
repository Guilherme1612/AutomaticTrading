# PMACS Dashboard UI/UX Audit

**Audited:** 2026-05-30
**Scope:** Dashboard templates, static assets, SSE flow, components, routes
**Baseline:** Source.md 13-21, Architecture.md 4.4, abstract 6-pillar standards
**Screenshots:** Not captured (no dev server running on ports 3000/8001)

---

## Executive Summary

The PMACS dashboard is a well-structured, spec-compliant single-page application with consistent design tokens, thorough error handling, and robust SSE event routing. The architecture correctly chains: engine -> nervous SSE publisher -> dashboard SSEClient (server-side) -> browser EventSource. Security is solid with CSRF double-submit, security headers, TOTP gating, and Jinja2 autoescaping enabled.

**However, several concrete bugs and UX gaps were found.** The most critical is a broken SSE event chain in agents.html, and dark mode inconsistencies across multiple pages.

---

## Critical Findings

### C1. Dead SSE Handlers in agents.html (BROKEN EVENT CHAIN)

**Files:** `pmacs/web/templates/agents.html:262,289`

agents.html registers two SSE handlers at the bottom of the page:

```javascript
onSSE("persona.status", function (data) { ... });
onSSE("persona.result", function (data) { ... });
```

The SSE publisher only emits events on 6 streams: `cycle`, `agent`, `decision`, `trade`, `mutation`, `system`. The streams `"persona.status"` and `"persona.result"` do not exist. These handlers will never fire.

**Mitigating factor:** app.js (line 1383) already has a correct handler on the `"agent"` stream that updates persona cards via `[data-persona]` selectors. The agents.html handlers are dead code that duplicates work already done correctly.

**Impact:** No user-visible breakage because the app.js handler covers the same ground. But the agents.html handlers consume memory, add confusion, and would break if someone relied on them.

**Fix:** Remove the two dead `onSSE()` calls from agents.html (lines 261-296). The app.js `onSSE("agent", ...)` handler already does this work.

---

### C2. Dark Mode Not Applied to Multiple Pages

**Files:** cortex.html, settings.html, pipeline.html, universe.html, debug.html, compare.html, dashboard.html

The base.html correctly defines CSS custom properties for both light and dark modes (lines 59-88). The style.css has dark mode overrides for `.dark .bg-white`, `.dark .text-zinc-900`, etc. (lines 17-51).

**However**, most page-level templates use hardcoded Tailwind classes like `bg-white`, `text-zinc-900`, `text-zinc-500` directly instead of the token classes (`bg-surface-elevated`, `text-text-primary`, `text-text-secondary`).

The style.css compensates with explicit dark mode overrides:

```css
.dark .bg-white { background-color: var(--surface-elevated); }
.dark .text-zinc-900 { color: var(--text-primary); }
```

This approach is fragile but functional. The dark mode works through the CSS overrides, not through the token system. **The risk is that any new component using `bg-white` that isn't covered by the style.css overrides will break dark mode.**

Pages using tokens correctly (base.html): `bg-surface`, `bg-surface-elevated`, `text-text-primary`, `text-text-secondary`, `text-text-muted`, `border-border`

Pages using raw Tailwind (needs CSS overrides): `bg-white`, `text-zinc-900`, `text-zinc-500`, `text-zinc-400`, `border-zinc-200`

**Affected component counts per page:**
- cortex.html: 6 panels with `bg-white`
- settings.html: 10+ sections with `bg-white`
- pipeline.html: kanban cards with `bg-white`
- universe.html: table with `bg-white`
- debug.html: event stream with `bg-white`
- compare.html: `bg-white` in comparison cards
- dashboard.html: portfolio card, positions table, health card with `bg-white`

**Fix:** No immediate breakage because style.css covers the common cases. Long-term: migrate page templates from raw `bg-white` to `bg-surface-elevated`, `text-zinc-900` to `text-text-primary`, etc.

---

### C3. `cancelLoading()` Function Referenced but Never Defined

**File:** `pmacs/web/components/loading_state.html:25`

The loading_state component renders a Cancel button:

```html
<button onclick="cancelLoading()" ...>Cancel</button>
```

The function `cancelLoading()` is never defined anywhere in app.js or any template. Clicking this button will throw a `ReferenceError` in the console and do nothing.

**Fix:** Either define `cancelLoading()` in app.js (to abort the current HTMX request or navigate away), or remove the button from the component.

---

## High-Priority Findings

### H1. Hardcoded Colors in SVG and Inline Styles

**Files:**
- `dashboard.html:41,104` -- `stroke="#2563eb"` in sparkline SVGs
- `style.css:304,351,353,356,359` -- `#2563eb`, `#16a34a`, `#dc2626` in persona progress bars
- `app.js:1466,1527` -- `stroke="#2563eb"` in sparkline SVG generation
- `pipeline.html:341` -- `'2px dashed #2563eb'` in drag-over outline

These hardcoded hex colors bypass the CSS variable system and will not update when the theme changes to dark mode or a different accent color.

**Fix:** Replace `#2563eb` with `var(--accent)` or `currentColor` where applicable. For SVG polylines, use `stroke="var(--accent)"`.

---

### H2. `prompt()` Used for Ticker Addition and Subsector Tagging

**File:** `pmacs/web/templates/universe.html:195,255`

```javascript
var ticker = prompt("Enter ticker symbol (e.g. AAPL):");
var subsector = prompt("Enter sub-sector for " + selected.length + " ticker(s):");
```

`window.prompt()` is a blocking, non-stylable browser dialog. It breaks the visual identity of the application, cannot be themed, and provides no input validation or feedback. For a desktop tool targeting a single operator this is low-severity, but it contradicts the custom TOTP modal pattern used elsewhere.

**Fix:** Replace with inline input fields or a small modal dialog consistent with the TOTP modal pattern.

---

### H3. innerHTML Usage Without Sanitization (Low XSS Risk)

**Files:** `app.js:495,570,1449,1465,1509,1525`, `compare.html:165-171`

Multiple places use `innerHTML` with dynamically constructed strings. However, audit shows:

1. `compare.html:177` defines an `escapeHtml()` function that is used for ticker names and verdicts
2. `app.js:11-15` defines a global `escapeHtml()` that is used in command palette rendering (line 502)
3. Template variables are Jinja2-autoescaped (confirmed in `app.py:167-169`)

**Risk assessment:** LOW. User-controlled data (ticker symbols) passes through `escapeHtml()`. The template autoescaping is enabled. However, app.js line 570 constructs the cycle-compare modal HTML with template literals that include `item.name` values from hardcoded arrays, not user input.

**Specific concern at compare.html:165-171:** The `renderResults` function builds table rows with `innerHTML`. Ticker names and verdicts are passed through `escapeHtml()`, which is correct. However, `t.conviction_a.toFixed(2)` and `t.conviction_b.toFixed(2)` are not escaped -- if the server returns non-numeric values, this could throw or produce unexpected output.

**Fix:** Wrap numeric `.toFixed()` calls in try/catch, and ensure server always returns valid numbers.

---

### H4. Missing Focus Trap for Settings Diff Modal and Blocking Modal

**File:** `app.js:1691-1712`

The diff modal in settings.html (line 745) does not have focus trap wiring. When opened via `viewDiff()`, Tab key navigation can escape to the background content.

The blocking modal (base.html:314) has focus trap wiring via MutationObserver (app.js:1694-1712), which is correct.

However, the shortcut overlay (base.html:212) also lacks explicit focus trap wiring -- it relies on manual tabindex management.

**Fix:** Add `trapFocus()` calls to `viewDiff()` and the shortcut overlay open handler.

---

## Medium-Priority Findings

### M1. Double Registration of Cycle SSE Handler

**File:** `app.js`

The `"cycle"` stream has three separate `onSSE()` registrations:
1. Line 1320: Main cycle events (open/close/progress)
2. Line 1431: Sparkline updates
3. Line 1617: Cycle timing measurement

Each registration adds to an array of handlers. This is technically correct (the `onSSE` function appends to `eventHandlers[stream]`), but it means every cycle event triggers three separate handler functions. For performance, these could be consolidated into one handler.

---

### M2. Debug Page Clear Button Has No Wiring

**File:** `pmacs/web/templates/debug.html:34`

```html
<button class="px-3 py-1.5 text-sm border border-zinc-200 rounded hover:bg-zinc-50 transition-colors">
    Clear
</button>
```

This button has no `onclick` handler, no `id`, and no `data-` attribute. It does nothing when clicked.

**Fix:** Wire it to clear the event stream display or reset filters.

---

### M3. Debug Page Search Not Wired to Event Filtering

**File:** `pmacs/web/templates/debug.html:30-32`

The search input has `data-page-search` attribute but there is no JS handler to filter debug events on input. The `filterEvents()` function only filters by level (ERROR/WARN/INFO/DEBUG), not by text search.

---

### M4. No Loading State Between HTMX Navigation

The base.html has `hx-boost="true"` on the body and `hx-target="#main-content"` on sidebar links. When HTMX swaps page content, the HTMX indicator spinner appears (base.html:326). However, the `hx-swap="innerHTML"` on sidebar links means only the `#main-content` div is replaced, not the full page. Inline `<script>` blocks in page templates (agents.html, pipeline.html, settings.html, etc.) will NOT execute after an HTMX swap because `innerHTML` assignment does not execute scripts.

**Impact:** Page-specific JavaScript (drag-and-drop, queue management, sankey initialization) will not work after HTMX navigation. The user must do a full page reload.

**Mitigating factor:** The app.js `htmx:afterSwap` handler (line 1716) re-initializes some things (SSE, sidebar state, sankey). But inline scripts in templates are not re-executed.

**Fix:** Move all page-specific JS initialization into functions callable from the `htmx:afterSwap` handler, or use `<script type="text/javascript">` with HTMX's `hx-ext="allow-scripts"`.

---

### M5. SSEClient Ignores Named Events

**File:** `pmacs/web/sse_client.py:52`

The SSEClient only processes lines starting with `"data:"`. The nervous API sends events with both `event:` and `data:` fields. The SSEClient correctly ignores the `event:` line and reads only the `data:` line, which contains the merged `stream` and `event_type` fields. This is correct behavior.

However, the SSEClient also ignores `id:` lines from nervous, which means it cannot support `Last-Event-ID` reconnection on the nervous-to-dashboard leg. The dashboard-to-browser leg does support reconnection via `last_event_id` query parameter.

---

### M6. Cortex Kill Switch Button Missing TOTP Gate on Engage

**File:** `pmacs/web/templates/cortex.html:107-111`

The Cortex page kill switch button has no `onclick` handler. It renders visually but clicking it does nothing. The header kill switch button (base.html:134) correctly calls `handleKillSwitch()`.

**Fix:** Add `onclick="handleKillSwitch()"` to the Cortex kill switch button.

---

## Low-Priority Findings

### L1. Wizard Templates Duplicate Color Variables

**Files:** `wizard/step01_welcome.html`, `wizard/layout.html`

Both files copy the full set of CSS custom properties from base.html. If the base tokens change, these must be updated independently. Consider extracting into a shared CSS file.

### L2. No Print Stylesheet (Intentional)

`style.css:435-437` hides all content with `body { display: none }` in print mode. This is intentional for a desktop trading tool.

### L3. `components.json` Check (Registry Audit)

No `components.json` file exists in the project root. No shadcn registry audit needed.

---

## Positive Findings (What Works Well)

1. **Security:** CSRF double-submit cookie, CSP headers (with necessary `unsafe-inline` for HTMX), HttpOnly session cookies, TOTP rate limiting, Jinja2 autoescaping enabled.

2. **SSE Architecture:** Correct three-hop relay (nervous -> dashboard SSEClient -> browser EventSource) with proper event merging, keepalive comments, and exponential backoff reconnection.

3. **Accessibility:** Skip-to-content link, ARIA roles on navigation/dialogs/toasts, `aria-live` regions for dynamic content, `focus-visible` outlines, reduced-motion support, keyboard shortcut overlay, focus traps on modals.

4. **Error Handling:** Every page template checks for error state and renders the error_state component. The error_state component includes error code, description, explanation expander, suggested actions, spec reference, and "Copy for Claude Code" button.

5. **Design Token System:** CSS custom properties for light/dark modes, Tailwind config extending with custom tokens (surface, accent, positive, negative, warning, etc.).

6. **TOTP Modal:** Well-implemented parameterized modal with auto-advance digit inputs, paste support, confirmation text for destructive actions, and correct focus trap wiring.

7. **Notification Policy:** Comprehensive event-to-surface mapping with configurable levels, non-disableable critical events (kill switch, audit chain failure), and sound effects.

---

## Files Audited

### Templates
- `/pmacs/web/templates/base.html` (339 lines)
- `/pmacs/web/templates/dashboard.html` (270 lines)
- `/pmacs/web/templates/agents.html` (305 lines)
- `/pmacs/web/templates/cortex.html` (157 lines)
- `/pmacs/web/templates/universe.html` (318 lines)
- `/pmacs/web/templates/pipeline.html` (612 lines)
- `/pmacs/web/templates/settings.html` (764 lines)
- `/pmacs/web/templates/debug.html` (122 lines)
- `/pmacs/web/templates/compare.html` (185 lines)

### Components
- `/pmacs/web/components/card.html`
- `/pmacs/web/components/ticker_chip.html`
- `/pmacs/web/components/error_state.html`
- `/pmacs/web/components/loading_state.html`
- `/pmacs/web/components/empty_state.html`
- `/pmacs/web/components/statblock.html`
- `/pmacs/web/components/totp_modal.html`

### Static Assets
- `/pmacs/web/static/app.js` (1775 lines)
- `/pmacs/web/static/style.css` (488 lines)

### Backend (SSE + Web)
- `/pmacs/nervous/sse_publisher.py` (143 lines)
- `/pmacs/nervous/api.py` (291 lines)
- `/pmacs/web/app.py` (364 lines)
- `/pmacs/web/sse_client.py` (81 lines)
- `/pmacs/web/data.py` (938 lines)
- `/pmacs/web/routes/cortex.py` (TOTP verify endpoint)
