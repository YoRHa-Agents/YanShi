# Design

## Mood

Mythic dark command atelier: a near-black workshop where an artisan pulls fine amber control threads
through precise mechanical forms.

## Palette

Use OKLCH tokens and preserve WCAG AA contrast.

- `--color-bg`: `oklch(0.145 0.012 285)` — near-black neutral, the primary stage.
- `--color-surface`: `oklch(0.205 0.018 285)` — raised panels and code blocks.
- `--color-surface-2`: `oklch(0.275 0.022 285)` — diagrams, secondary bands, table headers.
- `--color-ink`: `oklch(0.94 0.018 92)` — primary text on dark.
- `--color-muted`: `oklch(0.74 0.025 285)` — secondary text; verify contrast before use below
  body size.
- `--color-thread`: `oklch(0.842 0.165 91.3)` — warm amber/honey-gold control-thread accent.
- `--color-thread-strong`: `oklch(0.78 0.18 82)` — hover, focus rings, active state.
- `--color-shadow`: `oklch(0.36 0.08 305)` — ink-purple shadow accent and depth.
- `--color-danger`: `oklch(0.67 0.20 28)` — explicit errors only.

## Typography

Choose type that feels technical without defaulting to a generic mono costume. Prefer a crisp text
sans for prose, paired with a narrow or engraved-feeling display face only for major headings. Keep
body copy at 65-75ch, use slightly looser line-height on dark backgrounds, and reserve monospace for
commands, config keys, paths, and protocol objects.

## Layout

Lead with one strong proposition: the parent agent as artisan, YanShi as the control frame, adapter
CLIs as mechanisms. Use asymmetric dark sections, thin amber connector lines, and compact technical
diagrams. Avoid repeated card grids; when grouping is necessary, prefer timelines, contract tables,
or thread-like flows that explain dispatch -> monitor -> pull.

## Components & Sections

- Hero: name as `YanShi 偃师`, short technical promise, and a restrained mythic note.
- Onboarding: install, doctor, first dispatch, status, summary, improve loop. Show commands as
  ritual steps without changing their syntax.
- Architecture: visibility plane vs context plane, disk persistence, deterministic reducer, optional
  summarizer.
- Safety: read-only default, argv-only spawn, secret redaction, explicit errors, cost ceilings.
- Compatibility: adapter table for `claude`, `codex`, `cursor`, `gemini`; keep vendor-neutral tone.
- Docs navigation: quickstart, skill contract, source-of-truth spec, development commands.

## Motion

Motion should feel like threads being tensioned: subtle line draws, focus glows, and short reveal
sequences around diagrams. Do not hide content before animation starts. Under
`prefers-reduced-motion: reduce`, replace movement with static states or instant transitions.

## Accessibility

Meet WCAG AA. Keep amber accents decorative or redundant with labels/icons; never rely on color alone
for state. Provide visible focus states using `--color-thread-strong`, maintain keyboard access for
all interactive elements, and avoid parallax, flicker, or continuous glow animations. Code blocks and
tables must remain readable on small screens without horizontal information loss where possible.
