# PMACS Dashboard — UI Bug Report

**Audited:** 2026-06-02
**Scope:** `pmacs/web/` — all templates, JS, CSS
**Dev server:** Not running (code-only audit)

---

## CRITICAL

### C-1 — `diff-modal` referenced before it exists in `settings.html`

`viewDiff()` calls `document.getElementById('diff-modal')` and `document.getElementById('diff-container')` at the top of the function body. The diff modal markup appears at the very bottom of `settings.html` (line 751), but `viewDiff()` is defined in an earlier `<script>` block (line 591). If the function is ever called before the DOM is fully parsed (e.g., via the Cmd-K palette action), both getElementById calls return null and the function silently fails — no error, no feedback to the operator.

**File:** `pmacs/web/templates/settings.html` lines 591-603 and 751-766
**Fix:** Null-check both elements at the top of `viewDiff()` and show a toast on failure:
```js
var modal = document.getElementById('diff-modal');
var container = document.getElementById('diff-container');
if (!modal || !container) { showToast('Diff modal not available', 'error'); return; }
```

---

### C-2 — `toggleEventDetail` targets wrong selector — detail rows never expand

`app.js` line 1262:
```js
function toggleEventDetail(el) {
    var detail = el.querySelector(".event-detail-row");
```
But `debug.html` line 74 uses class `event-detail-row hidden`. The function queries for `.event-detail-row`, which exists. However, the _parent_ of `toggleEventDetail` is the entire row `div` (which has `onclick="toggleEventDetail(this)"`), and the hidden state is controlled by the `.hidden` class. The function calls `classList.toggle("hidden")` nowhere — it calls `classList.toggle("hidden")` ... wait — it does not. It calls:
```js
detail.classList.toggle("hidden");
```
That is correct. **However**, the CSS in `style.css` defines `.event-detail` (no `-row` suffix, lines 209-217) for max-height animation, but the template uses class `event-detail-row` (not `event-detail`). The `.event-detail.open` class is never applied by `toggleEventDetail` — it only toggles `.hidden`. The CSS transition (`max-height: 0 → 500px`) is dead code; rows appear/disappear with a hard cut instead of the intended slide animation. Minor visual regression; rows do reveal but without the transition.

**File:** `pmacs/web/static/style.css` lines 209-217; `pmacs/web/templates/debug.html` line 74; `pmacs/web/static/app.js` line 1263
**Fix:** Rename `.event-detail-row` to `.event-detail` in debug.html, then update `toggleEventDetail` to toggle `.open` instead of `.hidden` (or add `.hidden` override in CSS for the `.open` rule).

---

### C-3 — `openCycleCompare` modal uses raw `bg-white` / `text-zinc-900` — invisible in dark mode

`app.js` lines 681-693: the dynamically-created cycle compare modal is hardcoded with:
```js
'<div class="bg-white rounded-lg shadow-xl w-full max-w-2xl border border-zinc-200 p-6">'
'<h3 class="text-lg font-semibold text-zinc-900 mb-4">Compare Cycles</h3>'
```
In dark mode the background stays white with white text — the modal is effectively invisible. The CSS dark overrides target `.bg-white` → `var(--surface-elevated)` (style.css line 63) only on elements inside `.dark` wrapper, which does apply. Actually CSS line 63 is `.dark .bg-white { background-color: var(--surface-elevated); }` so the background does switch. But `text-zinc-900` has no dark override and renders dark text on dark background. Also `border-zinc-200` has no dark override.

**File:** `pmacs/web/static/app.js` lines 681-693
**Fix:** Replace all `bg-white`, `text-zinc-*`, `border-zinc-*` in the dynamically-built modal HTML with design-token classes: `bg-surface-elevated`, `text-text-primary`, `text-text-secondary`, `border-border`.

---

## HIGH

### H-1 — Shortcut key table lists two different actions mapped to key "K"

`base.html` lines 329-333: the keyboard shortcuts overlay lists both "Command palette" → `K` and "Kill switch" → `K` in the Actions column. The actual shortcut for kill switch is `Cmd-Shift-K` (app.js line 820), not bare `K`. This misleads the operator into thinking pressing `K` triggers the kill switch, when it actually opens the command palette.

**File:** `pmacs/web/templates/base.html` line 330
**Fix:** Change the Kill switch row key from `K` to `Shift-K` (or the full `⌘⇧K` representation).

---

### H-2 — `sparkline-point` dot positioned at `left:100%; top:{lastY}px` but container is not `position:relative`

`app.js` lines 1628-1633:
```js
'<div class="sparkline-point absolute w-1.5 h-1.5 rounded-full -translate-x-1/2 -translate-y-1/2' +
' ..." style="left:100%;top:' + lastY + 'px">'
```
`lastY` is computed in SVG viewBox units (0-40), but the rendered container is 40px tall, so `top` values between 2 and 38 are roughly correct. However, `left:100%` positions the dot outside the right edge of the sparkline container. The container div has class `sparkline-container` which has `position:relative` in CSS (line 527), so absolute positioning works. But `left:100%` puts the dot completely outside the visible area — it should be `left:calc(100% - 3px)` or similar, or `right:0`. Combined with `-translate-x-1/2`, `left:100%` actually moves the center to the far right edge, which is correct — but the dot is clipped by `overflow:hidden` on ancestor cards (`card` has `border-radius:16px` which clips corners). Additionally `lastY` is calculated as SVG y-coordinate pixels but `top` is CSS pixels — if the SVG height is set to 40px via `style="height:40px"` and viewBox is `0 40`, the coordinate space matches, so this is actually consistent. The more relevant bug: `-translate-x-1/2 -translate-y-1/2` are Tailwind utility classes which require the Tailwind CDN to process them — but since Tailwind CDN (tailwind.min.js Play CDN) scans the DOM at runtime, dynamically-inserted classes via `innerHTML` are NOT detected by Play CDN's JIT scanner. The dot will render without the translate, making it appear shifted.

**File:** `pmacs/web/static/app.js` lines 1628-1633
**Fix:** Apply the transforms via inline style instead of Tailwind classes:
```js
'" style="left:100%;top:' + lastY + 'px;transform:translate(-50%,-50%)">'
```
Remove the Tailwind translate classes.

---

### H-3 — Kanban `filterPipelineCards()` selects by `select` with no `id` — fragile and breaks if page has other selects

`pipeline.html` line 562:
```js
var verdictSelect = document.querySelector('select');
```
The pipeline page has two selects in the priority queue scheme section (line 134 area) and the verdict filter. Using `document.querySelector('select')` picks the first `<select>` in DOM order, which may not be the verdict filter if DOM order changes. Currently the verdict select appears first in the template, but the scheme-related `select` is not present (it only has text inputs). However, the verdict select has no `id` or `data-` attribute, making targeting unreliable.

**File:** `pmacs/web/templates/pipeline.html` line 28, line 562
**Fix:** Add `id="verdict-filter-select"` to the select element and use `document.getElementById('verdict-filter-select')` in `filterPipelineCards()`.

---

### H-4 — `promoteQueueHead()` in agents page shows toast but does NOT actually promote — no API call

`agents.html` lines 273-279:
```js
function promoteQueueHead() {
    var list = document.getElementById("queue-list");
    var first = list.querySelector("[data-ticker]");
    if (!first) return;
    var ticker = first.getAttribute("data-ticker");
    showToast(ticker + " promoted to next cycle head", "success", 2000);
}
```
No `fetch()` call. The operator sees a success toast but no state change is persisted. The function signature says "Promote P1 Head" but it only shows a toast.

**File:** `pmacs/web/templates/agents.html` lines 273-279
**Fix:** Add a `fetch('/pipeline/queue/promote', ...)` call similar to the pipeline page's `promoteAllP1()`.

---

### H-5 — TOTP modal backdrop does not close when clicking outside

`totp_modal.html`: the modal overlay is `id="totp-modal"` with class `fixed inset-0 ... flex items-center justify-center`. There is no `onclick` on the outer div to dismiss it when clicking the backdrop (unlike the cycle-compare modal and read-more modal which both have `onclick` on the backdrop). The only way to close is via the Cancel button or Escape key.

This is by design for security (TOTP is a gated action), but inconsistency with other modals creates UX confusion. At minimum, add a comment. If intentional, document in the template that backdrop-click-dismiss is deliberately omitted.

**File:** `pmacs/web/templates/components/totp_modal.html` line 12-21
**Severity note:** Downgraded from HIGH to MEDIUM if intentional — but as-is there is no comment explaining the omission, and the modal has `role="dialog"` without `aria-describedby` pointing to the consequences text.

---

### H-6 — `onBandDrop` drops chip to wrong band container when ticker appears in multiple DOM locations

`pipeline.html` line 365:
```js
var chip = document.querySelector('[data-ticker="' + ticker + '"].queue-chip');
```
`querySelector` returns the FIRST matching element. If the same ticker appears in both the Kanban columns (which use `data-ticker` on kanban cards) and the priority queue chips, the wrong element is moved. Kanban cards have `data-ticker` but not `.queue-chip`, so the selector is safe. However, if a ticker is pinned in two bands (a data integrity issue but possible), `querySelector` silently moves only the first.

**File:** `pmacs/web/templates/pipeline.html` line 365
**Fix:** Use `querySelectorAll` and verify `data-band` matches `fromBand` before moving.

---

### H-7 — `renderDiff` uses unsanitized `r['class']` and `r.baseline`/`r.candidate` from API response as innerHTML

`settings.html` lines 619-623:
```js
html += '<tr class="' + r['class'] + '"><td ... >' + r.baseline + '</td><td ...>' + r.candidate + '</td></tr>';
```
`r['class']`, `r.baseline`, and `r.candidate` are inserted directly as raw HTML. If the diff API returns content with `<script>` tags or `"` in the class field, this is an XSS vector. The dashboard is loopback-only, but this is still a bad pattern especially if the mutation engine writes attacker-influenced content.

**File:** `pmacs/web/templates/settings.html` lines 619-623
**Fix:** Escape all three values using the `escapeHtml()` utility before inserting.

---

## MEDIUM

### M-1 — `cycle-running-indicator` div has `hidden` class AND inline `flex` via Tailwind — double-declaration conflict

`dashboard.html` line 175:
```html
<div id="cycle-running-indicator" class="hidden flex items-center gap-2">
```
`hidden` sets `display:none !important`. The `flex` class sets `display:flex`. Because `hidden` uses `!important`, `flex` is overridden. When JS removes `hidden`, the element gets `display:flex` from the `flex` class — this works correctly. But in Tailwind CDN Play mode, `hidden flex` on the same element can produce confusing behavior during SSR hydration. The correct pattern is to use only `hidden` for the initial state and remove it via JS, which is what happens. Not a hard bug but worth noting for clarity — the `flex` class is redundant while `hidden` is present and relies on JS removal working correctly.

---

### M-2 — `cmd-k` palette results use `hover:bg-zinc-100` — not design-token-aware, breaks dark mode

`app.js` lines 578-584:
```js
li.className = "flex items-center px-4 py-2.5 cursor-pointer hover:bg-zinc-100 text-sm";
```
`bg-zinc-100` is a hardcoded color that does not respect the dark/light token system. In dark mode, hovering a palette result produces a light grey background on a dark surface — visible contrast failure.

Also `updateCmdKActiveItem` (line 611): `li.classList.add("bg-zinc-100")` has the same problem.

**File:** `pmacs/web/static/app.js` lines 578, 611, 614
**Fix:** Replace `bg-zinc-100` with `bg-surface-sunken` (or the CSS custom property via inline style) throughout the palette result rendering.

---

### M-3 — `anim-breathe` class on status dots in `agents.html` line 62 does not exist; correct class is `anim-breathing`

`agents.html` line 62:
```html
<span class="w-2 h-2 rounded-full bg-blue-500 anim-breathing"></span>
```
This one is correct. But `base.html` line 289 uses:
```html
<span class="w-1.5 h-1.5 rounded-full bg-positive anim-breathe"></span>
```
CSS defines `@keyframes breathe` and classes `.anim-breathe, .anim-breathing` (style.css line 850) — both exist, so this is fine. Confirmed: no bug here.

---

### M-4 — `agents.html`: `escapeHtml` redefined locally, conflicts with global definition in `app.js`

`agents.html` lines 399-403 define a local `escapeHtml` inside a `DOMContentLoaded` callback:
```js
function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
```
`app.js` line 11 defines a global `escapeHtml` that does the same. The local definition shadows the global within the callback scope, but the global is still accessible outside it. The local version also escapes `"` (quotes) while the global does not — this inconsistency means XSS protection differs between contexts. If an agent name or analysis text contains `"`, the local version escapes it; the global does not.

**File:** `pmacs/web/templates/agents.html` line 399; `pmacs/web/static/app.js` line 11
**Fix:** Remove the local redefinition. Update `app.js`'s global `escapeHtml` to also escape `"` for consistency.

---

### M-5 — `cost_widget.html` / `cost_settings.html` included in dashboard but not audited — likely contain additional issues

`dashboard.html` line 97 does `{% include "cost_widget.html" %}` and `settings.html` line 463 does `{% include "cost_settings.html" %}`. Both files exist in the templates directory but were not included in the files list provided. These templates could contain additional bugs.

**Fix:** Extend this audit to cover `cost_widget.html`, `cost_settings.html`, `compare.html`, `memo.html`, and `universe.html`.

---

### M-6 — `cortex.html` line 252: `onclick` attribute contains inline JSON with unescaped double quotes — HTML attribute value breaks

`cortex.html` line 252:
```html
onclick="{{ 'open_totp_modal({actionId: \"cortex.kill_switch_disengage\", description: \"Disengage kill switch\", ...' if kill_switch.engaged else 'handleKillSwitch()' }}"
```
Jinja2 renders the backslash-escaped quotes as literal `\"` inside the HTML attribute value. The attribute is wrapped in double quotes (`onclick="..."`). The `\"` sequences inside the attribute string are valid JSON but NOT valid HTML attribute escaping — the `"` character terminates the HTML attribute. Browsers parse this incorrectly; the onclick fires a malformed JS expression or not at all when `kill_switch.engaged` is true.

**File:** `pmacs/web/templates/cortex.html` line 252
**Fix:** Move the kill-switch-disengage TOTP call to a named JS function and call it from the onclick:
```html
<button onclick="killSwitchAction()">
```
with a page-level script block that defines `killSwitchAction()` based on the server-rendered state.

---

### M-7 — `base.html` tailwind breakpoint remapping collides with standard Tailwind breakpoints

`base.html` lines 66-69:
```js
screens: {
    'xs': '1024px',
    'sm': '1280px',
    'md': '1440px',
}
```
This completely removes Tailwind's standard `sm` (640px), `md` (768px), and `lg` (1024px) breakpoints. Templates use classes like `sm:col-span-2` (dashboard.html line 191), `sm:grid-cols-3` (dashboard.html line 189), `lg:hidden` (base.html line 191), `lg:inline` (base.html line 246), etc.

- `sm:col-span-2` now activates at 1280px instead of 640px — the two-column layout only appears on large monitors.
- `lg:hidden` / `lg:inline` / `lg:px-6` reference `lg` which is NOT defined in the custom config — Tailwind CDN falls back to the default `lg=1024px` when it's not overridden, but since `sm` and `md` are remapped, the breakpoint ladder is `xs=1024, sm=1280, md=1440, lg=1024` — `lg` is identical to `xs`, causing redundancy.
- On tablet (768-1023px), none of the custom breakpoints fire; `lg` (defaulting to 1024px) also does not fire. The sidebar hamburger button uses `lg:hidden` — on tablet (768-1023px), `lg` does not fire (768 < 1024), so the hamburger IS shown. This is correct. But `sm:col-span-3` on agents grid does NOT fire on tablet because `sm=1280px`. The 7-persona grid stays 2-column on tablets.

**File:** `pmacs/web/templates/base.html` lines 66-69
**Fix:** Add back the standard breakpoints or use distinct names (`dash-sm`, `dash-md`) that do not collide. Alternatively, define the breakpoints as extensions that add to (not replace) defaults.

---

### M-8 — `settings.html` "Save" button for API key uses `bg-surface-overlay text-white` — illegible in light mode

`settings.html` lines 329, 340:
```html
class="px-4 py-2 text-xs bg-surface-overlay text-white rounded-xl ..."
```
`--surface-overlay` in light mode is `rgba(255,255,255,0.85)` — white background with white text. The button is invisible in light mode.

**File:** `pmacs/web/templates/settings.html` lines 329, 340
**Fix:** Replace `bg-surface-overlay` with `bg-accent` for these action buttons.

---

### M-9 — `queueDrop` in `agents.html` does not reset `draggedEl.style.opacity` when ticker is the same (early return)

`agents.html` line 249:
```js
function queueDrop(e) {
    e.preventDefault();
    var target = e.target.getAttribute("data-ticker");
    if (!_draggedTicker || !target || _draggedTicker === target) return;
```
When `_draggedTicker === target` (dropped on itself), the function returns without resetting opacity. The dragged element remains at `opacity: 0.5` (set in `queueDragStart` line 242) until the page is refreshed.

**File:** `pmacs/web/templates/agents.html` line 249
**Fix:**
```js
if (!_draggedTicker || !target || _draggedTicker === target) {
    if (draggedEl) draggedEl.style.opacity = "1";
    _draggedTicker = null;
    return;
}
```

---

### M-10 — SSE `"cycle"` stream handler registered twice — duplicate toast on cycle complete

`app.js` line 1435: `onSSE("cycle", function(data) {...})` — main cycle handler.
`app.js` line 1573: `onSSE("cycle", function(data) {...})` — sparkline refresh handler.
`app.js` line 1777: `onSSE("cycle", function(data) {...})` — cycle timing handler.

`onSSE` (line 186) uses an array per stream, pushing each handler: `eventHandlers[stream].push(handler)`. All three cycle handlers are called. The second handler (line 1573) only acts on `event === "sparkline_update"` so it's safe. The third (line 1777) acts on `event === "cycle_start"` and `event === "cycle_complete"`. The main handler (line 1435) also handles `cycle.closed` and shows a toast. If the backend emits `event_type: "cycle.closed"` and `event: "cycle_complete"` simultaneously (e.g., as two fields of the same payload), neither duplicates. But if both fields are set on the same event, `handleNotification("cycle_complete", ...)` is called from both the main handler's `data.event_type` branch (line 1474) and potentially from `data.event` matching. Needs server-side payload inspection to confirm, but the pattern is fragile.

**File:** `pmacs/web/static/app.js` lines 1435, 1573, 1777
**Fix:** Merge the cycle timing and sparkline SSE handlers into the main cycle handler to avoid three separate registrations.

---

## LOW

### L-1 — `read-more-modal` dynamically created, but focus is set via `modal.focus()` on a non-focusable div

`app.js` line 80: `modal.focus()`. The modal div has no `tabindex` attribute. Calling `.focus()` on a non-focusable element silently fails in most browsers — focus does not move into the modal, breaking the focus trap.

**File:** `pmacs/web/static/app.js` line 80
**Fix:** Add `modal.setAttribute("tabindex", "-1")` before `modal.focus()`.

---

### L-2 — Dashboard portfolio hero sparkline uses `linearGradient id="spark-grad"` — id collides if multiple pages share SVG ids

`dashboard.html` line 73: `<linearGradient id="spark-grad" ...>`. SVG `id` attributes must be unique per document. If the HTMX partial swap injects the dashboard content into `#main-content` while another page's SVG is still in the DOM (during transitions), or if future pages also use `spark-grad`, SVG gradient references break and the fill becomes black.

**File:** `pmacs/web/templates/dashboard.html` line 73
**Fix:** Use a more unique id like `id="spark-grad-portfolio"`.

---

### L-3 — `base.html` media query `:root:not(:not(.dark))` is a no-op

`base.html` lines 159-162:
```css
@media (prefers-color-scheme: dark) {
    :root:not(:not(.dark)) {
        /* dark class takes precedence */
    }
}
```
`:not(:not(.dark))` is logically equivalent to `.dark` — this selector only matches the root when it already has `.dark`. The block is empty, so the entire `@media` block is dead code. The actual dark-mode switching is handled via JS (line 166-175), which is correct. This CSS block does nothing.

**File:** `pmacs/web/templates/base.html` lines 159-163
**Fix:** Remove the dead media query block.

---

### L-4 — `debug.html`: `clearEvents()` removes all `[data-event-level]` rows but leaves the `divide-y` container, which then has no children — border artifacts

`debug.html` line 49: events are wrapped in `<div class="divide-y divide-border-subtle">`. When `clearEvents()` removes all children, the parent `divide-y` container remains (it's inside `#event-stream`). The `divide-y` class adds `border-top` to all children via `> * + *` selector — with no children, no visual artifact. But the empty container div remains in the DOM with no semantic purpose.

Minor cosmetic only — the empty div renders zero height. Not a functional bug.

---

### L-5 — `shortcut-overlay` backdrop click closes the overlay but the overlay itself lacks `aria-labelledby`

`base.html` line 303:
```html
<div id="shortcut-overlay" ... role="dialog" aria-label="Keyboard shortcuts" aria-modal="true">
```
Using `aria-label` directly is valid but `aria-labelledby` pointing to the heading element (line 306) is preferred per ARIA spec. Low impact for a single-operator app.

---

### L-6 — `pipeline.html`: chip context menus (`chip-menu-{ticker}`) are inside a `relative` container with `z-index:10` — can be clipped by parent card's `overflow:hidden`

`pipeline.html` line 207:
```html
<div id="chip-menu-{{ item.ticker }}" class="hidden absolute right-0 top-5 card card-elevated z-10 py-1 w-36 rounded-xl">
```
The `.card` class has `position:relative` (style.css line 713). The chip container inside the priority band uses `rounded-xl` which in some browsers clips absolutely-positioned children. If any ancestor has `overflow:hidden`, the dropdown is clipped. The `priority-band` div does not have overflow:hidden, but the outer `.card.card-elevated` wrapping the entire right rail does (inherited from `border-radius` + browser paint). Test needed with live rendering, but the pattern is risky.

---

### L-7 — `settings.html` notification level dropdowns for `kill_switch_engaged` and `audit_chain_failure` are `disabled` but have no visual disabled state defined

`settings.html` line 120:
```html
{% if event_key in ("kill_switch_engaged", "audit_chain_failure") %}disabled{% endif %}
```
The `<select>` element gets the HTML `disabled` attribute but no CSS class is applied to visually indicate it is locked (e.g., reduced opacity, lock icon, tooltip). A user unfamiliar with the system cannot tell why these selects don't respond.

**Fix:** Add `opacity-50 cursor-not-allowed` classes when disabled, and a `title="This event cannot be silenced"` tooltip.

---

### L-8 — `sankey.js` loaded unconditionally on agents page via `<script src="/static/sankey.js">` but checked with `typeof PMACS_SANKEY !== "undefined"` — if file 404s, error is silent

`agents.html` line 234: `<script src="/static/sankey.js"></script>`. If this file fails to load (404, network error), the script block on line 403 wraps the init in a typeof check and silently skips. But the visualization containers (`#viz-process`, `#viz-network`, `#viz-math`) show "Loading..." indefinitely with no error state.

**Fix:** Add an `onerror` handler to the script tag:
```html
<script src="/static/sankey.js" onerror="document.getElementById('sankey-container').innerHTML='<p class=...>Visualization unavailable</p>'"></script>
```

---

## Summary Table

| ID | Severity | File | Description |
|----|----------|------|-------------|
| C-1 | CRITICAL | settings.html:591 | `diff-modal` getElementById without null check — silent failure |
| C-2 | CRITICAL | debug.html:74, style.css:209, app.js:1262 | `event-detail` CSS class name mismatch — transition never fires |
| C-3 | CRITICAL | app.js:681 | Cycle compare modal hardcoded `bg-white/text-zinc-900` — invisible in dark mode |
| H-1 | HIGH | base.html:330 | Kill switch shortcut listed as `K` but actual binding is `Shift-K` |
| H-2 | HIGH | app.js:1628 | Sparkline dot Tailwind translate classes not picked up by Play CDN JIT |
| H-3 | HIGH | pipeline.html:562 | Verdict filter select picked by `document.querySelector('select')` — fragile |
| H-4 | HIGH | agents.html:273 | `promoteQueueHead()` shows success toast but makes no API call |
| H-5 | HIGH | totp_modal.html | No `aria-describedby` on TOTP dialog; no intentional note about disabled backdrop-close |
| H-6 | HIGH | pipeline.html:365 | `querySelector` returns first matching chip — breaks if ticker in multiple bands |
| H-7 | HIGH | settings.html:619 | Diff rows inserted as raw innerHTML — XSS risk from mutation API response |
| M-1 | MEDIUM | dashboard.html:175 | `hidden flex` double-declaration on cycle indicator — Tailwind anti-pattern |
| M-2 | MEDIUM | app.js:578,611 | `bg-zinc-100` hardcoded in Cmd-K palette — not dark-mode-aware |
| M-4 | MEDIUM | agents.html:399, app.js:11 | `escapeHtml` locally redefined with different behavior (escapes `"`) |
| M-5 | MEDIUM | — | `cost_widget.html`, `cost_settings.html`, `memo.html`, `universe.html` not audited |
| M-6 | MEDIUM | cortex.html:252 | Inline onclick with escaped quotes in HTML attribute — parse error on kill switch |
| M-7 | MEDIUM | base.html:66 | Custom breakpoints replace standard Tailwind breakpoints — `sm:` fires at 1280px instead of 640px |
| M-8 | MEDIUM | settings.html:329,340 | `bg-surface-overlay` (white/85%) with `text-white` — invisible Save button in light mode |
| M-9 | MEDIUM | agents.html:249 | Queue drag-drop: opacity not reset when dropped on self |
| M-10 | MEDIUM | app.js:1435,1573,1777 | Triple SSE cycle handler registration — fragile event handling |
| L-1 | LOW | app.js:80 | `read-more-modal.focus()` on non-focusable div — focus trap fails |
| L-2 | LOW | dashboard.html:73 | SVG gradient id `spark-grad` not unique — collision risk on HTMX swap |
| L-3 | LOW | base.html:159 | Dead CSS `@media` block with no-op selector |
| L-4 | LOW | debug.html | `clearEvents()` leaves empty `divide-y` container |
| L-5 | LOW | base.html:303 | Shortcut overlay missing `aria-labelledby` |
| L-6 | LOW | pipeline.html:207 | Chip context menus may be clipped by card `border-radius` paint |
| L-7 | LOW | settings.html:120 | Disabled notification selects have no visual disabled indicator |
| L-8 | LOW | agents.html:234 | `sankey.js` 404 shows "Loading..." indefinitely — no error state |

---

## Top 5 Priority Fixes

1. **C-3 dark mode invisible modal** — Replace all `bg-white`/`text-zinc-*` in dynamically-built modals with design token classes. Operator cannot use cycle compare in dark mode.

2. **M-6 cortex.html kill switch button broken** — Move the inline TOTP call to a named function. The "Lift Kill Switch" button may not fire at all when the switch is engaged, which is a safety-critical path.

3. **H-4 promoteQueueHead no-op** — Add the fetch call. Operator sees success but nothing changes.

4. **H-7 XSS in diff viewer** — Escape `r['class']`, `r.baseline`, `r.candidate` before innerHTML insertion.

5. **M-7 breakpoint remapping** — Restore standard Tailwind breakpoints or document the remapping explicitly. The current remapping means two-column layouts only appear at 1280px+, making the UI cramped on most 1080p monitors (1920×1080 = fine, but 1366×768 = single-column everything).
