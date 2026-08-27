"""Auth foundation package — passwords, tokens, sessions, principal, service."""

from __future__ import annotations

from app.auth.password import (
    PasswordAuthenticator,
    PasswordPolicy,
    PasswordUnsetError,
    RecordLockedError,
)
from app.auth.principal import Principal, SessionContext
from app.auth.service import AuthService, LoginResult
from app.auth.session_store import (
    SessionRecord,
    SessionStore,
    hash_token,
    new_token,
    verify_token,
)
from app.auth.tokens import (
    TokenPair,
    generate_token,
    hash_opaque,
)

__all__ = [
    "AuthService",
    "LoginResult",
    "PasswordAuthenticator",
    "PasswordPolicy",
    "PasswordUnsetError",
    "Principal",
    "RecordLockedError",
    "SessionContext",
    "SessionRecord",
    "SessionStore",
    "TokenPair",
    "generate_token",
    "hash_opaque",
    "hash_token",
    "new_token",
    "verify_token",
]
