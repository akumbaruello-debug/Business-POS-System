"""Audit logging infrastructure.

Implements ``Backend-Architecture-V1.0.md`` §18 Audit Architecture:

- ``app/audit/service.py`` — the append-only ``audit_log`` write helper,
  called by the service layer for every create / update / deactivate.
- ``app/audit/query.py`` — read-only query helpers.

The ``audit_log`` table is append-only: UPDATE/DELETE is blocked by a
DB trigger (see ``schema.sql``). We only INSERT here.
"""

from __future__ import annotations

from app.audit.service import (
    AuditContext,
    write_audit,
)

__all__ = [
    "AuditContext",
    "write_audit",
]
