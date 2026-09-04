"""F.9 - Settings API request/response schemas.

Mirrors ``openapi.yaml`` ``SettingsMap`` (line 8966) and ``SettingsPatch``
(line 8979) exactly.

OAS shape
---------
Both schemas are flat maps::

    SettingsMap:
      type: object
      additionalProperties:
        oneOf: [string, number, boolean, object]

    SettingsPatch:
      type: object
      additionalProperties:
        oneOf: [string, number, boolean, object]

The keys are the setting names (``costing_method``, ``is_initialized``,
``company_name``, ...). The value type is polymorphic. We model this
with Pydantic ``RootModel[dict[str, Any]]`` because Pydantic's
``BaseModel`` with a fixed set of field names is the wrong tool for a
runtime-extensible map. The ``extra=forbid`` config on the
``SettingsPatch`` root enforces the known-keys whitelist (preventing
mass-assignment of arbitrary columns).

Read-only keys
--------------
``costing_method`` is read-only in V1 per the OpenAPI summary and
``schema.sql`` ``fn_system_settings_readonly_keys()`` trigger. The
service layer pre-validates PATCH payloads and returns 400 before
hitting the DB so the contract envelope stays clean (the trigger's
``RAISE EXCEPTION`` would otherwise surface as a 23514 ``check_violation``
-> 500 from the global handler).
"""

from __future__ import annotations

from typing import Any

from pydantic import RootModel, model_validator

__all__ = [
    "SettingsMap",
    "SettingsPatch",
    "KNOWN_SETTING_KEYS",
    "READONLY_SETTING_KEYS",
    "SETTING_VALUE_TYPES",
]

# Whitelist of known setting keys (schema.sql:2384-2394). The service
# rejects any PATCH key outside this set so unknown JSON keys cannot
# mutate arbitrary columns.
KNOWN_SETTING_KEYS: frozenset[str] = frozenset(
    {
        "costing_method",
        "default_negative_stock_allowed",
        "default_low_stock_threshold",
        "default_posting_timing",
        "enable_sequential_doc_numbers",
        "is_initialized",
        "display_rounding",
        "company_name",
        "company_address",
        "adjustment_creates_pnl_entry",
    }
)

# Keys the PATCH endpoint refuses (read-only in V1).
READONLY_SETTING_KEYS: frozenset[str] = frozenset({"costing_method"})

# Allowed ``value_type`` tags from ``schema.sql``
# ``ck_system_settings_value_type`` ('string', 'number', 'boolean',
# 'json'). JSON values are object-shaped (additionalProperties=true per
# OAS); everything else is scalar.
SETTING_VALUE_TYPES: frozenset[str] = frozenset({"string", "number", "boolean", "json"})


class SettingsMap(RootModel[dict[str, Any]]):
    """Response shape for ``GET /settings`` and ``PATCH /settings``.

    Built by :meth:`from_rows` from the service's typed
    ``key -> deserialised value`` dict. Returned to the client as a
    flat JSON object. ``RootModel`` cannot enforce ``extra=forbid``;
    the whitelist check is delegated to the service layer (which
    knows the canonical list via :data:`KNOWN_SETTING_KEYS`).
    """

    @classmethod
    def from_rows(cls, rows: dict[str, Any]) -> SettingsMap:
        """Build a ``SettingsMap`` from a typed ``key -> value`` dict.

        ``rows`` comes from the service's deserialisation layer.
        """
        return cls(root=dict(rows))

    def to_value_dict(self) -> dict[str, Any]:
        """Return the plain Python ``key -> value`` map for the response."""
        return dict(self.root)


class SettingsPatch(RootModel[dict[str, Any]]):
    """Request body for ``PATCH /settings``.

    Partial update: each key in the body replaces the corresponding
    row's ``value``. Keys not in the body are left unchanged. Pydantic
    ``RootModel`` does not support ``extra=forbid`` (see
    https://errors.pydantic.dev/2.13/u/root-model-extra) so the
    unknown-key check is enforced by :meth:`_check_known_keys` and
    called by the route after Pydantic validation (the same pattern
    is used elsewhere in the project).
    """

    @model_validator(mode="after")
    def _check_known_keys(self) -> SettingsPatch:
        unknown = set(self.root) - KNOWN_SETTING_KEYS
        if unknown:
            from pydantic import ValidationError

            # Raise a Pydantic-shaped error so the global handler maps
            # it to a 400 ``validation_failed`` envelope.
            raise ValidationError.from_exception_data(
                title=type(self).__name__,
                line_errors=[
                    {
                        "type": "extra_forbidden",
                        "loc": ("body", k),
                        "input": k,
                        "ctx": {"error": f"Unknown setting key: {k!r}"},
                    }
                    for k in sorted(unknown)
                ],
            )
        return self
