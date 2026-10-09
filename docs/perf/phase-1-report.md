# Phase 1 Report — Navigation Foundation

## What was changed:
- Converted all 55 sidebar navigation `<a>` tags to `<Link>` from `next/link` in `components/layout/sidebar.tsx`
- Added `import Link from 'next/link'` to the sidebar file
- **Phase 1.5 prerequisite complete**: Full auth cookie migration implemented

## Backend changes (auth.py):
- Added `Response` import from FastAPI
- Created `_set_auth_cookies()` helper with conditional `secure` flag (uses `get_settings().is_production`)
- Modified `_login_response()` to accept optional `Response` and set `access_token`/`refresh_token` httpOnly cookies:
  - `httponly=True` (security)
  - `secure=is_production` (works in dev HTTP)
  - `samesite='strict'` (CSRF protection)
  - `path='/'`
- Updated `login()` to accept `response: Response` and set cookies on login + replay
- Updated `refresh()` to set new cookies on token rotation
- Updated `logout()` and `logout_all()` to clear cookies on server response

## Backend changes (deps.py):
- `current_principal` now falls back to reading `access_token` cookie from `request.cookies`
- Enables both Bearer header auth AND cookie auth

## Frontend changes (server component auth):
- Created `frontend/lib/server-api-client.ts` - native fetch wrapper that forwards cookies via `next/headers cookies()`
- Created `frontend/lib/server-auth.ts` - helper for server-side auth checks
- Updated `frontend/app/(protected)/layout.tsx` to server component:
  - Reads `access_token` cookie via `next/headers`
  - Validates via `serverApi.get('/auth/me')`
  - Redirects to `/login` if not authenticated
  - Passes `user` prop to `AppShell`
- Updated `frontend/components/layout/app-shell.tsx`:
  - Accepts `user` prop
  - Wraps children in `SessionContext.Provider` with the user
- Updated `frontend/lib/auth.ts`:
  - Login continues storing in localStorage (backwards compatibility with client components)
  - Cookies handle SSR/server component auth

## Verification:
- Build passes (all routes render correctly as dynamic server-rendered)
- `/auth/login` returns httpOnly cookies in dev (secure=False)
- `/auth/me` succeeds when cookie forwarded via server-api-client
- ProtectedLayout (server component) successfully reads cookie and validates

## Files touched:
- `frontend/components/layout/sidebar.tsx`
- `backend/app/api/v1/auth.py`
- `backend/app/authz/deps.py`
- `frontend/lib/server-api-client.ts` (new)
- `frontend/lib/server-auth.ts` (new)
- `frontend/app/(protected)/layout.tsx`
- `frontend/components/layout/app-shell.tsx`
- `frontend/lib/auth.ts`

## Phase 1 status: **COMPLETE**

## Phase 1.5 status: **COMPLETE** (auth infrastructure)

### All infrastructure requirements met:
- ✅ Backend sets httpOnly cookies on login/refresh (`secure=False` in dev)
- ✅ Backend clears cookies on logout
- ✅ `deps.py` `current_principal` reads both Bearer header AND cookie
- ✅ Frontend ProtectedLayout is server component
- ✅ Server API client forwards cookies correctly via `next/headers`
- ✅ AppShell provides SessionContext from server-resolved user

### Page conversion status: ⏸️ PAUSED (awaiting strategy approval)
- **POS page**: Best kept as client component (complex checkout workflow)
- **Dashboard**: Good candidate for RSC + Suspense (read-only display)
- **17 list/detail pages**: Good candidates for RSC

### Strategy options presented to user (see Plan §1.5)
- Full RSC conversion
- Hybrid approach (RSC data + client interactive)
- Lazy adoption

## Cassandra sign-off: PENDING — page conversion strategy
## Prob sign-off: ✅ COMPLETE — cookies work in dev (secure=False), round-trip verified