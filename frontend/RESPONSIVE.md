# Frontend Responsive Conventions

Mobile-first design tokens, breakpoint scale, and container/spacing/typography conventions for the Daedalus SPA. This document is the contract between `tailwind.config.js`, `src/index.css`, and per-component class usage. Pair with `RESPONSIVE_AUDIT.md` for the backlog of pages that still need to adopt these conventions.

## Mobile-first baseline

- **Authoring rule:** unprefixed Tailwind utilities apply at every width and define the **mobile** layout. Use `xs:`, `sm:`, `md:`, `lg:`, `xl:`, `2xl:` prefixes only to scale **up** for wider screens. Never write `lg:hidden` to hide something on desktop without an unprefixed mobile counterpart — the mobile state must always be specified explicitly.
- **Smallest supported viewport:** 360 px (`iPhone SE` portrait). The base CSS (`html, body { overflow-x: hidden }` and `body { min-width: 320px }` in `src/index.css`) ensures the root layout never produces horizontal scroll at 360 px regardless of which route renders.
- **Touch target floor:** 44 × 44 px on viewports ≤ `md` (768 px). The shared `.btn` (28 px tall) and `text-[10px]` action buttons fail this; per-component fixes are tracked in `RESPONSIVE_AUDIT.md`.

## Breakpoint scale

Defined explicitly in `tailwind.config.js` under `theme.screens`. We extend Tailwind's defaults with one custom screen — `xs` for the small-phone tier — and pin the others so the contract is visible in source rather than implicit from defaults.

| Token | Min width | Typical device | Use |
|------:|----------:|---------------|-----|
| _(base)_ | 0 px | small phone (360 px) | mobile-first; one-column stacks, tap-friendly controls |
| `xs` | **480 px** | large phone (portrait) | two-column where it fits without crowding; widen primary inputs |
| `sm` | 640 px | large phone landscape / phablet | inline form rows, side-by-side meta + value pairs |
| `md` | 768 px | tablet portrait | two-pane layouts, persistent nav, tables become viable |
| `lg` | 1024 px | tablet landscape / small laptop | three-pane layouts, kanban grids |
| `xl` | 1280 px | desktop | full kanban grid (`grid-cols-6`), terminal at full height |
| `2xl` | 1536 px | wide desktop | comfortable max content width inside `max-w-shell` |

The shell content cap remains `max-w-[1600px]`, exposed as the `shell` token in `theme.extend.maxWidth` (use `max-w-shell`).

## Root layout invariants

- `index.html` includes `<meta name="viewport" content="width=device-width, initial-scale=1.0" />`.
- `src/App.tsx` wraps `<Routes>` in `<div className="min-h-screen w-full overflow-x-hidden">` so every route — login, project list, error states — gets a fluid full-height container with no horizontal scroll, even before `Shell` mounts.
- `src/components/Shell.tsx` continues to use `flex min-h-screen flex-col`, `max-w-[1600px]`, and `mx-auto` for the centred app shell. `<main>` content padding scales per the spacing scale below (currently `p-6` everywhere; per-page fixes tracked separately).

## Container scale

Pages should use one of these widths; do not introduce ad-hoc `max-w-*` values without a reason.

| Class | Pixels | Use |
|-------|-------:|-----|
| `max-w-prose` | 65ch | long-form text, banners, modal copy |
| `max-w-md` | 28 rem (448 px) | login form, single-column auth dialog |
| `max-w-2xl` | 42 rem (672 px) | settings panel, single-task editor |
| `max-w-4xl` | 56 rem (896 px) | review panes (PlanReview, AuditPage rows) |
| `max-w-shell` | 1600 px | top-level `<main>` and `<header>` shells |

All page-level containers must include `w-full` before any `max-w-*` cap so they shrink at narrow widths instead of overflowing fixed pixel widths (see audit issue 0.8).

## Spacing scale (page padding & gaps)

Mobile-first; widen with `sm:` / `lg:` rather than starting at desktop and clipping down.

| Token | Where | Example |
|-------|-------|---------|
| `p-3 sm:p-4 lg:p-6` | page outer padding | `<main>`, `Shell` content |
| `p-2 sm:p-3 lg:p-4` | nested panel padding | inner `.subpanel` (when nesting `.panel`) |
| `gap-3 lg:gap-6` | grid / flex gaps in dense layouts | TaskBoard columns, RunPanel rows |
| `gap-2 sm:gap-3` | tight inline groupings | tag rows, button groups |
| `gap-1` | dense pill clusters | `flex flex-wrap` tag containers |

Avoid `p-6` / `gap-6` unprefixed: at 360 px that is 48 px of horizontal real estate burned before content starts (audit issue 0.4).

## Typography scale

| Class | Pixels | Use |
|-------|-------:|-----|
| `text-[10px]` | 10 px | **avoid on touch viewports.** Reserved for desktop-only chrome (status pills inside dense desktop tables). |
| `text-xs` | 12 px | desktop dense labels, secondary metadata |
| `text-sm` | 14 px | mobile-default body text, primary form values |
| `text-base` | 16 px | mobile primary copy, login fields (matches OS no-zoom minimum on iOS) |
| `text-lg` | 18 px | section headings, brand mark in `Shell` |
| `text-xl` / `text-2xl` | 20 / 24 px | page titles |

**Rule:** below `md` (768 px), no interactive text smaller than `text-xs`; no readable copy smaller than `text-sm`. Use `md:text-xs` to opt back into 12 px on tablet+.

## Touch target & interaction conventions

- Interactive elements on touch viewports (≤ `md`) need either `min-h-[44px]` or padding that reaches it (`py-2.5` on `text-sm` ≈ 44 px).
- Apply mobile floors with `md:` to opt back to dense desktop sizing: `min-h-[44px] md:min-h-0 md:py-1.5`.
- Tables: wrap with `<div className="overflow-x-auto">` and set `min-w-[640px]` (or larger) on `<table>` so they horizontally-scroll on phone instead of crushing columns.
- Grids: every `grid-cols-N` where N ≥ 3 must specify a mobile fallback. Pattern: `grid-cols-1 md:grid-cols-2 lg:grid-cols-N`.

## Adding a new component

1. Author the mobile layout first (no breakpoint prefixes).
2. Add `xs:` / `sm:` / `md:` / `lg:` / `xl:` modifiers as the layout grows; do not skip a tier without thinking through the in-between widths.
3. Verify at 360 / 480 / 768 / 1024 / 1280 px in DevTools (`Cmd-Shift-M`).
4. Confirm no horizontal scroll on the root layout at 360 px (the global `overflow-x-hidden` will hide it, but content should still _fit_ rather than be clipped).
5. Confirm interactive elements meet the 44 px floor on ≤ `md`.

## Where the conventions live in code

- **`frontend/tailwind.config.js`** — `theme.screens` defines the breakpoint tokens; `theme.extend.maxWidth.shell` defines the content cap.
- **`frontend/src/index.css`** — base CSS sets `overflow-x: hidden` on `html, body` and `min-width: 320px` on `body`. Component classes (`.btn`, `.panel`, `.field`, `.tag`, `.status-pill`) are still the shared building blocks; per-component mobile variants will be added as the audit backlog is worked.
- **`frontend/src/App.tsx`** — root container `min-h-screen w-full overflow-x-hidden`.
- **`frontend/index.html`** — viewport meta tag.

## Out of scope for this document

- Per-page rewrites (kanban → mobile lane, header → hamburger, table wrapping). Tracked in `RESPONSIVE_AUDIT.md`.
- Visual design tokens (colour, motion, elevation). Colour tokens already live in `tailwind.config.js`; motion/elevation are not yet codified.
