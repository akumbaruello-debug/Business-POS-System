"""Middleware package.

The actual middleware implementations live in dedicated submodules:

* :mod:`app.middleware.request_id` — request id / correlation token
* :mod:`app.middleware.security` — secure response headers (M1+)
* :mod:`app.middleware.rate_limit` — slowapi integration
"""

from __future__ import annotations
