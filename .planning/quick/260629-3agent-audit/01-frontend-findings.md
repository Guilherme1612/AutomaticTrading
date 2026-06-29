# Agent 1 — Frontend-Design Audit: `/ticker/{ticker}` workspace

**Audit date:** 2026-06-29
**Scope:** `pmacs/web/templates/ticker_detail.html` (1043 L), `pmacs/web/routes/ticker_data.py` (886 L), `pmacs/web/static/app.js` (2030 L), `pmacs/web/static/style.css` (1570 L — the real CSS file; `app.css` does not exist in this repo).
**Methodology:** read-only, six-pillar sweep + state-region dispatcher check (§13.4) + cross-reference with `memo.html`/`agents.html`/`_decisions.html` for `/ticker/` link consistency.
**Skill applied:** `frontend-design` (six-pillar aesthetic rubric).

---

## Six-pillar score (1=poor, 5=excellent)

| # | Pillar | Score | One-line reason |
|---|--------|-------|-----------------|
| 1 | Typography hierarchy | 4 | Consistent 4-step scale (10px muted / 12px body / xs-lg section label / 2xl-3xl metric) with `font-mono tabular-nums` on all numerics. But: no `<h1>` — the page title is a `<span>` inside `<header>` (L52-75) which breaks outline + landmark semantics. |
| 2 | Color & contrast (WCAG AA) | 3 | `text-text-muted` on `bg-surface-sunken/40` cards at 10-12px is borderline (likely 3.8-4.4:1 in light mode). 79 uses of `text-text-muted`, many on small uppercase tracking-widest micro-labels. Tone classes (`positive/negative/warning`) carry the data signal — but the chips use color-only with no icon or shape (e.g. RSI Overbought at L690-700). |
| 3 | Spacing rhythm | 3 | Cards use a uniform `mb-6` between sections (L412, L448, L499, L543, L658, L776, L811, L817, L823, L870, L908, L919, L1004) — good vertical cadence. **But:** ad-hoc `-mt-4` (L97), `ml-auto` (L43, L153, L502, L662), `pt-1` (L900), `mb-2` (L358, L433, L543), `mt-1` / `mt-2` (L361, L406, L441, L672, L704, L717, L768) sprinkled throughout the 5 tabs. The Phase 7c additions (L88-104 empty state) introduced a one-off `text-center -mt-4` to compensate for loading_state's own bottom margin. |
| 4 | Interactive states | 2 | `:focus-visible` works (L1317-1325) and `card-elevated:hover` lifts (L742-746). **But:** the 5 tab buttons (L160-164) have **no `:focus` style of their own** — they inherit only the global outline, but the active-tab state (L1026-1028 sets `bg-accent-soft text-accent`) is **set by JS, not CSS**, so on no-JS or during the brief render before the script runs, the visual selection cue is absent. **No `:disabled` style** anywhere even though empty/error paths exist. **No keyboard navigation** between tabs (Arrow Left/Right, Home/End) — only mouse click. The `tab` role is missing so screen readers don't announce it as a tab. |
| 5 | Responsive (<=375px mobile) | 3 | Card grids correctly collapse to `grid-cols-1` at sm. Tab list `flex flex-wrap gap-1 mb-5` (L159) wraps gracefully. **But:** the workspace summary strip (L110 `flex flex-wrap items-center gap-x-6 gap-y-2`) holds 6 inline-flex items at 12px+ font — at 375px the wrap is 4 lines deep and the `ml-auto` "Last analyzed" (L153) jumps oddly. The persona details header (L886) `flex items-center justify-between gap-3` with three trailing chips (Brier + 3 arrow percentages) overflows at 375px (no `flex-wrap`, no `truncate` on the long Brier number). The 52w cards (L727, L749) at 2-col `sm:grid-cols-2` already collapse to 1-col on mobile, but the inline `vs $X.XX now` (L743, L765) is non-truncating and overflows if the price is high. |
| 6 | Accessibility / axe | 2 | **Tab pattern is malformed:** tabs have no `id`, no `role="tab"`, no `aria-controls`. Tab panels (L807, L878, L916, L988) have `aria-labelledby="ws-tablist"` pointing to the *whole nav* rather than the specific tab -> screen readers announce all 4 hidden panels as labelled by the same string ("AAPL workspace tabs"), losing the per-tab cue. The `<details>` elements (L885-904) use `list-none` which strips the disclosure triangle, and **no custom marker** is provided — open/closed state is invisible to sighted users. The `tablist` `<nav>` element (L159) is wrong ARIA — `<nav>` implies navigation, not tab container. The 10-12px micro-labels used for data (e.g. "Universe", "Verdict" at L19, L114) are *visually* styled like headings but are `<span>`s — no semantic weight. The `Open full memo` button (L866) is an `<a>` styled as a button — okay pattern, but lacks `aria-label` to distinguish from in-page `View memo` link (L62). |

**Overall:** the page is visually well-organized and data-rich, but the **tab interaction is the single biggest a11y and interaction-state gap**. Numbered tab buttons (28px tall) and the four mis-labelled panels together represent ~30% of the post-Fundamentals content.

---

## Findings table (severity-sorted)

| ID | Sev | Pillar | File:L | One-line |
|----|-----|--------|--------|----------|
| F-001 | BLOCKER | 6 | ticker_detail.html:L807,878,916,988 | All 4 tabpanels `aria-labelledby="ws-tablist"` — points to the nav, not the specific tab |
| F-002 | BLOCKER | 4 | ticker_detail.html:L160-164 + L1011-1039 | Tab buttons have no `role="tab"`, no `id`, no `aria-controls`; no keyboard nav (Arrow/Home/End) |
| F-003 | HIGH | 4 | ticker_detail.html:L160-164 | Tab buttons `px-3 py-1.5 text-xs` = ~28px tall, below 44px tap target (WCAG 2.5.5) |
| F-004 | HIGH | 4 | ticker_detail.html:L160 + style.css:no rule | No `:focus-visible` / `:hover` style for `.ws-tab`; active state is JS-only (L1026-1028) |
| F-005 | HIGH | 6 | ticker_detail.html:L159 | `<nav role="tablist">` — wrong element; should be `<div role="tablist">` |
| F-006 | HIGH | 2 | ticker_detail.html:L690-700 | RSI "Neutral" chip color-only signal — same shape as "Overbought"/"Oversold", no icon or text-only backup |
| F-007 | HIGH | 3 | ticker_detail.html:L97 | `-mt-4` ad-hoc patch over `loading_state.html`'s own bottom padding; should be inside the component |
| F-008 | HIGH | 1 | ticker_detail.html:L52-75 | `<header>` has no `<h1>` — page title is a bare `<span>`; breaks document outline and screen-reader landmark |
| F-009 | MED | 5 | ticker_detail.html:L886-898 | Persona details header non-wrapping flex with 3 trailing chips + Brier; overflows at 375px |
| F-010 | MED | 6 | ticker_detail.html:L885-904 | `<details><summary class="list-none">` strips default chevron; no replacement marker -> open/closed state is invisible |
| F-011 | MED | 2 | ticker_detail.html (79 sites) | `text-text-muted` on `bg-surface-sunken/40` at 10-12px is borderline WCAG AA (4.0-4.4:1) |
| F-012 | MED | 4 | ticker_detail.html:L866 | `<a>` styled as primary button but no `aria-label`; conflicts with `View memo` (L62) by ambiguous text |
| F-013 | MED | 3 | ticker_detail.html:L153, L502, L662, L900 | `ml-auto` / `pt-1` / `mb-2` ad-hoc micro-offsets; inconsistent with the rest of the 5-tab rhythm |
| F-014 | MED | 1 | ticker_detail.html:L8-10 + L52 | Universe chip (L19) and verdict label (L114) are `<span>`s styled as 10px uppercase tracking-widest — should be a consistent `<p class="label">` or design token |
| F-015 | LOW | 5 | ticker_detail.html:L743, L765 | "vs $X.XX now" inline price is non-truncating; can overflow at 375px when value is long |
| F-016 | LOW | 6 | ticker_detail.html:L860-862 | `list-disc pl-5` for `what_would_change_my_mind` — bullets inside a card with no `aria-label`; acceptable but inconsistent with the `space-y-1.5` rhythm used elsewhere |
| F-017 | LOW | 4 | ticker_detail.html:L70, L31, L34 | HALTED / DELISTED `<span role="status">` — `role="status"` on a non-live span doesn't trigger `aria-live`; should be `<span role="status" aria-live="polite">` |
| F-018 | LOW | 4 | ticker_detail.html:L266 | "No trend data" placeholder is `aria-hidden="true"` — correct, but its parent (sparkline_svg) sets `role="img"` + `aria-label` only in the *rendered* branch; the empty branch loses the image role entirely (acceptable) |
| F-019 | LOW | 3 | ticker_detail.html:L866 vs memo.html:L38 | "Open full memo" uses `bg-accent-soft` (soft button); memo.html:L38 primary CTA uses `bg-accent` (solid) — minor inconsistency, but memo.html is Agent 3's territory |
| F-020 | LOW | 1 | ticker_detail.html:L696-700 + L732-738 + L754-758 | Three color-tone chip patterns copy-pasted with slightly different dot sizes (`w-1 h-1` vs `w-1.5 h-1.5`) — drift, not a bug |

**Total: 20** | **BLOCKER: 2** | **HIGH: 6** | **MED: 7** | **LOW: 5**

---

## State-region dispatcher (§13.4) check

`pmacs/web/templates/components/state_region.html` is the single contract for non-ready states. Required variable `region_state in {"loading","empty","error","ready"}` plus `loading_*`, `empty_*`, `error_*` props.

**On `/ticker/{ticker}` the dispatcher is NOT used.** Instead, the page does bespoke if/elif inline branching (L80-104):

```
{% if error %}            -> include "components/error_state.html"  (partial — 4/5 vars set, no dispatcher)
{% elif no_data %}        -> bespoke inline markup (L88-104), NOT empty_state.html
{% elif warming %}         -> include "components/loading_state.html" with loading_what/eta/cancel (L91-96)
{% else %}                -> ready content (workspace)
```

**Gaps:**
- `no_data` (non-warming) does NOT use `empty_state.html` — it hand-rolls the markup with `<h2>`, `<p>`, no SVG icon, no `aria-label` on the section, no `anim-fade-in`. The bespoke title is "No data for {{ ticker }} yet" (L89) which is generic and not the §13.4 contract phrase.
- The `warming` branch uses `loading_state.html` correctly (good) but wraps it in a custom `<section class="card card-elevated">` (L88) and then adds `-mt-4` (L97) to compensate for the loading component's own bottom margin — ad-hoc, see F-007.
- The per-tab `{% else %}` empty states (L869-873 memo, L907-911 personas, L979-982 lineage, L1003-1007 failures) are also bespoke — they use the same `card card-elevated p-8 text-center` pattern, not `empty_state.html`.

**Net:** the workspace ships 5 hand-rolled empty cards + 1 hand-rolled warming section where 1 dispatcher + 1 empty_state include + 1 loading_state include would do. Consistent with §13.4's "never a generic 'No data'" spirit, but inconsistent with the architectural contract.

---

## Link style cross-reference (Agent 3 territory, FYI only)

`ticker_detail.html` L62 (`text-sm text-accent hover:underline`) matches `memo.html` L106 (`ml-auto text-sm text-accent hover:underline`) — same pattern, good.
`ticker_detail.html` L866 (`bg-accent-soft text-accent hover:bg-accent hover:text-white`) is a *soft* primary button. `memo.html` L38 uses `bg-accent text-white` (solid) for the *primary* CTA. The 2 styles coexist in the same user journey (Link From Memo -> /ticker -> Button "Open full memo" -> /memo). Not a bug; the soft treatment on /ticker signals "secondary, contextual" vs the solid treatment on /memo for the main action. **No change recommended on the cross-page link styles.**

---

## Detailed finding cards

```
- ID: F-001
- Severity: BLOCKER
- Pillar: 6
- File: pmacs/web/templates/ticker_detail.html:L807,878,916,988
- Problem: All 4 tabpanels (ws-memo, ws-personas, ws-lineage, ws-failures) use
  aria-labelledby="ws-tablist" which targets the <nav role="tablist"> element, not
  a specific tab — so AT users hear the same 35-character label on every panel.
- Fix: Give each tab an id (e.g. id="ws-tab-fundamentals") and point its
  corresponding panel to that id.
- Before/after sketch:
  - L160: <button ... data-ws-tab="fundamentals" aria-selected="true" id="ws-tab-fundamentals">
  - L807: <section id="ws-memo" ... aria-labelledby="ws-tab-memo">
  (same for ws-tab-personas / ws-tab-lineage / ws-tab-failures)
```

```
- ID: F-002
- Severity: BLOCKER
- Pillar: 4
- File: pmacs/web/templates/ticker_detail.html:L160-164 + L1011-1039
- Problem: Tabs lack the ARIA tab pattern (role, id, aria-controls) and the
  click handler (L1033) doesn't implement ArrowLeft/Right/Home/End/Enter/Space.
  Power users and AT cannot operate the workspace.
- Fix: Add role="tab" + id + aria-controls="ws-{name}" to each button, add a
  keydown handler that moves focus on Arrow keys, and prevent default on
  Enter/Space so clicking is equivalent to keypress.
- Before/after sketch:
  - L160: <button role="tab" id="ws-tab-fundamentals" aria-controls="ws-fundamentals"
              tabindex="0" aria-selected="true">
  - L1032: tabs.forEach(b => b.addEventListener('keydown', e => {
              const ids = [...tabs].map(t => t.dataset.wsTab);
              const i = ids.indexOf(b.dataset.wsTab);
              let next = i;
              if (e.key === 'ArrowRight') next = (i+1) % tabs.length;
              else if (e.key === 'ArrowLeft') next = (i-1+tabs.length) % tabs.length;
              else if (e.key === 'Home') next = 0;
              else if (e.key === 'End') next = tabs.length-1;
              else return;
              e.preventDefault();
              tabs[next].focus(); activate(ids[next]);
            }));
```

```
- ID: F-003
- Severity: HIGH
- Pillar: 4
- File: pmacs/web/templates/ticker_detail.html:L160-164
- Problem: Tab buttons are px-3 py-1.5 text-xs — height ~28px, well below
  the 44x44 px WCAG 2.5.5 target-size minimum for touch.
- Fix: Add `min-h-[44px] inline-flex items-center` to the ws-tab class. On
  desktop, vertical centering prevents the text from drifting; on mobile the
  tap target meets the guideline.
- Before/after sketch:
  - L160: class="ws-tab min-h-[44px] inline-flex items-center px-3 py-1.5 text-xs rounded-xl font-medium transition-colors"
```

```
- ID: F-004
- Severity: HIGH
- Pillar: 4
- File: ticker_detail.html:L160 + style.css (no rule)
- Problem: The .ws-tab class has no dedicated :focus / :hover / :disabled rule
  in style.css. The "active" style is applied by JS in the activate() function
  (L1026-1028) by toggling utility classes — so on the very first paint (before
  the inline script runs) and on no-JS, the user sees no selection state.
- Fix: Add a CSS rule so the active state is fully data-attribute driven and
  survives without JS. Use [aria-selected="true"].
- Before/after sketch (style.css after L1325):
  .ws-tab { transition: background 0.15s, color 0.15s; }
  .ws-tab:hover { background: var(--surface-sunken); }
  .ws-tab[aria-selected="true"] { background: var(--accent-soft); color: var(--accent); }
  .ws-tab[aria-selected="true"]:hover { filter: brightness(0.97); }
```

```
- ID: F-005
- Severity: HIGH
- Pillar: 6
- File: pmacs/web/templates/ticker_detail.html:L159
- Problem: <nav role="tablist"> — <nav> is a navigation landmark; role="tablist"
  expects a non-landmark container. AT may announce this as BOTH a navigation
  region AND a tablist, or skip the tablist altogether.
- Fix: Replace <nav> with <div role="tablist">. The 5 buttons inside are the
  tabs, not navigation links.
- Before/after sketch:
  - L159: <div class="flex flex-wrap gap-1 mb-5 reveal" role="tablist"
              aria-label="{{ ticker }} workspace tabs" id="ws-tablist">
  - L165: </div>
```

```
- ID: F-006
- Severity: HIGH
- Pillar: 2
- File: pmacs/web/templates/ticker_detail.html:L690-700 (and the 52w chips at L731-738, L753-759)
- Problem: RSI / 52w-distance chips rely on color alone (red/amber/green dot
  + same-shape pill) to convey overbought/oversold/expensive/value. WCAG 1.4.1
  Use of Color requires a non-color cue.
- Fix: Add a leading glyph (U+25B2 U+25BC U+25C6) or a `font-bold` uppercase letter (O/V) to
  the chip text. Cheap, zero-JS, restores the cue for color-blind users.
- Before/after sketch (L694-700):
  <span class="... text-[10px] font-semibold uppercase tracking-widest">
    {%- if rsi_tone == 'negative' %}OVER {{ rsi_label }}
    {%- elif rsi_tone == 'positive' %}UNDER {{ rsi_label }}
    {%- else %}NEAR {{ rsi_label }}{% endif %}
  </span>
```

```
- ID: F-007
- Severity: HIGH
- Pillar: 3
- File: pmacs/web/templates/ticker_detail.html:L97
- Problem: `-mt-4` is a one-off patch to cancel the loading_state component's
  own bottom padding, because both the component and the wrapping section add
  margin. The page now depends on a specific internal margin of loading_state.
- Fix: Remove `-mt-4`; either (a) tighten loading_state.html's bottom padding
  to 0 and let callers control the section spacing, or (b) drop the wrapping
  <section> wrapper and use the loading_state component directly.
- Before/after sketch:
  - L97 (before): <p class="text-xs text-text-muted text-center -mt-4 mb-4">This page will populate once the first fetch completes. The skeleton matches the workspace shape.</p>
  - L97 (after):  <p class="text-xs text-text-muted text-center">This page will populate once the first fetch completes. The skeleton matches the workspace shape.</p>
```

```
- ID: F-008
- Severity: HIGH
- Pillar: 1
- File: pmacs/web/templates/ticker_detail.html:L52-75
- Problem: The page has no <h1>. The company name + ticker live in a <span>
  inside <header>. Screen-reader heading navigation (H key in NVDA/JAWS) is
  broken for this page.
- Fix: Promote the company-name span to <h1 class="..."> with the same styling.
- Before/after sketch:
  - L52-56 (before): <header class="mb-6 reveal">
                          <div class="flex items-baseline gap-3 flex-wrap mb-1">
                              <span class="font-mono text-3xl font-black text-text-primary tracking-tight">{{ ticker }}</span>
                              <span class="text-sm text-text-secondary">{{ company_name }}</span>
  - L52-56 (after):  <header class="mb-6 reveal">
                          <div class="flex items-baseline gap-3 flex-wrap mb-1">
                              <h1 class="font-mono text-3xl font-black text-text-primary tracking-tight m-0">{{ ticker }}</h1>
                              <p class="text-sm text-text-secondary m-0">{{ company_name }}</p>
```

```
- ID: F-009
- Severity: MED
- Pillar: 5
- File: pmacs/web/templates/ticker_detail.html:L886-898
- Problem: The persona details summary holds (left) persona name + key_signal
  (no truncate) and (right) optional Brier + 3-arrow percentage cluster.
  At <=375px, the right cluster overflows; no wrap, no truncate, no scroll.
- Fix: Allow the right cluster to wrap to a second line at small viewports,
  and truncate the key_signal text to 1 line.
- Before/after sketch:
  - L886: <summary class="cursor-pointer p-4 flex flex-wrap items-center justify-between gap-3 list-none">
  - L891: <div class="flex items-center gap-3 flex-shrink-0 basis-full sm:basis-auto justify-end">
  - L889 (key_signal): add `truncate` (already there) + max-w on its parent
```

```
- ID: F-010
- Severity: MED
- Pillar: 6
- File: pmacs/web/templates/ticker_detail.html:L885-904
- Problem: <summary class="list-none"> strips the default disclosure triangle.
  No replacement marker is provided. The user has no visual cue that the
  element is expandable, and no signal of open/closed state.
- Fix: Add a rotating chevron at the end of the summary, styled with CSS to
  rotate 90deg when [open]. ~6 lines of CSS, no JS.
- Before/after sketch (style.css after L1325):
  details.pm-details > summary { list-style: none; cursor: pointer; }
  details.pm-details > summary::after { content: '\25B8'; margin-left: auto; padding-left: 0.5rem; transition: transform 0.2s; }
  details.pm-details[open] > summary::after { transform: rotate(90deg); }
  (apply class `pm-details` to <details> in the personas loop)
```

```
- ID: F-011
- Severity: MED
- Pillar: 2
- File: pmacs/web/templates/ticker_detail.html (79 sites)
- Problem: text-text-muted is used 79 times for small labels and notes, almost
  always inside .bg-surface-sunken/40 cards. The actual contrast ratio depends
  on the design-token values (defined in tailwind config, not visible in this
  scoped audit) but the consistent 10-12px size with muted grey is borderline
  for WCAG AA (4.5:1 for normal text).
- Fix: Use a slightly lighter token (text-text-secondary) for the 10-12px
  tracking-widest labels, OR add `font-medium` to bump the visual weight so the
  contrast perception improves. Bulk replace, or add a CSS class .pmacs-label.
- Before/after sketch (representative L19):
  - L19 (before): <span class="text-[10px] text-text-muted uppercase tracking-widest">Universe</span>
  - L19 (after):  <span class="text-[10px] text-text-secondary font-medium uppercase tracking-widest">Universe</span>
```

```
- ID: F-012
- Severity: MED
- Pillar: 4
- File: pmacs/web/templates/ticker_detail.html:L866
- Problem: The "Open full memo" <a> is styled as a primary button and lives
  alongside the in-page "View memo" <a> at L62. Both go to /memo/{{ticker}}.
  An AT user navigating by links will hear "View memo, link" and "Open full
  memo, link" with no clear distinction.
- Fix: Add an aria-label that disambiguates the destination.
- Before/after sketch:
  - L866 (before): <a href="/memo/{{ ticker }}" class="...">Open full memo &rarr;</a>
  - L866 (after):  <a href="/memo/{{ ticker }}" class="..." aria-label="Open {{ ticker }} investment memo in full context">Open full memo &rarr;</a>
```

```
- ID: F-013
- Severity: MED
- Pillar: 3
- File: pmacs/web/templates/ticker_detail.html:L153, L502, L662, L900
- Problem: Phase 7c ad-hoc micro-offsets (ml-auto on the "Last analyzed" date,
  ml-auto on the SaaS heading chip, ml-auto on the Technical description, pt-1
  on the details body) fight the otherwise regular `mb-6` card rhythm.
- Fix: Replace ml-auto with justify-between on the parent flex, or use
  `ms-auto` consistently. Drop the pt-1 (use py-3 or py-4 instead).
- Before/after sketch:
  - L659-662 (before): <div class="flex items-center gap-2 mb-4">
                            <span class="w-2 h-2 rounded-full bg-accent"></span>
                            <h2 id="tech-heading" ...>Technical</h2>
                            <span class="text-[10px] text-text-muted ml-auto">Price, ...</span>
                        </div>
  - L659-662 (after):  <div class="flex items-baseline justify-between gap-2 mb-4 flex-wrap">
                            <div class="flex items-center gap-2">
                                <span class="w-2 h-2 rounded-full bg-accent"></span>
                                <h2 id="tech-heading" ...>Technical</h2>
                            </div>
                            <span class="text-[10px] text-text-muted">Price, ...</span>
                        </div>
```

```
- ID: F-014
- Severity: MED
- Pillar: 1
- File: pmacs/web/templates/ticker_detail.html:L19, L114, L124, L130, L136, L142, L148, L154, L463, L470, L501, L547, L553, L561, L567, L611, L662, L668, L679, L709, L729, L751, L779, L784, L812, L818, L825, L832, L838, L844, L851, L856, L920, L945
- Problem: ~30+ inline "label" spans reproduce the same 4-5 Tailwind classes
  for the 10px uppercase tracking-widest micro-label style. This is a design
  token masquerading as utility composition.
- Fix: Add a single .pmacs-eyebrow class in style.css and replace the
  repeated 4-class string with it.
- Before/after sketch (style.css after L1325):
  .pmacs-eyebrow { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-secondary); }
  Then bulk-replace in the template.
```

```
- ID: F-015
- Severity: LOW
- Pillar: 5
- File: pmacs/web/templates/ticker_detail.html:L743, L765
- Problem: "vs $X.XX now" can push the 52w card past 1 line at 375px when
  the price has 3+ digits before the decimal.
- Fix: Add `truncate` and move the live price under the headline value.
- Before/after sketch:
  - L742-744 (before): <span class="text-xs text-text-muted">vs {{ fmt(tech.current_price, "$") }} now</span>
  - L742-744 (after):  <p class="mt-1 text-xs text-text-muted truncate">vs {{ fmt(tech.current_price, "$") }} now</p>
```

```
- ID: F-016
- Severity: LOW
- Pillar: 6
- File: pmacs/web/templates/ticker_detail.html:L860-862
- Problem: <ul class="list-disc pl-5"> with text-sm — bullets in a card body
  with no aria-label. Acceptable; the heading "What would change my mind"
  (the parent div) provides context. Mentioned for completeness.
- Fix: No change required. (If F-014 is taken, the surrounding label would
  also be restyled.)
```

```
- ID: F-017
- Severity: LOW
- Pillar: 4
- File: pmacs/web/templates/ticker_detail.html:L31, L34, L70
- Problem: role="status" without aria-live="polite" does not announce dynamic
  updates; the HALTED/DELISTED chip is loaded at first paint so it's seen
  not announced, but if it ever flips dynamically, AT users will miss it.
- Fix: Add aria-live="polite" to all three role="status" sites, OR change
  to <span class="..."> data-status="halted"> with no role and let the
  visual cue carry the signal (the chip text is already the message).
- Before/after sketch:
  - L31 (before): <span class="..." role="status">HALTED</span>
  - L31 (after):  <span class="..." role="status" aria-live="polite">HALTED</span>
```

```
- ID: F-018
- Severity: LOW
- Pillar: 4
- File: pmacs/web/templates/ticker_detail.html:L266, L295-323
- Problem: The sparkline empty branch (L266) is aria-hidden — correct. The
  rendered branch sets role="img" + aria-label with the trend summary. Good.
  No change; flagged only to document that this is the right pattern (the
  tabpanel problem is the actual a11y bug, not the sparkline).
- Fix: None.
```

```
- ID: F-019
- Severity: LOW
- Pillar: 3
- File: pmacs/web/templates/ticker_detail.html:L866
- Problem: Cross-page link style: /ticker uses bg-accent-soft (soft button),
  /memo uses bg-accent (solid) for the primary CTA. Not a bug; the soft
  treatment signals "contextual navigation back to memo" vs "primary action
  on memo page." Documented so Agent 3 has the rationale if they want to
  harmonize.
- Fix: None.
```

```
- ID: F-020
- Severity: LOW
- Pillar: 1
- File: pmacs/web/templates/ticker_detail.html:L696-700, L732-738, L753-759
- Problem: Three "tone chip" patterns are copy-pasted with different dot
  sizes (w-1 h-1 vs w-1.5 h-1.5). Visual drift, not a regression.
- Fix: Extract a Jinja macro `tone_chip(label, tone)` with a single dot size.
  This is a 5-min refactor; pairs naturally with F-014.
- Before/after sketch (new macro near L211):
  {% macro tone_chip(label, tone) -%}
    {%- set bg = {'positive':'bg-positive-soft','negative':'bg-negative-soft','warning':'bg-warning-soft','accent':'bg-accent-soft','muted':'bg-surface-sunken'}[tone] -%}
    {%- set txt = {'positive':'text-positive','negative':'text-negative','warning':'text-warning','accent':'text-accent','muted':'text-text-muted'}[tone] -%}
    {%- set dot = {'positive':'bg-positive','negative':'bg-negative','warning':'bg-warning','accent':'bg-accent','muted':'bg-text-muted'}[tone] -%}
    <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md {{ bg }} {{ txt }} text-[10px] font-semibold uppercase tracking-widest">
      <span class="w-1.5 h-1.5 rounded-full {{ dot }}"></span>{{ label }}
    </span>
  {%- endmacro %}
```

---

## Note on pre-loaded memories (NOT re-flagged)
- Workspace shape (5 tabs as intentional) — respected, findings target within-tab quality, not the tab count.
- `format_xy_poly` fix — not relevant; no new Jinja filter activity on this page.
- 1h in-process Polygon cache — not relevant; this is a Python-layer cache, not a UX issue.
- No TTM-divergence suppression / no Fwd P/E cap — confirmed: I did NOT propose suppression or magnitude caps. The "raw grid" sections (L776-802) are kept verbatim.
- Stale fundamentals cache refresh command — runtime concern, not a frontend finding.

## Files NOT touched (per scope)
- `pmacs/agents/*`, `pmacs/engines/*`, `pmacs/nervous/*`, `pmacs/schemas/*`
- `spec/*.md`, `AGENTS.md`
- `pmacs/web/templates/{base,pipeline,cortex,dashboard,agents,memo,settings,universe,debug,cost_widget,wizard/*}.html`
- `pmacs/web/routes/*` (other than `ticker_data.py` — no findings on it)
