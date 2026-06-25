# Product

## Register

brand

## Users

YanShi serves senior agent builders, platform engineers, and automation authors who already use
headless agent CLIs but need one reliable way to dispatch sub-agents across vendors. They are often
working inside a parent agent, CI job, or long-lived host process where every extra log line competes
with useful context.

## Product Purpose

YanShi is a Python 3.12+ vendor-neutral sub-agent dispatch layer. It gives a parent agent one
contract for spawning agent CLIs, then returns deterministic, low-context status and summary views
while raw NDJSON remains on disk. Success means orchestration feels controlled: the parent chooses
the mechanism, pulls only the control threads it needs, and never has to drink from a log firehose.

## Brand Personality

Mythic, precise, sovereign. The voice should feel like a calm master artisan describing a reliable
machine: ceremonial enough to be memorable, exact enough to earn trust, and restrained enough that
the metaphor never obscures the contract.

## Anti-references

Avoid generic SaaS gradient cards, bland robot mascots, the overdone wuxia "Yan Shisan" reading,
logs-as-firehose screenshots, and template-like AI landing pages. Do not turn the myth into cosplay;
no sword fantasy, fake lore, or decorative mysticism that weakens technical clarity.

## Design Principles

- **The artisan stays in control.** Present the parent agent as the coordinating hand, with sub-agent
  CLIs as mechanisms it can safely choose, observe, and stop.
- **Control threads over spectacle.** Status, summary, and safety invariants are the narrative
  center; raw logs and noisy screenshots are supporting audit material, not the hero.
- **One contract, no vendor throne.** The brand should feel sovereign without implying loyalty to
  any single model provider or CLI.
- **Determinism before divination.** Any magical language must point back to explicit state machines,
  pulled summaries, redaction, argv-only spawn, and surfaced errors.
- **Darkness with legibility.** Mythic dark is a stage for precise reading, not an excuse for low
  contrast or decorative haze.

## Accessibility & Inclusion

Target WCAG AA across public README/docs and future web surfaces. Body text must meet at least 4.5:1
contrast, large text at least 3:1, and color must never be the only carrier of meaning. Motion should
be reduced or disabled under `prefers-reduced-motion: reduce`; essential onboarding and CLI guidance
must remain fully usable without animation, hover, or visual ornament.
