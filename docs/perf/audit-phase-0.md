# POS V2 Performance Audit - Phase 0

## Executive Summary

After thorough audit of the V2 branch (checksum: `ccc52c8`), I've identified **critical architectural issues** that prevent fast first load and instant navigation. The root causes are:

1. **50 client components** (including layouts) — prevents SSR and partial prerendering
2. **No Next.js 16 caching features enabled** — missing `cacheComponents`, `use cache`, `cacheLife`
3. **Sidebar uses `<a>` tags** — no prefetching, full page reloads possible
4. **Auth via localStorage only** — server components cannot authenticate users

---

## 1. Next.js Architecture Audit

### Framework Version
- **Next.js**: 16.3.4
- **React**: 19.2.8
- **Bundle mode**: server (production build)
- **Caching**: NOT enabled

### next.config.mjs Analysis
```javascript
// File: frontend/next.config.mjs
export default {
  images: { unoptimized: true }
}
```

**Missing configurations:**
- No `cacheComponents: true`
- No `experimental.staleTimes` or built-in Next.js 16 caching
- No `fontSource: 'local'` or optimized font loading
- Image optimization disabled (`unoptimized: true`)

### Component Classification

| Component | Server/Client | Notes |
|-----------|---------------|-------|
| `app/layout.tsx` | **SERVER** | Root layout OK |
| `app/(protected)/layout.tsx` | **CLIENT** | Uses `'use client'`, check tokens from localStorage |
| `app/(protected)/dashboard/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/pos/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/sales/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/inventory/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/customers/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/payments/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/reports/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/finance/**` | CLIENT | Multiple pages |
| `app/(protected)/products/page.tsx` | CLIENT | Data fetch in useEffect |
| `app/(protected)/purchases/**` | CLIENT | Multiple pages |
| `components/layout/app-shell.tsx` | CLIENT | Uses useState for UI state |
| `components/layout/sidebar.tsx` | CLIENT | Navigation with `<a>` tags |

**Critical Finding**: Root layout is server component, but **all protected layouts and pages are client components**. This defeats the purpose of Next.js App Router for performance.

### Loading Files
**Zero `loading.tsx` or `error.tsx` files exist** in the entire `app/` directory. This means:
- No Suspense boundaries
- No React 19 automatic batching UI feedback
- Navigation feels abrupt without transition states

---

## 2. Navigation Audit

### Navigation Pattern Found
```tsx
// In sidebar.tsx (lines ~163-172)
<a href="/dashboard">...</a>
<a href="/pos">...</a>
<a href="/sales">...</a>
// etc.
```

**Problem**: Using `<a>` tags instead of Next.js `<Link>` means:
- No automatic prefetch on hover
- Full page reloads in non-SPA fallback
- No route caching between navigations
- No optimized client-side transitions

### Page Component Data Fetching Pattern

**Dashboard** (`dashboard/page.tsx`):
```typescript
// Lines 686-690
const [dashRes, invRes, salesRes] = await Promise.allSettled([
  fetchDashboard(params),
  fetchInventory(),
  api.get<SalesReportResponse>('/reports/sales', { params }),
])
```
✅ **3 parallel requests** (correct pattern)

**POS Page** (`pos/page.tsx`):
- Uses `Promise.all` for 3 parallel fetches (products, contacts, payment-methods)
✅ Correct pattern

**Sales Page** (`sales/page.tsx`):
- Uses `useEffect` for data fetching
- No parallel/concurrent fetching pattern visible
- Fetch triggers on mount only

**Key Issue**: All pages use **client-side useEffect data fetching**. This means:
- No initial HTML contains page data
- Users see loading state after JS hydration
- No server-rendered content for SEO/social

---

## 3. Caching Capabilities Audit

### What's Available in Next.js 16+
- `cacheComponents` (opt-in)
- `use cache` for server actions
- `cacheLife` for fine-grained revalidation
- `cacheTag` / `revalidateTag` / `updateTag`
- Partial prerendering with `<Suspense>`
- BFCache support (no blockers found)

### What's Actually Present
| Feature | Status | Impact |
|---------|--------|--------|
| `cacheComponents` | ❌ NOT configured | Can't cache server components |
| `use cache` | ❌ Not used | API calls must be refetched |
| `loading.tsx` | ❌ Missing | No Suspense boundaries |
| `fetch caching` | ❌ Manual only | Every nav triggers fresh requests |
| Next.js Font Optimization | ❌ Using globals.css | 9,972 char CSS blob, not optimized |

### Browser Cache Opportunity
- **No `unload`, `beforeunload`, `pagehide`, `pageshow` event listeners** in app code
- **Bfcache friendly** — no scroll position restoration needed
- Can benefit from `pageCache` in Next.js 16

---

## 4. Bundle Analysis

### Production Build Output
- **Total .next/static size**: ~2.0MB
- **Root chunk**: 229KB uncompressed (Next.js runtime + React)
- **Multiple large chunks** present but not analyzed fully due to build output structure

### Key Dependencies
From `package.json`:
- `lucide-react`: 48 icons used per page
- Several `date-fns` locales
- Charting libraries (not confirmed)

### Bundle Concerns
- 50 client components means significant JS for interactivity
- No `next/font` — fonts inlined in CSS or loaded separately

---

## 5. Auth Model (CRITICAL)

### Current Implementation
- **Tokens stored in localStorage** (`access_token`, `refresh_token`)
- **No cookies set** by `/auth/login`, `/auth/refresh`, `/auth/logout` endpoints
- Verified: `backend/app/api/v1/auth.py` has zero `set_cookie` calls

### Impact on Performance
**Server-side ProtectedLayout is IMPOSSIBLE without cookie migration** because:
- Server components cannot read `localStorage`
- No way to authenticate server-side
- Every page requires full client-side hydration

### Route Protection Pattern
```tsx
// app/(protected)/layout.tsx
const token = localStorage.getItem('access_token')
if (!token) redirect('/login')
```
This runs **client-side only**, meaning:
- Protected routes render briefly before redirect
- Flash of unauthenticated UI possible
- No optimistic navigation

---

## 6. State Preservation Analysis

### AppShell State
```tsx
// app-shell.tsx
const [collapsed, setCollapsed] = useState(false)
const [mobileOpen, setMobileOpen] = useState(false)
const [accountOpen, setAccountOpen] = useState(false)
```

**Problem**: This state is **inside a client component** (`app-shell.tsx`), which remounts on every route change because the protected layout is also a client component.

**Result**: Sidebar collapse state resets on every navigation.

---

## 7. Recommendations Summary

### Phase 1: Enable Next.js 16 Caching (Build-time)
1. Add `cacheComponents: true` to `next.config.mjs`
2. Create `loading.tsx` files for all layout types
3. Wrap data fetching in `use cache` where possible

### Phase 2: Partial Prerendering (Build-time)
1. Identify static parts of layouts (sidebar, header)
2. Convert to **server components** with client islands
3. Add `cacheLife` annotations for infrequently changing data

### Critical Decision Point
**@user's auth transport decision required**:

**Option A: Keep localStorage tokens**
- Cannot implement server-side auth
- Must keep all components as client
- Implement client-side caching strategies
- Use `<Link>` for navigation

**Option B: Migrate to httpOnly cookies** ⚠️ BLOCKING
- Allows server components to read auth state
- Enables ProtectedLayout as server component
- Enables full SSR benefits
- Requires backend changes (cookie setting in auth.py)

---

## 8. Action Items (Per Plan Section 4.3)

1. ✅ Verify Next.js version → 16.3.4
2. ✅ Audit client/server component usage → 50 client components
3. ✅ Map layout structure → Root server, protected client
4. ✅ Analyze sidebar navigation → Uses `<a>` not `<Link>`
5. ✅ Check loading/error files → None exist
6. ✅ Review data fetching patterns → Client-side useEffect
7. ✅ Inspect globals.css → 9,972 chars, no next/font
8. ✅ Verify auth mechanism → localStorage only, no cookies
9. ✅ Check for bfcache blockers → None found
10. ✅ Build production → Complete

---

## 9. Open Questions for @user

1. **Authentication**: Do you want to migrate auth to httpOnly cookies? This would unlock SSR benefits.
2. **Bundle optimization**: Should we investigate tree-shaking for lucide-react icons?
3. **Route-based code splitting**: Should we optimize the large root chunks?

---

*Report generated: Phase 0 Audit Complete*
*Next step: Await @user's auth transport decision to proceed with optimizations*