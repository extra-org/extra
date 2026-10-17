---
title: Cache Repeated Function Calls
impact: MEDIUM
impactDescription: avoid redundant computation
tags: javascript, cache, memoization, performance
---

## Cache Repeated Function Calls

Use a module-level Map to cache the results of a pure function that is called repeatedly with the same inputs during render. Cache only results that depend solely on the arguments, and bound the cache: a module-level Map lives as long as the page or server process, so unbounded inputs make it grow forever. Never cache user- or request-specific results in module scope on a server.

**Incorrect (redundant computation):**

```typescript
function ProjectList({ projects }: { projects: Project[] }) {
  return (
    <div>
      {projects.map(project => {
        // slugify() called 100+ times for same project names
        const slug = slugify(project.name)

        return <ProjectCard key={project.id} slug={slug} />
      })}
    </div>
  )
}
```

**Correct (cached results):**

```typescript
// Module-level cache for a pure function, bounded by size
const MAX_SLUGS = 1000
const slugifyCache = new Map<string, string>()

function cachedSlugify(text: string): string {
  const cached = slugifyCache.get(text)
  if (cached !== undefined) {
    return cached
  }
  const result = slugify(text)
  if (slugifyCache.size >= MAX_SLUGS) {
    // Maps keep insertion order, so this evicts the oldest entry
    slugifyCache.delete(slugifyCache.keys().next().value!)
  }
  slugifyCache.set(text, result)
  return result
}

function ProjectList({ projects }: { projects: Project[] }) {
  return (
    <div>
      {projects.map(project => {
        // Computed only once per unique project name
        const slug = cachedSlugify(project.name)

        return <ProjectCard key={project.id} slug={slug} />
      })}
    </div>
  )
}
```

**Single-value cache with explicit invalidation:**

This result depends on browser state, not arguments, so it is correct only if every change to that state calls the invalidation function. If you cannot guarantee that, recompute instead.

```typescript
let isLoggedInCache: boolean | null = null

function isLoggedIn(): boolean {
  if (isLoggedInCache !== null) {
    return isLoggedInCache
  }

  isLoggedInCache = document.cookie.includes('auth=')
  return isLoggedInCache
}

// Clear cache when auth changes
function onAuthChange() {
  isLoggedInCache = null
}
```

Use a Map (not a hook) so it works everywhere: utilities, event handlers, not just React components.

Reference: [How we made the Vercel Dashboard twice as fast](https://vercel.com/blog/how-we-made-the-vercel-dashboard-twice-as-fast)
