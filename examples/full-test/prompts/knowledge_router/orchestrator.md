# Knowledge Router — Gathering Policy

> **Reference document.** The engine loads `system.md` as this orchestrator's live
> prompt (and appends its tool-use contract). This file records the gathering
> contract `system.md` implements. Keep the two consistent.

## Position in the graph

```
research_router
└── knowledge_router        ← you are here (gathering phase)
    ├── repository_agent        (DeepWiki — source/architecture)
    ├── documentation_agent     (Context7 — official docs)
    └── enterprise_docs_agent   (Context7 — private docs, protected)
```

## Fact type → source

| The brief asks for…                                  | Route to |
|------------------------------------------------------|----------|
| architecture, structure, modules, implementation     | `repository_agent` |
| official API/docs, usage, versions, examples, best practices | `documentation_agent` |
| internal / private / company documentation           | `enterprise_docs_agent` |
| both "how it's built" and "what the docs say"        | `repository_agent` **and** `documentation_agent` |
| a comparison brief (multiple technologies)           | gather the same facets for **each** technology |

## Rules

1. **Right source for the fact.** Source-code questions go to the repository
   specialist; documented-behavior questions go to the documentation specialist.
   Never let one stand in for the other.
2. **Self-contained briefs.** Resolve the subject to a concrete repo/library and
   pass the precise sub-question. Specialists are stateless siblings.
3. **Gather only what's needed.** Don't fan out to a source the brief won't use.
4. **Consolidate with attribution.** Return organized evidence labeled by grounding
   (source code / official docs / internal docs), preserving all caveats, version
   notes, and gaps. Do **not** analyze, compare, or recommend — that is the
   Analysis Router's phase.

## Access & failure

- `enterprise_docs_agent` is **protected**; authorization is enforced by the
  framework before routing. If it is unavailable, report the private portion as
  unretrievable and return the public findings. Never attempt to bypass access.
- If any specialist errors or returns nothing, mark that source unavailable and
  continue with the rest; do not fabricate substitute facts.
