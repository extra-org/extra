---
title: Cache Storage API Calls
impact: LOW-MEDIUM
impactDescription: reduces expensive I/O
tags: javascript, localStorage, storage, caching, performance
---

## Cache Storage API Calls

`localStorage`, `sessionStorage`, and `document.cookie` are synchronous and expensive. Cache reads in memory.

**Incorrect (reads storage on every call):**

```typescript
function getTheme() {
  return localStorage.getItem('theme') ?? 'light'
}
// Called 10 times = 10 storage reads
```

**Correct (Map cache):**

```typescript
const storageCache = new Map<string, string | null>()

function getLocalStorage(key: string) {
  if (!storageCache.has(key)) {
    storageCache.set(key, localStorage.getItem(key))
  }
  return storageCache.get(key)
}

function setLocalStorage(key: string, value: string) {
  localStorage.setItem(key, value)
  storageCache.set(key, value)  // keep cache in sync
}
```

Use a Map (not a hook) so it works everywhere: utilities, event handlers, not just React components.

**Cookie caching:**

```typescript
let cookieCache: Record<string, string> | null = null

function getCookie(name: string) {
  if (!cookieCache) {
    cookieCache = Object.fromEntries(
      document.cookie
        .split('; ')
        .filter(Boolean)
        // Split on the first "=" only; cookie values may contain "="
        .map(c => {
          const i = c.indexOf('=')
          return i < 0 ? ['', c] : [c.slice(0, i), c.slice(i + 1)]
        })
    )
  }
  return cookieCache[name]
}

function setCookie(cookie: string) {
  document.cookie = cookie
  cookieCache = null  // re-read on next access
}
```

**Important (invalidate on external changes):**

If storage can change outside this code (another tab, the server), invalidate both caches:

```typescript
window.addEventListener('storage', (e) => {
  if (e.key) storageCache.delete(e.key)
  else storageCache.clear()  // storage.clear() in another tab
})

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    storageCache.clear()
    cookieCache = null
  }
})
```

The `storage` event fires only for `localStorage`/`sessionStorage` changes made by other documents; it never fires for cookies. A cookie set by a server response (`Set-Cookie`) or by other code changes with no event at all, so cache a cookie only when your own code is its sole writer, and reset `cookieCache` after any request that can set it. Otherwise read `document.cookie` directly.
