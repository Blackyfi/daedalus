# Frontend Responsive & Mobile Audit

**Reviewed at viewport widths:** 360 px (small phone), 768 px (tablet portrait), 1280 px (desktop).
**Method:** static read of every page and major component in `frontend/src` against the Tailwind config in `frontend/tailwind.config.js` (no custom screens — defaults `sm 640 / md 768 / lg 1024 / xl 1280 / 2xl 1536`) and the global classes in `frontend/src/index.css` (`.btn`, `.field`, `.panel`, `.tag`, `.status-pill`).
**Hit-target reference:** WCAG 2.5.5 / Apple HIG ≥ 44 × 44 px; Material ≥ 48 × 48 dp. The shared `.btn` resolves to `px-3 py-1.5 text-xs` ≈ 28 px tall, and `.tag` / `text-[10px]` actions resolve to ≈ 16–20 px — both fail tap-target on touch devices.
**Screenshots:** none captured in this static pass; before/after screenshots should be added when the dev server is brought up alongside the implementation PR. Per-issue annotations describe the visible failure mode so a reviewer can reproduce in DevTools (Cmd-Shift-M → 360/768/1280).

---

## 0. Cross-cutting issues (apply globally)

| # | Issue | Where | Target breakpoints |
|---|---|---|---|
| 0.1 | `<meta name="viewport">` is correct (`width=device-width, initial-scale=1.0`), so layout problems below are **real layout bugs**, not zoom artefacts. | `frontend/index.html:6` | n/a |
| 0.2 | Tailwind config has no custom `screens`. Every responsive fix must use the defaults; many existing classes are unprefixed (apply at all widths) — that is the root cause of most issues below. | `frontend/tailwind.config.js` | all |
| 0.3 | `.btn` is 28 px tall and `text-[10px]` action buttons are 16–20 px tall. Below the 44 px touch-target minimum on phone/tablet. | `frontend/src/index.css:19-30`; uses in TaskBoard, RunPanel, IdeaBox | ≤ 768 px: bump to ≥ `py-2.5` and `min-h-[44px]`, or apply `md:py-1.5` and use a larger variant on mobile. |
| 0.4 | No mobile container padding scaling — pages use `p-6` / `gap-6` everywhere. On 360 px that burns 48 px of horizontal real estate before content starts. | `Shell.tsx:25,63`, `ProjectListPage.tsx`, `ProjectPage.tsx` | ≤ 640 px: use `p-3 sm:p-4 lg:p-6` and `gap-3 lg:gap-6`. |
| 0.5 | Shared `.panel` is `p-4`. Nested panels (panel inside panel) double the inset; on phone the inner content gets ~ 32 px of padding eating the row. | `frontend/src/index.css:32-34`; nested in `ProjectListPage.tsx:48-83`, `RunPanel.tsx:367,399,413` | ≤ 640 px: nested panels should use `p-2 sm:p-3 lg:p-4`, or stop nesting `.panel` and use a lighter `.subpanel` class. |
| 0.6 | All tables (`ConnectorsPage`, `AuditPage`, `SecurityPage`, `DiscoverModal`) lack an `overflow-x-auto` wrapper. They overflow the viewport horizontally on phone and tablet. | listed below per page | ≤ 1024 px: wrap with `<div class="overflow-x-auto">`, set `min-w-[…]` on `<table>`. |
| 0.7 | No `lg:` / `md:` prefixes on any grid: `grid-cols-3`, `grid-cols-4`, `grid-cols-6`, `grid-cols-12` apply at every width, producing 50-px-wide kanban columns and crushed sidebars on phone. | TaskBoard, ProjectListPage, ProjectPage, PlanReview, RunPanel | < 768 px stack to 1 column; 768–1024 use 2; ≥ 1024 use current. |
| 0.8 | Several inputs have explicit pixel widths (`w-[420px]`, `max-w-4xl`) without a `w-full` fallback. They overflow ≤ 360 px. | LoginPage, DiscoverModal | ≤ 640 px: change to `w-full max-w-[420px]`. |
| 0.9 | xterm uses `FitAddon` only on `window.resize`. When the surrounding flex grid reflows (sidebar collapses, modal opens), the terminal does not re-fit. | `RunPanel.tsx:48-55` | all — replace with `ResizeObserver` on `containerRef.current`. |

---

## 1. `App.tsx`

**Reviewed:** routing shell only; no layout of its own. **No issues.**

The `PrivateOutlet` / `LoginRouteGuard` render `null` while `bootChecked === false`. That can flash an empty viewport on slow phones; not a responsive bug, but worth noting because at 360 px the user will see a fully blank screen during the boot probe and may assume the page broke.

**Action:** add a centred spinner or "Checking session…" state in the guards. (Out of scope for the responsive PR but flag it.)

---

## 2. `components/Shell.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 2.1 | **High** | ≤ 640 px | The header bar (`flex items-center gap-4`) packs `DAEDALUS` brand + 4 `NavLink`s (Projects / Connectors / Audit / Security) + flex-1 spacer + `Log out` button on a single row. With `gap-4` and `px-6` the row is ~520 px wide and overflows below `sm`. There is **no mobile menu / hamburger / collapse**. | < 768 px: collapse the nav into a hamburger / `<details>` drawer; show only the brand + a menu button. |
| 2.2 | Medium | all | `max-w-[1600px]` is fine, but `mx-auto` means at 1280 px there is no centred-content artefact. At ≥ 1600 px the brand and logout drift far apart with empty middle — minor. | ≥ 1600 px: optional, ignore. |
| 2.3 | Medium | ≤ 640 px | `<main className="mx-auto w-full max-w-[1600px] flex-1 p-6">` — the `p-6` (24 px) padding is too large on phone. With `Shell` header (~52 px) and banner (~32 px), under 768 px the available content height is < 600 px on a typical phone; visible content is cramped. | ≤ 640 px: `p-3` ; ≥ 1024: `p-6`. |
| 2.4 | Medium | all | Banner uses `px-6 py-2 text-sm`, click-to-dismiss but no visible close affordance on touch — confusing on phone, where users will not know the banner is dismissable. | all: add an explicit close `×` button at `min-h-[44px]`. |
| 2.5 | Low | all | `Log out` button (`.btn`) is 28 px tall — fails 44 px tap target on phone and tablet. | ≤ 768 px: `min-h-[40px]`. |

`Shell.tsx:25` — `mx-auto flex max-w-[1600px] items-center gap-4 px-6 py-3`
`Shell.tsx:29` — `<nav className="flex gap-2">` (always horizontal)

---

## 3. `components/TaskBoard.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 3.1 | **Critical** | ≤ 1024 px | `grid grid-cols-6 gap-2` (line 149) shows the kanban as 6 rigid columns at *every* width. On 360 px each column is ~50 px wide; task titles and `▶ Run` buttons are unreadable / unclickable. | < 768 px: a single horizontal-scroll lane (`flex overflow-x-auto snap-x` with `min-w-[260px]` cards) **or** vertical accordion of columns. 768–1280 px: `grid-cols-3` (two rows). ≥ 1280 px: keep `grid-cols-6`. |
| 3.2 | **High** | ≤ 768 px | New-task form (line 72) `grid grid-cols-2 gap-3` keeps Priority + Profile as two narrow selects on phone — selects collapse to ~120 px and labels wrap awkwardly. Connector dropdown (`col-span-2`) is fine. | ≤ 640 px: `grid-cols-1`. |
| 3.3 | **High** | all touch | `▶ Run` button (line 178-182) is `text-[10px] py-1.5 w-full justify-center` ≈ 22 px tall. Below 44 px tap target. | ≤ 768 px: `text-xs py-2 min-h-[40px]`. |
| 3.4 | Medium | ≤ 1024 px | Task card title `<h3 class="text-xs font-semibold leading-tight">` (line 164) at 12 px font is hard to read on phone. With long titles + 50-px-wide column, titles wrap to 5+ lines. | ≤ 768 px: `text-sm` and rely on `line-clamp-2`. |
| 3.5 | Medium | all | Tag pill row (line 167-175): `.tag` is `text-[10px] mr-1` with no `flex-wrap`. Multiple tags overflow the card. | all: wrap tags in `<div class="flex flex-wrap gap-1 mt-1">`. |
| 3.6 | Medium | ≤ 768 px | `header className="mb-3 flex items-center justify-between"` with `+ New task` button — fine, but the panel header has only `text-sm` for "Tasks" and on phone nothing distinguishes columns visually because the column header text (`text-xs uppercase`) is the same size as task titles. | ≤ 768 px: column header `text-sm`, sticky at top of horizontal lane. |
| 3.7 | Low | all | The form's submit row `flex justify-end` with single Create button — fine on desktop, but on mobile users expect a full-width primary button. | ≤ 640 px: `w-full` button. |

---

## 4. `components/RunPanel.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 4.1 | **Critical** | ≤ 1024 px | `grid grid-cols-3 gap-3` (line 252) splits 2-col terminal + 1-col "Recent runs" sidebar at every width. On 360 px the terminal is ~210 px wide, ~30 cols of `12 px` mono — almost unusable. The `aside` collapses to ~110 px. | < 1024 px: stack — terminal on top (full width), recent runs below; expose an "Active run" card at the top of the recent-runs list. |
| 4.2 | **Critical** | all | Terminal container: `h-[420px] w-full` (line 256). Fixed 420 px height — 60 % of an iPhone SE viewport (667 px). Combined with the rest of the page, the user can barely scroll. | ≤ 768 px: `h-[60vh] min-h-[280px] max-h-[420px]`; ≥ 1024 px: keep `h-[420px]`. |
| 4.3 | **High** | all | xterm font is hard-coded to `fontSize: 12` (line 39). On a 360-px-wide screen with 12 px mono, each row holds ~30 cols. The agent emits 80-col TUI output — heavy wrap. | ≤ 640 px: `fontSize: 11`; expose a setting in `ProjectSettings`. |
| 4.4 | **High** | all | `FitAddon.fit()` is only triggered on `window.resize` (line 48-55). When the React tree re-flows because a sibling component changes height (banner appears, plan card opens, modal closes) — the terminal does **not** refit and the WebSocket continues to send the wrong rows/cols to the PTY. | all — replace with `ResizeObserver(containerRef.current)`; on entry, debounce and call `apiJson(/resize)`. |
| 4.5 | **High** | ≤ 768 px | Action button row (lines 259-320): "Take input / Release input / pause / resume / interrupt / kill / detach / transcript / diff / Rollback / Retry" — up to **11 buttons**. Uses `flex flex-wrap gap-2`, which wraps to 4–5 rows on phone. Each `.btn` is 28 px tall (fail tap target). | ≤ 768 px: collapse the lifecycle controls into an overflow `⋯` menu; keep only Take/Release input + transcript visible; bump to `min-h-[44px]`. |
| 4.6 | Medium | all | `usageLabel` ("12k in · 4.2k out · $0.043") sits in `header` next to "{kind} · {id} · {state}" with a `gap-3 text-xs text-muted` row. On phone these wrap to two lines and the run state pill becomes hard to spot. | ≤ 640 px: stack `kind/id/state` above the usage label; `flex-col gap-1`. |
| 4.7 | Medium | all | Argus findings list (lines 376-388): `<pre>` for evidence has `overflow-x-auto text-[10px]` — works, but on phone the 10 px text is below readable size. | ≤ 640 px: `text-[11px]` minimum, allow `whitespace-pre-wrap`. |
| 4.8 | Medium | all | Transcript modal (lines 398-410): `max-h-[400px] overflow-auto whitespace-pre-wrap` — readable on desktop. On phone, the parent `.panel` already has `p-4`, so the visible height is ~330 px. | ≤ 640 px: `max-h-[60vh]`. |
| 4.9 | Medium | ≤ 768 px | Recent-runs aside cards (lines 327-360): each card has `text-xs` body + a Retry button at `text-[10px] mt-1 w-full`. The retry button is ~24 px tall — tap-target fail. | ≤ 768 px: `min-h-[40px]`. |
| 4.10 | Low | all | The status pill (line 261-277) `Input: you / vacant / X` uses `text-[10px]`. Title attribute is invisible on touch — there is no tap-to-explain. | ≤ 768 px: a small `?` icon that opens a tooltip on tap. |

---

## 5. `components/PlanReview.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 5.1 | **High** | ≤ 768 px | `grid grid-cols-4 gap-2` per task (line 91) keeps Title (col-span-3) + Priority select (col-span-1) on a single row at all widths. On 360 px the Title input is ~150 px wide and the Priority select is ~50 px wide — wraps awkwardly. | ≤ 640 px: `grid-cols-1` so each control gets full width; `≥ md`: keep current. |
| 5.2 | Medium | ≤ 640 px | The "Suggested connector" input + Remove button row (lines 124-132) — Remove button is `.btn` (28 px tall). | ≤ 640 px: full-width Remove button on its own row; `min-h-[44px]`. |
| 5.3 | Medium | ≤ 768 px | Card header (line 64-83) `flex items-center justify-between` with `Confirm all` + `Discard` buttons. On phone the rationale + buttons share a row and the buttons get pushed off-screen. | ≤ 640 px: `flex-col gap-2`, buttons full-width. |
| 5.4 | Low | all | `whitespace-pre-wrap` on rationale (line 86) is correct; long rationales render cleanly. **No issue.** | n/a |

---

## 6. `components/IdeaBox.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 6.1 | **High** | all touch | The per-idea delete button (lines 80-86) is `text-[10px] py-1.5 px-3` ≈ 22 px square — fails tap target. The icon (`✕`) is the only affordance; on phone it is easy to mis-tap and delete the wrong idea. | ≤ 768 px: `min-w-[44px] min-h-[44px]`, with confirmation. |
| 6.2 | Medium | all | The textarea has `rows={3}` and the parent `panel` is full-width inside `aside col-span-4`. Inside the page's `grid-cols-12` (issue 9.1), the column on phone is ~120 px wide → textarea is unusably narrow. | Driven by 9.1; once page stacks, fine. |
| 6.3 | Low | all | Form is full-width and stacks naturally — fine. | n/a |

---

## 7. `components/ProjectSettings.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 7.1 | Medium | all | Save / Reset row (lines 190-208): `flex items-center justify-between` with three children (Reset, "no changes" label, Save). On 360 px the middle label collides with the buttons. | ≤ 640 px: `flex-col-reverse gap-2`, hide "no changes" label, full-width buttons. |
| 7.2 | Low | all | Collapsed-by-default `<details>`-style header (lines 86-92) is fine. But the click target on the chevron is the whole header (`cursor-pointer select-none`) — works on touch. ✓ |
| 7.3 | Low | ≤ 768 px | Settings is rendered in a 4-col aside (col-span-4 in ProjectPage). When the page stacks (9.1 fix), Settings becomes a full-width panel — the `select` and `input` elements at `field` are already `w-full`, fine. | n/a |
| 7.4 | Low | all | The `argus_enabled` checkbox is a native `<input type="checkbox">` with no enlarged hit-area. Default size on iOS/Android is ~16 × 16 px — fail. | ≤ 768 px: wrap in a label with `min-h-[44px]` and `accent-color`. |

---

## 8. `components/DiffViewer.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 8.1 | **Critical** | ≤ 1024 px | The diff is **side-by-side** — 4 `<td>`s per row: gutter / old / gutter / new. On 360 px each text column gets ~120 px → `whitespace-pre-wrap break-all` causes every code line to wrap into 4–8 visual lines, completely destroying alignment. The `overflow-x-auto` wrapper (line 147) helps but the table is `w-full`, so it never overflows — it just wraps. | < 1024 px: render **unified** mode (single column) with +/- markers; keep side-by-side for ≥ 1024 px. Toggle in the header. |
| 8.2 | **High** | ≤ 768 px | Even at 768 px tablet portrait, two ~340-px code columns are too narrow for typical 100-col source. | < 1024: unified. |
| 8.3 | Medium | all | Gutter `w-10` (~40 px) is fine for 4-digit line numbers but wastes space when code lines are short. | n/a — minor. |
| 8.4 | Medium | all | The fallback raw-patch view (line 127) `pre max-h-[420px] overflow-auto whitespace-pre text-[11px]` — correct. ✓ |
| 8.5 | Medium | all | `font-mono text-[11px]` for diff content — 11 px on phone is below comfortable read size. | ≤ 640 px: `text-xs` (12 px). |

---

## 9. `components/DiscoverModal.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 9.1 | **Critical** | ≤ 768 px | The repo table (lines 175-269) has **7 columns** (checkbox / Path / Name / Branch / Last commit / Connector / status). The container is `overflow-y-auto` only — there is no horizontal scroll wrapper, so the table overflows the modal panel and clips on phone. | ≤ 1024 px: wrap `<table>` in `<div class="overflow-x-auto">`; set `<table class="min-w-[720px]">`. |
| 9.2 | **High** | ≤ 768 px | The modal is `w-full max-w-4xl max-h-[85vh] flex flex-col` with `panel p-4` inside `bg-black/60 p-4` (line 120-121). On 360 px the modal body is `360 - 32 = 328 px` wide. The header alone (title + description + Close button) `flex items-center justify-between` has the description text getting squashed. | ≤ 640 px: drop the description on small viewports, or move it to a second row. |
| 9.3 | **High** | all touch | The per-row checkbox is a default native control (~16 px) — tap-target fail on touch. Same for the in-row connector `<select>` and `<input>` (`field !py-0.5` shrinks to ~22 px tall). | ≤ 768 px: minimum `py-2`, `min-h-[44px]`. |
| 9.4 | **High** | ≤ 640 px | Top filter row (lines 150-172) `flex flex-wrap items-center gap-2 text-xs` with: Select all, Clear, "·", "Apply connector to all:" label, `<select>`, "X selected" — wraps to 3 rows on phone, looks chaotic. | ≤ 640 px: stack into a `<details>` summary "Bulk actions" so the table is the primary content. |
| 9.5 | Medium | all | The footer `flex items-center justify-end gap-2` with cancel + register — buttons are 28 px tall. | ≤ 768 px: `min-h-[44px]`; full-width on phone. |
| 9.6 | Medium | all | Modal does not close on backdrop tap. On phone the only way to dismiss is the `close` button (28 px). | all: add `onClick` on the backdrop div + `aria-modal` + ESC handler. |
| 9.7 | Low | all | Modal header description text uses `text-xs text-muted` and never collapses — fine. | n/a |

---

## 10. `pages/LoginPage.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 10.1 | **Critical** | ≤ 420 px | The login card is `<div className="panel w-[420px]">` (line 86) — **fixed 420-px width**. On 360 px the card overflows the viewport horizontally; users see a horizontal scroll bar and the card clipped. | all: change to `w-full max-w-[420px] mx-4` so on small phones it fits with margin. |
| 10.2 | **High** | ≤ 360 px | Outer flex container is `flex min-h-screen items-center justify-center bg-bg` — no horizontal padding, so combined with 10.1 there is no breathing room around the card. | all: add `px-4`. |
| 10.3 | Medium | all | The "Use a hardware key (skip 3-step)" button at `btn w-full` is fine height-wise on desktop but 28 px on phone. | ≤ 768 px: `min-h-[44px]`. |
| 10.4 | Low | all | `autoFocus` on the email/OTP/TOTP fields fires the soft keyboard immediately on iOS — usually desirable for this flow. ✓ |
| 10.5 | Low | all | Form labels are `.label` (`text-xs uppercase`) — fine. ✓ |

---

## 11. `pages/ProjectListPage.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 11.1 | **Critical** | ≤ 1024 px | `<div className="grid grid-cols-3 gap-6">` (line 48) — Projects (col-span-2) + New-project form (col-span-1) at *every* width. On 360 px the form is ~80 px wide; inputs are unusable. | < 1024 px: `grid-cols-1`; ≥ 1024 px: `grid-cols-3`. |
| 11.2 | **High** | all | Projects list cards are `<Link className="panel hover:border-accent">` — `panel` provides `p-4`, but they are nested inside a parent `panel` (double-padding). On phone this looks fine; on desktop the whole list panel feels heavy. | ≤ 640 px: switch nested cards to `border border-border rounded p-3` (no panel double inset). |
| 11.3 | Medium | all | "Discover repos" + project count sit in `flex items-center gap-3 text-xs text-muted` — on phone fine, on tablet fine. ✓ |
| 11.4 | Medium | ≤ 768 px | Each project card row has the description hidden behind `mt-1 text-xs text-muted` (line 79) — fine but small. | n/a |
| 11.5 | Low | all | Form Create button is already `w-full` — ✓. |

---

## 12. `pages/ProjectPage.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 12.1 | **Critical** | ≤ 1024 px | `<div className="grid grid-cols-12 gap-6">` (line 121). Inside: header `col-span-12`, main `col-span-8`, aside `col-span-4`. At every width. On 360 px the main column (TaskBoard + RunPanel) is ~210 px wide, and the aside (IdeaBox + Settings) is ~110 px wide. **All child components break.** | < 1024 px: stack — main (col-span-12) above aside (col-span-12). ≥ 1024 px: `col-span-8` / `col-span-4`. |
| 12.2 | **High** | ≤ 640 px | The page header (line 122-152) is `flex items-center justify-between` with title + workspace path + 2 action buttons (Plan / Run all). On phone the title block + 2 buttons cram into one row and the buttons clip. | ≤ 640 px: `flex-col items-start gap-3`; buttons full-width. |
| 12.3 | Medium | all | Header subtext "workspace_path · default connector: … · max-fix-loops: …" on one line — wraps cleanly but reads poorly on phone. | ≤ 640 px: render each pair on its own line. |
| 12.4 | Medium | all | `runs.refetchInterval: 3000` + `tasks.refetchInterval: 5000` + `plans.refetchInterval: 5000` — three intervals firing constantly will drain mobile battery. | all: pause polling when `document.visibilityState !== "visible"`. |
| 12.5 | Low | all | Run-all confirmation uses `window.confirm` — looks native on phone but cannot be styled. ✓ |

---

## 13. `pages/ConnectorsPage.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 13.1 | **High** | ≤ 1024 px | `<table className="w-full text-sm">` (line 26) with 5 columns including `connector_id` (UUID-ish) and "display name" + Disable button. **No `overflow-x-auto` wrapper** — table overflows the panel on tablet portrait, even worse on phone. | ≤ 1024 px: wrap in `<div class="overflow-x-auto">`, set `min-w-[640px]` on `<table>`. |
| 13.2 | Medium | ≤ 768 px | The `connector_id` column shows the full UUID/slug in `font-mono text-xs` — long values force the row tall (wraps). | ≤ 768 px: hide column or truncate `max-w-[120px] truncate`. |
| 13.3 | Medium | all | Toggle button is single `.btn` at end of row — 28 px tall, fail tap target on touch. | ≤ 768 px: `min-h-[44px]`. |
| 13.4 | Low | all | Table header uses `text-xs uppercase tracking-wide` — readable but 12 px. ✓ |

---

## 14. `pages/AuditPage.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 14.1 | **Critical** | ≤ 1280 px | 5-column table (at / action / target / ip / payload) where the `payload` column renders a `<pre>` with raw JSON (line 42). On a 1280 px viewport the JSON already overflows; on tablet/phone the table extends well past 1500 px and there is **no scroll wrapper**. | all: wrap in `overflow-x-auto`; set `<pre>` to `whitespace-pre-wrap break-all max-w-[40ch]`; ≤ 1024 px collapse the row to a stacked card layout. |
| 14.2 | **High** | ≤ 768 px | `<pre>` payload at `text-[10px]` is below comfortable read size on phone. | ≤ 640 px: `text-[11px]` and `max-h-[120px] overflow-auto` per row. |
| 14.3 | Medium | all | Cert fingerprint slice at `text-[10px]` (line 38) — same readability issue. | ≤ 640 px: hide on phone, show on tap-to-expand. |
| 14.4 | Medium | all | No filter / search / pagination UI; loading 500 events into a table renders a very long page. On phone this is a memory hit. | all: virtualize (react-virtual) or paginate. |

---

## 15. `pages/SecurityPage.tsx`

| # | Severity | Width | Issue | Target |
|---|---|---|---|---|
| 15.1 | **High** | ≤ 768 px | 4-column table (nickname / transports / last used / Remove). **No `overflow-x-auto` wrapper**. On 360 px the Remove button collides with the previous columns and the row clips. | ≤ 1024 px: `overflow-x-auto`; ≤ 640 px stacked cards. |
| 15.2 | Medium | ≤ 640 px | Bottom enroll form (line 92-108) `flex items-end gap-2` with input + button — works because the input is `flex-1`, but the button drops off-screen if there is a long `nickname` placeholder. | ≤ 640 px: `flex-col gap-2`, button full-width. |
| 15.3 | Medium | all | Remove button is `.btn btn-danger` — 28 px tall. | ≤ 768 px: `min-h-[44px]`. |
| 15.4 | Low | all | "No hardware keys yet — enroll one below" empty-state row spans 4 cols — readable. ✓ |

---

## 16. Putting it together — implementation order

A single PR can resolve everything in three layered passes. The phases are ordered so each lands shippable improvements; do not bundle them all into one diff.

### Phase 1 — global plumbing (unlocks everything below)
- **0.3 / 0.5**: Bump `.btn` to `min-h-[40px]` on `≤ md` and add `.btn-touch` modifier; introduce `.subpanel` to stop double-padding; commit `frontend/src/index.css`.
- **0.4**: Add a `<Container>` wrapper component or update `Shell` `main` to `p-3 sm:p-4 lg:p-6`.
- **0.6**: Add a small `<TableScroll>` helper (`<div class="overflow-x-auto">`) used by all four tables.
- **0.9**: Replace xterm window-resize with `ResizeObserver`.

### Phase 2 — page-level grids stack
- **2.1**: Hamburger menu in `Shell.tsx` for `< md`.
- **11.1, 12.1**: Apply `grid-cols-1 lg:grid-cols-3` / `lg:grid-cols-12` so `ProjectListPage` and `ProjectPage` stack on phone and tablet.
- **3.1**: Kanban → horizontal-scroll lane on `< md`, `grid-cols-3 md:grid-cols-6` otherwise.
- **4.1, 4.2, 4.3**: Stack `RunPanel` columns on `< lg`, give the terminal a `60vh` cap, drop xterm font to 11 on phone.
- **8.1**: Add unified-mode rendering to `DiffViewer`; auto-select unified for `< lg`.

### Phase 3 — polish & touch targets
- **2.4, 9.6**: Banner / modal close-on-backdrop and dismiss `×` controls.
- **5.1–5.3, 6.1, 7.1, 7.4, 9.3–9.5, 10.1–10.3, 12.2, 13.1–13.3, 14.1–14.4, 15.1–15.3**: per-component fixes outlined above.
- **12.4**: pause polling on `visibilitychange === 'hidden'`.
- Capture before/after screenshots at 360 / 768 / 1280 in DevTools and attach to the PR description.

---

## 17. Per-component summary checklist

Every component / page in `frontend/src` is listed below — even the ones with no findings — to make this an exhaustive walk and to confirm coverage.

- [ ] `App.tsx` — no layout, only routing. (Note 1: blank screen during boot probe — separate issue.)
- [ ] `components/Shell.tsx` — header overflow, no mobile menu (2.1), padding (2.3), banner dismiss (2.4), logout tap target (2.5).
- [ ] `components/TaskBoard.tsx` — fixed 6-col kanban (3.1), form columns (3.2), Run button tap target (3.3), title size (3.4), tag wrap (3.5), column header (3.6), submit button width (3.7).
- [ ] `components/RunPanel.tsx` — 3-col split (4.1), terminal 420 px height (4.2), font (4.3), no `ResizeObserver` (4.4), action button row (4.5), header layout (4.6), Argus / transcript / diff modals (4.7-4.8), recent-runs cards (4.9), input pill tooltip (4.10).
- [ ] `components/PlanReview.tsx` — 4-col task editor (5.1), Remove button (5.2), card header (5.3).
- [ ] `components/IdeaBox.tsx` — delete tap target (6.1); other issues driven by parent (6.2).
- [ ] `components/ProjectSettings.tsx` — Save/Reset row (7.1), checkbox tap target (7.4); collapsible header is fine.
- [ ] `components/DiffViewer.tsx` — side-by-side at all widths (8.1-8.2), font size (8.5).
- [ ] `components/DiscoverModal.tsx` — 7-col table no scroll wrapper (9.1), header crowding (9.2), in-row controls tap target (9.3), bulk-actions row (9.4), footer (9.5), no backdrop dismiss (9.6).
- [ ] `pages/LoginPage.tsx` — fixed `w-[420px]` (10.1), no horizontal padding (10.2), hardware-key button height (10.3).
- [ ] `pages/ProjectListPage.tsx` — fixed 3-col grid (11.1), nested panel padding (11.2).
- [ ] `pages/ProjectPage.tsx` — fixed 12-col grid (12.1), header collisions (12.2-12.3), polling on hidden tab (12.4).
- [ ] `pages/ConnectorsPage.tsx` — table no scroll wrapper (13.1), UUID column (13.2), toggle tap target (13.3).
- [ ] `pages/AuditPage.tsx` — table + JSON payload no scroll (14.1), font size (14.2-14.3), no pagination (14.4).
- [ ] `pages/SecurityPage.tsx` — table no scroll wrapper (15.1), enroll form layout (15.2), Remove tap target (15.3).

---

## 18. Verification

Once the implementation lands, validate at the three target widths in Chrome DevTools (or Firefox Responsive Design Mode):

1. **360 × 800** — iPhone SE / small Android. Every page must render without horizontal scroll. Tap targets ≥ 44 × 44 px. xterm visible and ≥ 280 px tall.
2. **768 × 1024** — iPad portrait. Two-column page layouts allowed. Kanban shows ≥ 3 columns; modals fit in 720 px width.
3. **1280 × 800** — laptop. Current layout intact; no regressions.

Also run an automated check: `npx pa11y http://localhost:5173` at each width to catch contrast / focus regressions, and `npm run build` to verify the audit-driven CSS changes compile.
