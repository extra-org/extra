---
name: vercel-react-best-practices
description: Apply Vercel React performance guidance when implementing, reviewing, or optimizing the React chat widget, including data fetching, rendering, effects, and bundle size.
license: MIT
metadata:
  author: vercel
---

# Skill: Vercel React Best Practices

## Purpose

Apply Vercel's performance guidance to Extra's React chat widget. The 70
upstream rules and examples are available locally under
`.ai/references/vercel-react-best-practices/rules/`; read the rules relevant
to the change rather than loading the entire collection.

All paths in this playbook are relative to the repository root, including
when reading a generated Codex or Claude adapter.

## When to Use This Skill

- Implementing or reviewing components under
  `src/agent_manager/api/static/widget/react/`.
- Changing widget effects, event listeners, state, or data fetching.
- Investigating slow rendering, unnecessary requests, or bundle growth.

## Files to Read First

- `AGENTS.md` and `.ai/skills/project-architecture.md`.
- `docs/WIDGET_ARCHITECTURE.md` and `docs/WIDGET.md`.
- `package.json`, the components being changed, and their existing tests.
- Relevant rules from the local reference directory, using this routing table:

| Concern | Rule prefix | Starting point |
| --- | --- | --- |
| Sequential independent requests | `async-` | `async-parallel.md` |
| Unnecessary module loading | `bundle-` | `bundle-conditional.md` |
| Duplicate browser subscriptions | `client-` | `client-event-listeners.md` |
| Stale state or unnecessary renders | `rerender-` | `rerender-functional-setstate.md`, `rerender-dependencies.md` |
| Expensive rendering | `rendering-` | `rendering-hoist-jsx.md`, `rendering-content-visibility.md` |
| Repeated JavaScript work | `js-` | `js-set-map-lookups.md`, `js-combine-iterations.md` |
| Callback and initialization patterns | `advanced-` | `advanced-event-handler-refs.md` |
| Server rendering, if introduced separately | `server-` | `server-no-shared-module-state.md` |

## Core Principles

- The shipped widget is React inside a custom element and Shadow DOM, bundled
  with esbuild. Select rules that fit that environment; Next.js routing, RSC,
  SSR caching, and `next/dynamic` examples do not justify adding Next.js.
- Prioritize observable request waterfalls, bundle growth, and rendering costs
  over speculative micro-optimizations. Match APIs to the installed React
  version and esbuild browser target in `package.json`.
- Preserve custom-element attributes/events, Shadow DOM styling, stream
  ownership, cancellation, and approval behavior while optimizing components.
- Keep request-specific identity and credentials out of shared caches.
- Treat upstream examples as guidance. Add dependencies or abstractions only
  when the concrete change benefits from them.

## Process

1. Identify the affected interaction and its performance or correctness issue.
2. Read the relevant local rule files and existing widget implementation.
3. Make a focused change that preserves the widget's public behavior.
4. Run `npm run typecheck:widget` and `npm run test:widget` for widget changes.
   Run `npm run test:widget:e2e` when changing browser interactions such as
   scrolling, focus, streaming, cancellation, or approvals.
5. Rebuild changed widget sources with `npm run build:widget`, include the
   generated `src/agent_manager/api/static/widget.js`, and run `make check`.

## Checklist Before Finishing

- [ ] Applied rules match Extra's React/custom-element architecture.
- [ ] The change fixes an observed problem or has a clear performance rationale.
- [ ] Effect cleanup, callback state, and active stream behavior remain correct.
- [ ] Relevant widget checks pass and the bundle is rebuilt when sources change.
- [ ] `make check` passes.

## Common Mistakes to Avoid

- Applying Next.js-specific examples directly to this browser-only widget.
- Adding blanket memoization or caching without identifying the expensive work.
- Optimizing rendering by removing lifecycle, ownership, or approval checks.
- Editing the built bundle directly or changing generated skill adapters by hand.

## Expected Final Report

Use the `AGENTS.md` report format. State the affected interaction, relevant
rules, why the change helps, checks run, and any remaining performance limits.

## Upstream Source

Adapted from [Vercel React Best Practices](https://github.com/vercel-labs/agent-skills/tree/063bee94c3f4df8453406c830b0a7df0f2860278/skills/react-best-practices),
by Vercel Engineering, version 1.0.0, at commit
`063bee94c3f4df8453406c830b0a7df0f2860278`. Upstream declares the MIT license
in its `SKILL.md`. The 70 local rule files are copies from that revision; the
entrypoint above supplies Extra's project context and commands.

Eleven rules carry local correctness and security corrections from review:
`rendering-usetransition-loading` (stale responses are ignored, since a
transition does not cancel requests), `js-cache-function-results` (pure,
bounded caches only), `bundle-conditional` and `bundle-preload` (a
`typeof window` guard does not affect bundling), `server-cache-lru`
(unambiguous scope-complete keys, and authorization before any cached value is
returned),
`rendering-hydration-no-flicker` (the class change is still a hydration
mismatch), `async-api-routes` (handled early starts and an authentication
exit), `advanced-event-handler-refs` and `advanced-use-latest` (Effect Events
are not stable),
`js-cache-storage` (cookie caches are reset, and cookie changes raise no
event), and `server-after-nonblocking` (work in `after()` is awaited, and
session cookies are not logged). Trailing whitespace was also removed from the
copied rules. Reapply these changes when updating from upstream.
