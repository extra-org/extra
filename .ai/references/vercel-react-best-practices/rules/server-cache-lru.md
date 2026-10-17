---
title: Cross-Request LRU Caching
impact: HIGH
impactDescription: caches across requests
tags: server, cache, lru, cross-request
---

## Cross-Request LRU Caching

`React.cache()` only works within one request. For data shared across sequential requests (user clicks button A then button B), use an LRU cache.

**Implementation:**

```typescript
import { LRUCache } from 'lru-cache'

const cache = new LRUCache<string, any>({
  max: 1000,
  ttl: 5 * 60 * 1000  // 5 minutes
})

// Not exported: only reachable after the caller is authorized
async function loadUser(tenantId: string, id: string) {
  // Key by every input that changes the result, never by the record ID alone.
  // Encode the parts unambiguously: with a plain separator, ("a:b", "c") and
  // ("a", "b:c") would collide when an ID can contain the separator.
  const key = JSON.stringify([tenantId, id])
  const cached = cache.get(key)
  if (cached) return cached

  const user = await db.user.findFirst({ where: { tenantId, id } })
  cache.set(key, user)
  return user
}

export async function getUser(caller: Caller, id: string) {
  // Authorize on every request, before any cached data can be returned
  if (!(await canReadUser(caller, id))) {
    throw new ForbiddenError()
  }
  // The tenant comes from the authenticated caller, never from request input
  return loadUser(caller.tenantId, id)
}

// Request 1: authorized, DB query, result cached
// Request 2: authorized again, cache hit, no DB query
```

Use when sequential user actions hit multiple endpoints needing the same data within seconds.

This cache is shared by every request in the process, so only the unexported loader touches it and every exported entry point authorizes the caller first. Cache only data that is identical for every caller allowed to see it, and never cache the authorization decision or caller-specific views. Keep the TTL short, because permission and data changes take effect only when an entry expires.

**With Vercel's [Fluid Compute](https://vercel.com/docs/fluid-compute):** LRU caching is especially effective because multiple concurrent requests can share the same function instance and cache. This means the cache persists across requests without needing external storage like Redis.

**In traditional serverless:** Each invocation runs in isolation, so consider Redis for cross-process caching.

Reference: [https://github.com/isaacs/node-lru-cache](https://github.com/isaacs/node-lru-cache)
