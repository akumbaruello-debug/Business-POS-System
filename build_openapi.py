#!/usr/bin/env python3
"""Build openapi.yaml for the Business POS System.

Sources of truth (read-only, in priority order):
  - API-Architecture-V1.0.md (AUTHORITATIVE for endpoint shape)
  - Database-Design-V1.0.md (authoritative for field semantics)
  - V1.9-Accounting-Event-Matrix.md (authoritative for accounting events)
  - Business-Rules.md and Business-POS-System-PRD-V1.1.md (behavioral rules)

Output: Business-POS-System/openapi.yaml
"""

import yaml
from collections import OrderedDict


# Custom representer so PyYAML can dump OrderedDict (preserves key order).
def _ordered_dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


yaml.SafeDumper.add_representer(OrderedDict, _ordered_dict_representer)
yaml.SafeDumper.ignore_aliases = lambda *args: True


# ============================================================
# OPERATION HELPERS — every operation is correctly wrapped in a
# {method: op_object} path-item entry. No more direct `p["/x"] = verb_op(...)`.
# ============================================================
def _base_op(op_id, summary, description, tags, security):
    op = OrderedDict()
    op["operationId"] = op_id
    op["summary"] = summary
    op["description"] = description
    op["tags"] = tags
    if security is not None:
        op["security"] = security
    return op


def _attach_idempotency_if_match(op, idempotency_required, if_match_required):
    extra = []
    if idempotency_required is True:
        extra.append({"$ref": "#/components/parameters/IdempotencyKeyRequired"})
    if if_match_required is True:
        extra.append({"$ref": "#/components/parameters/IfMatchRequired"})
    if not extra:
        return
    existing = op.get("parameters", [])
    op["parameters"] = existing + extra


def get_op(op_id, summary, response_schema, tags, capability=None, parameters=None,
           security=None, response_codes=None, x_ratelimit=None):
    op = _base_op(op_id, summary, summary, tags, security if security is not None else [{"bearerAuth": []}])
    if capability:
        op["x-required-capability"] = capability
    if parameters:
        op["parameters"] = list(parameters)
    responses = {
        "200": {
            "description": "OK",
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{response_schema}"}}},
        }
    }
    if response_codes:
        for code, (desc, schema_ref, error_codes) in response_codes.items():
            responses[str(code)] = {
                "description": desc,
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_ref}"}}},
                "x-error-codes": error_codes,
            }
    else:
        responses["401"] = {"$ref": "#/components/responses/Unauthorized"}
        responses["403"] = {"$ref": "#/components/responses/Forbidden"}
    op["responses"] = responses
    if x_ratelimit:
        op["x-rate-limit"] = x_ratelimit
    return op


def post_op(op_id, summary, tags, capability=None, capability_dual=None, has_path_id=False,
            request_schema=None, responses_200=None, responses_201=None, responses_204=None,
            idempotency_required=False, if_match_required=False, response_codes=None,
            security=None, x_ratelimit=None):
    op = _base_op(op_id, summary, summary, tags,
                  security if security is not None else [{"bearerAuth": []}])
    if capability:
        op["x-required-capability"] = capability
    elif capability_dual:
        op["x-required-capability"] = {"oneOf": list(capability_dual)}
    if has_path_id:
        op["parameters"] = [path_id_("id", "Resource id", "int64")]
    _attach_idempotency_if_match(op, idempotency_required, if_match_required)
    if request_schema:
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{request_schema}"}}},
        }
    responses = {}
    if responses_200:
        responses["200"] = {
            "description": "OK",
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{responses_200}"}}},
        }
    if responses_201:
        responses["201"] = {
            "description": "Created",
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{responses_201}"}}},
        }
    if responses_204:
        responses["204"] = {"description": "No Content"}
    if response_codes:
        for code, (desc, schema_ref, error_codes) in response_codes.items():
            responses[str(code)] = {
                "description": desc,
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_ref}"}}},
                "x-error-codes": error_codes,
            }
    else:
        responses["400"] = {"$ref": "#/components/responses/BadRequest"}
        responses["401"] = {"$ref": "#/components/responses/Unauthorized"}
        responses["403"] = {"$ref": "#/components/responses/Forbidden"}
    if idempotency_required is True:
        responses["400"] = responses.get("400", {"$ref": "#/components/responses/BadRequest"})
        responses.setdefault("409", {"$ref": "#/components/responses/Conflict"})
    op["responses"] = responses
    if x_ratelimit:
        op["x-rate-limit"] = x_ratelimit
    return op


def patch_op(op_id, summary, tags, capability=None, request_schema=None, if_match_required=False,
             response_codes=None, security=None, idempotency_required=False, responses_200=None):
    op = _base_op(op_id, summary, summary, tags, security if security is not None else [{"bearerAuth": []}])
    if capability:
        op["x-required-capability"] = capability
    _attach_idempotency_if_match(op, idempotency_required, if_match_required)
    if request_schema:
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{request_schema}"}}},
        }
    responses = {}
    if responses_200:
        responses["200"] = {
            "description": "OK",
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{responses_200}"}}},
        }
    else:
        responses["200"] = {
            "description": "OK",
            "content": {"application/json": {"schema": {"type": "object"}}},
        }
    if response_codes:
        for code, (desc, schema_ref, error_codes) in response_codes.items():
            responses[str(code)] = {
                "description": desc,
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_ref}"}}},
                "x-error-codes": error_codes,
            }
    else:
        responses["400"] = {"$ref": "#/components/responses/BadRequest"}
        responses["401"] = {"$ref": "#/components/responses/Unauthorized"}
        responses["403"] = {"$ref": "#/components/responses/Forbidden"}
    op["responses"] = responses
    return op


def put_op(op_id, summary, tags, capability=None, request_schema=None, responses_200=None,
           idempotency_required=False, response_codes=None, security=None):
    op = _base_op(op_id, summary, summary, tags, security if security is not None else [{"bearerAuth": []}])
    if capability:
        op["x-required-capability"] = capability
    _attach_idempotency_if_match(op, idempotency_required, False)
    if request_schema:
        op["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{request_schema}"}}},
        }
    responses = {}
    if responses_200:
        responses["200"] = {
            "description": "OK",
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{responses_200}"}}},
        }
    if response_codes:
        for code, (desc, schema_ref, error_codes) in response_codes.items():
            responses[str(code)] = {
                "description": desc,
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_ref}"}}},
                "x-error-codes": error_codes,
            }
    else:
        responses["400"] = {"$ref": "#/components/responses/BadRequest"}
        responses["401"] = {"$ref": "#/components/responses/Unauthorized"}
        responses["403"] = {"$ref": "#/components/responses/Forbidden"}
    op["responses"] = responses
    return op


def delete_op(op_id, summary, tags, capability=None, idempotency_required=False, response_codes=None,
              security=None):
    op = _base_op(op_id, summary, summary, tags, security if security is not None else [{"bearerAuth": []}])
    if capability:
        op["x-required-capability"] = capability
    _attach_idempotency_if_match(op, idempotency_required, False)
    responses = {"204": {"description": "No Content"}}
    if response_codes:
        for code, (desc, schema_ref, error_codes) in response_codes.items():
            responses[str(code)] = {
                "description": desc,
                "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_ref}"}}},
                "x-error-codes": error_codes,
            }
    else:
        responses["400"] = {"$ref": "#/components/responses/BadRequest"}
        responses["401"] = {"$ref": "#/components/responses/Unauthorized"}
        responses["403"] = {"$ref": "#/components/responses/Forbidden"}
    responses["404"] = {"$ref": "#/components/responses/NotFound"}
    op["responses"] = responses
    return op


def collection_get_op(op_id, summary, item_schema, tags, capability, filterable_extra=None,
                      extra_query_params=None, sortable=None):
    op = _base_op(op_id, summary, summary, tags, [{"bearerAuth": []}])
    if capability:
        op["x-required-capability"] = capability
    # Build parameters: pagination, sort, q, from/to, plus extra filters.
    params = [
        {"$ref": "#/components/parameters/Page"},
        {"$ref": "#/components/parameters/PerPage"},
        {"$ref": "#/components/parameters/Sort"},
        {"$ref": "#/components/parameters/Q"},
        {"$ref": "#/components/parameters/From"},
        {"$ref": "#/components/parameters/To"},
    ]
    if extra_query_params:
        for q in extra_query_params.values():
            params.append(q)
    if filterable_extra:
        for name, spec in filterable_extra.items():
            params.append({"name": name, "in": "query", "required": False, **spec})
    op["parameters"] = params
    responses = {
        "200": {
            "description": "OK",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["data", "pagination"],
                        "properties": {
                            "data": {"type": "array", "items": {"$ref": f"#/components/schemas/{item_schema}"}},
                            "pagination": {"$ref": "#/components/schemas/Pagination"},
                            "links": {"$ref": "#/components/schemas/Links"},
                        },
                    },
                },
            },
        },
        "401": {"$ref": "#/components/responses/Unauthorized"},
        "403": {"$ref": "#/components/responses/Forbidden"},
    }
    op["responses"] = responses
    return op


def path_id_(name, desc, format_="int64"):
    return {
        "name": name,
        "in": "path",
        "required": True,
        "description": desc,
        "schema": {"type": "integer", "format": format_},
    }


# Alias for shorter calls
path_id = path_id_


# ============================================================
# SCHEMAS (full — see component section below)
# ============================================================
def build_schemas():
    # Embed the entire schema table here.  This is a single source of truth.
    s = OrderedDict()

    # -------- Error envelope (canonical) --------
    s["ErrorEnvelope"] = {
        "type": "object",
        "required": ["error"],
        "description": "Canonical error envelope. Used for all 4xx/5xx responses.",
        "properties": {
            "error": {
                "type": "object",
                "required": ["code", "message", "request_id"],
                "properties": {
                    "code": {"type": "string", "description": "Stable, machine-readable snake_case code.", "example": "not_found"},
                    "message": {"type": "string", "description": "Human-readable English."},
                    "details": {"description": "Optional, code-specific structure.", "oneOf": [
                        {"type": "object", "additionalProperties": True},
                        {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                        {"type": "null"},
                    ]},
                    "request_id": {"type": "string", "format": "uuid", "description": "Echoes `X-Request-ID` header."},
                },
            },
        },
    }

    s["ValidationErrorEnvelope"] = {
        "allOf": [
            {"$ref": "#/components/schemas/ErrorEnvelope"},
            {
                "type": "object",
                "description": "Returned for `validation_failed` (400).",
                "properties": {
                    "error": {
                        "type": "object",
                        "properties": {
                            "code": {"const": "validation_failed"},
                            "details": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["field", "code", "message"],
                                    "properties": {
                                        "field": {"type": "string"},
                                        "code": {"type": "string"},
                                        "message": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        ],
    }

    s["AuthorizationErrorEnvelope"] = {
        "allOf": [
            {"$ref": "#/components/schemas/ErrorEnvelope"},
            {
                "type": "object",
                "description": "Returned for `capability_required` (403).",
                "properties": {
                    "error": {
                        "type": "object",
                        "properties": {
                            "code": {"const": "capability_required"},
                            "details": {
                                "type": "object",
                                "properties": {
                                    "required": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "example": ["sale.cancel"],
                                    },
                                },
                            },
                        },
                    },
                },
            },
        ],
    }

    s["ConflictErrorEnvelope"] = {
        "allOf": [
            {"$ref": "#/components/schemas/ErrorEnvelope"},
            {
                "type": "object",
                "description": "Returned for state conflicts (409).",
                "properties": {
                    "error": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "enum": [
                                    "insufficient_stock", "negative_stock_disallowed", "payment_exceeds_total",
                                    "allocation_exceeds_payable", "refund_exceeds_crl", "repayment_exceeds_srec",
                                    "cash_insufficient", "return_quantity_exceeds_original",
                                    "purchase_return_quantity_exceeds_original", "lifecycle_state_invalid",
                                    "already_cancelled", "cannot_cancel_after_return",
                                    "production_inputs_exceed_stock", "production_finished_cost_invalid",
                                    "cost_type_deactivated", "product_deactivated", "concurrent_modification",
                                    "serialization_failure", "idempotency_violation", "delete_not_permitted",
                                    "referenced_by_history", "version_mismatch",
                                ],
                            },
                            "details": {"type": "object", "additionalProperties": True},
                        },
                    },
                },
            },
        ],
    }

    s["Pagination"] = {
        "type": "object",
        "required": ["page", "per_page", "total", "total_pages"],
        "properties": {
            "page": {"type": "integer", "minimum": 1, "example": 1},
            "per_page": {"type": "integer", "minimum": 1, "maximum": 500, "example": 50},
            "total": {"type": "integer", "minimum": 0, "example": 1234},
            "total_pages": {"type": "integer", "minimum": 0, "example": 25},
        },
    }
    s["Links"] = {
        "type": "object",
        "properties": {
            "self": {"type": "string", "format": "uri-reference"},
            "next": {"type": ["string", "null"], "format": "uri-reference"},
            "prev": {"type": ["string", "null"], "format": "uri-reference"},
        },
    }

    # -------- Auth & session --------
    s["LoginRequest"] = {"type": "object", "required": ["username", "password"], "properties": {
        "username": {"type": "string", "minLength": 1, "maxLength": 64},
        "password": {"type": "string", "minLength": 1, "maxLength": 256},
    }}
    s["LoginResponse"] = {"type": "object", "required": ["access_token", "refresh_token", "expires_in", "refresh_expires_in", "user"], "properties": {
        "access_token": {"type": "string"},
        "refresh_token": {"type": "string"},
        "expires_in": {"type": "integer"},
        "refresh_expires_in": {"type": "integer"},
        "user": {"$ref": "#/components/schemas/SessionUser"},
    }}
    s["RefreshRequest"] = {"type": "object", "required": ["refresh_token"], "properties": {"refresh_token": {"type": "string"}}}
    s["SessionUser"] = {"type": "object", "required": ["id", "username", "full_name", "role_id", "role_name", "capabilities"], "properties": {
        "id": {"type": "integer", "minimum": 1},
        "username": {"type": "string"},
        "full_name": {"type": "string"},
        "email": {"type": ["string", "null"], "format": "email"},
        "is_active": {"type": "boolean"},
        "role_id": {"type": "integer", "minimum": 1},
        "role_name": {"type": "string"},
        "capabilities": {"type": "array", "items": {"type": "string"}},
        "last_login_at": {"type": ["string", "null"], "format": "date-time"},
    }}

    # -------- Identity --------
    s["Role"] = {"type": "object", "required": ["id", "name", "is_system_role", "is_active", "created_at", "updated_at", "version"], "properties": {
        "id": {"type": "integer", "minimum": 1},
        "name": {"type": "string", "minLength": 1, "maxLength": 50},
        "description": {"type": ["string", "null"]},
        "is_system_role": {"type": "boolean"},
        "is_active": {"type": "boolean"},
        "capability_codes": {"type": "array", "items": {"type": "string"}},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "version": {"type": "integer"},
    }}
    s["RoleCreateRequest"] = {"type": "object", "required": ["name"], "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "^[A-Za-z0-9_ \\-]+$"},
        "description": {"type": "string", "maxLength": 500},
    }}
    s["RoleUpdateRequest"] = {"type": "object", "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 50},
        "description": {"type": "string", "maxLength": 500},
    }}
    s["RoleCapabilitiesRequest"] = {"type": "object", "required": ["capability_codes"], "properties": {
        "capability_codes": {"type": "array", "items": {"type": "string"}},
    }}
    s["Capability"] = {"type": "object", "required": ["id", "code", "domain", "default_owner", "default_staff"], "properties": {
        "id": {"type": "integer", "minimum": 1},
        "code": {"type": "string"},
        "domain": {"type": "string"},
        "description": {"type": ["string", "null"]},
        "default_owner": {"type": "boolean"},
        "default_staff": {"type": "boolean"},
    }}
    s["User"] = {"type": "object", "required": ["id", "username", "full_name", "is_active", "role_id", "role_name", "created_at", "updated_at", "version"], "properties": {
        "id": {"type": "integer", "minimum": 1},
        "username": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[a-z0-9._-]+$"},
        "full_name": {"type": "string", "minLength": 1, "maxLength": 150},
        "email": {"type": ["string", "null"], "format": "email", "maxLength": 150},
        "is_active": {"type": "boolean"},
        "role_id": {"type": "integer", "minimum": 1},
        "role_name": {"type": "string"},
        "last_login_at": {"type": ["string", "null"], "format": "date-time"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "version": {"type": "integer"},
    }}
    s["UserCreateRequest"] = {"type": "object", "required": ["username", "full_name", "password", "role_id"], "properties": {
        "username": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[a-z0-9._-]+$"},
        "full_name": {"type": "string", "minLength": 1, "maxLength": 150},
        "email": {"type": "string", "format": "email", "maxLength": 150},
        "password": {"type": "string", "minLength": 8, "maxLength": 256},
        "role_id": {"type": "integer", "minimum": 1},
    }}
    s["UserUpdateRequest"] = {"type": "object", "properties": {
        "full_name": {"type": "string", "minLength": 1, "maxLength": 150},
        "email": {"type": "string", "format": "email", "maxLength": 150},
        "is_active": {"type": "boolean"},
        "role_id": {"type": "integer", "minimum": 1},
    }}
    s["PasswordResetRequest"] = {"type": "object", "required": ["new_password"], "properties": {
        "new_password": {"type": "string", "minLength": 8, "maxLength": 256},
    }}
    s["UserCapabilityOverrideRequest"] = {"type": "object", "required": ["capability_code", "is_granted"], "properties": {
        "capability_code": {"type": "string"},
        "is_granted": {"type": "boolean"},
    }}
    s["UserEffectiveCapabilities"] = {"type": "object", "required": ["role", "role_capabilities", "overrides", "effective"], "properties": {
        "role": {"type": "string"},
        "role_capabilities": {"type": "array", "items": {"type": "string"}},
        "overrides": {"type": "array", "items": {"type": "object", "properties": {
            "capability_code": {"type": "string"},
            "is_granted": {"type": "boolean"},
            "granted_at": {"type": "string", "format": "date-time"},
            "granted_by": {"type": "integer"},
        }}},
        "effective": {"type": "array", "items": {"type": "string"}},
    }}

    # -------- Audit --------
    s["AuditEntry"] = {"type": "object", "required": ["id", "event_time", "action", "entity_type"], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "event_time": {"type": "string", "format": "date-time"},
        "user_id": {"type": ["integer", "null"]},
        "action": {"type": "string", "enum": [
            "create", "update", "post", "cancel", "complete", "return", "adjust", "deactivate",
            "permission_grant", "permission_revoke", "settings_change", "price_override",
            "refund", "payment", "supplier_repayment", "manual_entry",
        ]},
        "entity_type": {"type": "string", "enum": [
            "user", "role", "product", "category", "unit", "contact", "payment_method", "cost_type",
            "financial_category", "sale", "sale_line", "sale_payment", "sales_return", "sales_return_line",
            "refund", "purchase", "purchase_line", "purchase_payment", "purchase_return", "purchase_return_line",
            "purchase_shipping", "supplier_repayment", "stock_movement", "production_run",
            "production_input", "production_output", "production_cost_line", "manual_finance_entry",
            "system_settings", "capability_override",
        ]},
        "entity_id": {"type": ["integer", "null"], "format": "int64"},
        "old_values": {"type": ["object", "null"], "additionalProperties": True},
        "new_values": {"type": ["object", "null"], "additionalProperties": True},
        "reason": {"type": ["string", "null"]},
        "ip_address": {"type": ["string", "null"]},
    }}

    # -------- Master data --------
    for prefix, schema_name, req_name, patch_name, min_name, max_name, pat, in [
        ("Category", "Category", "CategoryRequest", "CategoryPatch", 1, 100, None),
        ("Unit", "Unit", "UnitRequest", "UnitPatch", 1, 50, "^[A-Za-z0-9_-]+$"),
        ("PaymentMethod", "PaymentMethod", "PaymentMethodRequest", "PaymentMethodPatch", 1, 100, "^[a-z0-9_]+$"),
        ("CostType", "CostType", "CostTypeRequest", "CostTypePatch", 1, 100, "^[a-z0-9_]+$"),
        ("FinancialCategory", "FinancialCategory", "FinancialCategoryRequest", "FinancialCategoryPatch", 1, 100, "^[a-z0-9_]+$"),
    ]:
        s[schema_name] = {"type": "object", "required": ["id", "name", "is_active", "version"], "properties": {
            "id": {"type": "integer", "minimum": 1},
            "name": {"type": "string", "minLength": min_name, "maxLength": max_name},
            "is_active": {"type": "boolean"},
            "version": {"type": "integer"},
        }}
        if prefix in ("Category",):
            s[schema_name]["properties"].update({"parent_id": {"type": ["integer", "null"], "minimum": 1}})
            s[schema_name]["required"] = ["id", "name", "is_active", "created_at", "updated_at", "version"]
            s[schema_name]["properties"].update({"created_at": {"type": "string", "format": "date-time"}, "updated_at": {"type": "string", "format": "date-time"}})
        if prefix == "PaymentMethod":
            s[schema_name]["required"] = ["id", "code", "name", "is_cash", "is_active", "version"]
            s[schema_name]["properties"].update({"code": {"type": "string", "enum": ["cash", "bank_transfer", "e_wallet", "other"]}, "is_cash": {"type": "boolean"}})
        if prefix in ("Unit", "CostType", "FinancialCategory"):
            s[schema_name]["required"] = ["id", "code", "name", "is_active", "version"] if prefix != "FinancialCategory" else ["id", "code", "name", "entry_type", "is_active", "version"]
            s[schema_name]["properties"].update({"code": {"type": "string", "minLength": 1, "maxLength": 50 if prefix != "Unit" else 20, "pattern": pat}})
        if prefix == "FinancialCategory":
            s[schema_name]["properties"].update({"entry_type": {"type": "string", "enum": ["income", "expense"]}})

        s[req_name] = {"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string", "minLength": min_name, "maxLength": max_name},
            "is_active": {"type": "boolean", "default": True},
        }}
        if prefix == "Category":
            s[req_name]["properties"].update({"parent_id": {"type": ["integer", "null"], "minimum": 1}})
        if prefix in ("Unit", "CostType", "FinancialCategory"):
            s[req_name]["required"] = ["code", "name"]
            s[req_name]["properties"]["code"] = {"type": "string", "minLength": 1, "maxLength": 50 if prefix != "Unit" else 20, "pattern": pat}
        if prefix == "PaymentMethod":
            s[req_name]["required"] = ["code", "name", "is_cash"]
            s[req_name]["properties"]["code"] = {"type": "string", "minLength": 1, "maxLength": 50, "pattern": "^[a-z0-9_]+$"}
            s[req_name]["properties"]["is_cash"] = {"type": "boolean"}
        if prefix == "FinancialCategory":
            s[req_name]["required"] = ["code", "name", "entry_type"]
            s[req_name]["properties"]["entry_type"] = {"type": "string", "enum": ["income", "expense"]}

        s[patch_name] = {"type": "object", "properties": {
            "name": {"type": "string", "minLength": min_name, "maxLength": max_name},
            "is_active": {"type": "boolean"},
        }}
        if prefix == "Category":
            s[patch_name]["properties"].update({"parent_id": {"type": ["integer", "null"], "minimum": 1}})

    # -------- Products --------
    s["Product"] = {"type": "object", "required": [
        "id", "name", "purchase_price", "selling_price", "is_sellable", "is_purchasable",
        "is_producible", "is_active", "on_hand_quantity", "moving_average_unit_cost",
        "inventory_value", "low_stock", "created_at", "updated_at", "version",
    ], "properties": {
        "id": {"type": "integer", "minimum": 1},
        "code": {"type": ["string", "null"], "maxLength": 64, "pattern": "^[A-Za-z0-9._-]+$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "category_id": {"type": ["integer", "null"], "minimum": 1},
        "unit_id": {"type": ["integer", "null"], "minimum": 1},
        "purchase_price": {"type": "number", "minimum": 0, "multipleOf": 0.01},
        "selling_price": {"type": "number", "minimum": 0, "multipleOf": 0.01},
        "low_stock_threshold": {"type": ["number", "null"], "minimum": 0},
        "allow_negative_stock": {"type": ["boolean", "null"]},
        "is_sellable": {"type": "boolean"},
        "is_purchasable": {"type": "boolean"},
        "is_producible": {"type": "boolean"},
        "is_active": {"type": "boolean"},
        "notes": {"type": ["string", "null"], "maxLength": 2000},
        "on_hand_quantity": {"type": "number", "description": "Derived: SUM(stock_movements.quantity)."},
        "moving_average_unit_cost": {"type": "number", "description": "Derived: SUM(positive total_cost) / on_hand_qty."},
        "inventory_value": {"type": "number", "description": "Derived: on_hand × moving_avg."},
        "low_stock": {"type": "boolean", "description": "Computed on read."},
        "negative_stock_fallback_supported": {"type": "boolean"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
        "updated_by": {"type": "integer"},
        "version": {"type": "integer"},
    }}
    s["ProductCreateRequest"] = {"type": "object", "required": ["name"], "properties": {
        "code": {"type": "string", "maxLength": 64, "pattern": "^[A-Za-z0-9._-]+$"},
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "category_id": {"type": ["integer", "null"], "minimum": 1},
        "unit_id": {"type": ["integer", "null"], "minimum": 1},
        "purchase_price": {"type": "number", "minimum": 0, "default": 0},
        "selling_price": {"type": "number", "minimum": 0, "default": 0},
        "low_stock_threshold": {"type": ["number", "null"], "minimum": 0},
        "allow_negative_stock": {"type": ["boolean", "null"]},
        "is_sellable": {"type": "boolean", "default": True},
        "is_purchasable": {"type": "boolean", "default": True},
        "is_producible": {"type": "boolean", "default": False},
        "is_active": {"type": "boolean", "default": True},
        "notes": {"type": "string", "maxLength": 2000},
    }}
    s["ProductPatch"] = {"type": "object", "description": "PATCH on a product master. `code` is immutable. Price changes insert a price_history row; historical transactions are unchanged.", "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "category_id": {"type": ["integer", "null"], "minimum": 1},
        "unit_id": {"type": ["integer", "null"], "minimum": 1},
        "purchase_price": {"type": "number", "minimum": 0},
        "selling_price": {"type": "number", "minimum": 0},
        "low_stock_threshold": {"type": ["number", "null"], "minimum": 0},
        "allow_negative_stock": {"type": ["boolean", "null"]},
        "is_sellable": {"type": "boolean"},
        "is_purchasable": {"type": "boolean"},
        "is_producible": {"type": "boolean"},
        "is_active": {"type": "boolean"},
        "notes": {"type": "string", "maxLength": 2000},
    }}
    s["ProductPriceHistory"] = {"type": "object", "required": ["id", "product_id", "purchase_price", "selling_price", "effective_at", "changed_by"], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "product_id": {"type": "integer"},
        "purchase_price": {"type": "number"},
        "selling_price": {"type": "number"},
        "effective_at": {"type": "string", "format": "date-time"},
        "changed_by": {"type": "integer"},
    }}
    s["ProductValuation"] = {"type": "object", "required": ["product_id", "on_hand_quantity", "inventory_value", "as_of"], "properties": {
        "product_id": {"type": "integer"},
        "on_hand_quantity": {"type": "number"},
        "moving_average_unit_cost": {"type": ["number", "null"]},
        "inventory_value": {"type": "number"},
        "as_of": {"type": "string", "format": "date-time"},
    }}
    s["ProductStock"] = {"type": "object", "required": ["product_id", "on_hand_quantity", "inventory_value", "as_of"], "properties": {
        "product_id": {"type": "integer"},
        "on_hand_quantity": {"type": "number"},
        "moving_average_unit_cost": {"type": ["number", "null"]},
        "inventory_value": {"type": "number"},
        "low_stock": {"type": "boolean"},
        "low_stock_threshold": {"type": ["number", "null"]},
        "as_of": {"type": "string", "format": "date-time"},
    }}

    # -------- Contacts --------
    s["Contact"] = {"type": "object", "required": ["id", "type", "name", "is_active", "created_at", "updated_at", "version"], "properties": {
        "id": {"type": "integer", "minimum": 1},
        "type": {"type": "string", "enum": ["customer", "supplier", "both"]},
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "phone": {"type": ["string", "null"], "maxLength": 50},
        "email": {"type": ["string", "null"], "format": "email", "maxLength": 150},
        "address": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
        "is_active": {"type": "boolean"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "version": {"type": "integer"},
    }}
    s["ContactRequest"] = {"type": "object", "required": ["type", "name"], "properties": {
        "type": {"type": "string", "enum": ["customer", "supplier", "both"]},
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "phone": {"type": "string", "maxLength": 50},
        "email": {"type": "string", "format": "email", "maxLength": 150},
        "address": {"type": "string"},
        "notes": {"type": "string"},
    }}
    s["ContactPatch"] = {"type": "object", "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "phone": {"type": ["string", "null"], "maxLength": 50},
        "email": {"type": ["string", "null"], "format": "email", "maxLength": 150},
        "address": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
        "is_active": {"type": "boolean"},
    }}

    # -------- Sales --------
    s["Sale"] = {"type": "object", "required": [
        "id", "sale_date", "total_amount", "discount_amount", "lifecycle_status",
        "paid_amount", "outstanding", "payment_state", "ar",
        "created_at", "updated_at", "version",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "reference_no": {"type": ["string", "null"], "maxLength": 50},
        "customer_id": {"type": ["integer", "null"], "minimum": 1},
        "customer": {"$ref": "#/components/schemas/Contact"},
        "sale_date": {"type": "string", "format": "date-time"},
        "total_amount": {"type": "number", "minimum": 0, "description": "Σ line_total − header discount."},
        "discount_amount": {"type": "number", "minimum": 0},
        "lifecycle_status": {"type": "string", "enum": ["draft", "posted", "completed", "partially_returned", "returned", "cancelled"]},
        "payment_state": {"type": "string", "enum": ["unpaid", "partial", "paid"]},
        "paid_amount": {"type": "number"},
        "outstanding": {"type": "number"},
        "ar": {"type": "number"},
        "crl": {"type": "number"},
        "cancellation_date": {"type": ["string", "null"], "format": "date-time"},
        "cancellation_reason": {"type": ["string", "null"]},
        "cancelled_by": {"type": ["integer", "null"]},
        "posted_at": {"type": ["string", "null"], "format": "date-time"},
        "posted_by": {"type": ["integer", "null"]},
        "notes": {"type": ["string", "null"], "maxLength": 2000},
        "lines": {"type": "array", "items": {"$ref": "#/components/schemas/SaleLine"}},
        "payments": {"type": "array", "items": {"$ref": "#/components/schemas/SalePayment"}},
        "returns": {"type": "array", "items": {"$ref": "#/components/schemas/SalesReturn"}},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
        "version": {"type": "integer"},
    }}
    s["SaleLineInput"] = {"type": "object", "required": ["product_id", "quantity"], "properties": {
        "product_id": {"type": "integer", "minimum": 1},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "unit_price": {"type": "number", "minimum": 0, "description": "Defaults to product.selling_price. Override requires `sale.price_override`; logged in audit."},
        "discount_amount": {"type": "number", "minimum": 0, "description": "Non-zero requires `sale.discount`."},
    }}
    s["SaleLine"] = {"type": "object", "required": [
        "id", "sale_id", "product_id", "quantity", "unit_price", "discount_amount", "line_total", "line_number",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "sale_id": {"type": "integer", "format": "int64"},
        "product_id": {"type": "integer"},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "unit_price": {"type": "number", "minimum": 0},
        "discount_amount": {"type": "number", "minimum": 0},
        "line_total": {"type": "number", "minimum": 0, "description": "Server-computed: quantity × unit_price − discount_amount."},
        "unit_cost_snapshot": {"type": ["number", "null"], "description": "Server-computed at post; immutable once posted."},
        "cogs_total_snapshot": {"type": ["number", "null"]},
        "is_negative_stock_fallback": {"type": "boolean"},
        "stock_movement_id": {"type": ["integer", "null"], "format": "int64"},
        "line_number": {"type": "integer", "minimum": 1},
    }}
    s["SaleLinePatch"] = {"type": "object", "properties": {
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "unit_price": {"type": "number", "minimum": 0},
        "discount_amount": {"type": "number", "minimum": 0},
    }}
    s["SaleCreateRequest"] = {"type": "object", "required": ["lines"], "properties": {
        "customer_id": {"type": ["integer", "null"], "minimum": 1, "description": "Optional. Customer is never required."},
        "sale_date": {"type": "string", "format": "date-time"},
        "discount_amount": {"type": "number", "minimum": 0, "default": 0},
        "notes": {"type": "string", "maxLength": 2000},
        "reference_no": {"type": ["string", "null"], "maxLength": 50},
        "lines": {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/SaleLineInput"}},
    }}
    s["SalePatch"] = {"type": "object", "properties": {
        "customer_id": {"type": ["integer", "null"], "minimum": 1},
        "sale_date": {"type": "string", "format": "date-time"},
        "discount_amount": {"type": "number", "minimum": 0},
        "notes": {"type": "string", "maxLength": 2000},
    }}
    s["SalePaymentInput"] = {"type": "object", "required": ["payment_method_id", "amount"], "properties": {
        "payment_method_id": {"type": "integer", "minimum": 1},
        "amount": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e13},
        "tendered_amount": {"type": ["number", "null"], "minimum": 0, "description": "Required only for over-tender cash; allowed only when `payment_method.is_cash` is true."},
        "change_amount": {"type": ["number", "null"], "minimum": 0, "description": "Server-computed."},
        "payment_date": {"type": "string", "format": "date-time"},
        "reference": {"type": "string", "maxLength": 100},
    }}
    s["SalePayment"] = {"type": "object", "required": ["id", "sale_id", "payment_method_id", "amount", "payment_date", "created_at"], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "sale_id": {"type": "integer", "format": "int64"},
        "payment_method_id": {"type": "integer"},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "tendered_amount": {"type": ["number", "null"], "minimum": 0},
        "change_amount": {"type": ["number", "null"], "minimum": 0},
        "payment_date": {"type": "string", "format": "date-time"},
        "reference": {"type": ["string", "null"], "maxLength": 100},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
    }}
    s["SaleCancelRequest"] = {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 1000}}}
    s["SaleReturnLineRequest"] = {"type": "object", "required": ["sale_line_id", "quantity"], "properties": {
        "sale_line_id": {"type": "integer", "format": "int64"},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
    }}
    s["SaleReturnRequest"] = {"type": "object", "required": ["reason", "lines"], "properties": {
        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
        "lines": {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/SaleReturnLineRequest"}},
    }}
    s["SalesReturn"] = {"type": "object", "required": [
        "id", "sale_id", "return_date", "total_selling_price_returned", "total_cost_returned",
        "lifecycle_status", "lines", "created_at", "created_by",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "sale_id": {"type": "integer", "format": "int64"},
        "return_date": {"type": "string", "format": "date-time"},
        "reason": {"type": ["string", "null"]},
        "total_selling_price_returned": {"type": "number", "exclusiveMinimum": 0},
        "total_cost_returned": {"type": "number", "exclusiveMinimum": 0},
        "lifecycle_status": {"type": "string", "enum": ["posted", "cancelled"]},
        "lines": {"type": "array", "items": {"$ref": "#/components/schemas/SalesReturnLine"}},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
    }}
    s["SalesReturnLine"] = {"type": "object", "required": [
        "id", "sales_return_id", "sale_line_id", "product_id", "quantity",
        "returned_selling_price", "returned_unit_cost", "line_number",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "sales_return_id": {"type": "integer", "format": "int64"},
        "sale_line_id": {"type": "integer", "format": "int64"},
        "product_id": {"type": "integer"},
        "quantity": {"type": "number", "exclusiveMinimum": 0},
        "returned_selling_price": {"type": "number", "description": "Server-computed: sale_line.unit_price × quantity."},
        "returned_unit_cost": {"type": "number", "description": "Server-computed from sale_line.unit_cost_snapshot."},
        "line_value": {"type": "number", "description": "= returned_selling_price."},
        "line_number": {"type": "integer", "minimum": 1},
    }}
    s["SalesReturnCancelRequest"] = {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 1000}}}

    # -------- Refunds --------
    s["RefundRequest"] = {"type": "object", "required": ["sale_id", "amount", "payment_method_id"], "properties": {
        "sale_id": {"type": "integer", "format": "int64"},
        "amount": {"type": "number", "exclusiveMinimum": 0, "description": "Must be ≤ current CRL (Σ payments − Σ refunds) and ≤ current cash balance."},
        "payment_method_id": {"type": "integer", "minimum": 1},
        "refund_date": {"type": "string", "format": "date-time"},
        "reason": {"type": "string", "maxLength": 1000},
    }}
    s["Refund"] = {"type": "object", "required": [
        "id", "sale_id", "amount", "payment_method_id", "refund_date",
        "refundable_amount_snapshot", "created_at", "created_by",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "sale_id": {"type": "integer", "format": "int64"},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "payment_method_id": {"type": "integer"},
        "refund_date": {"type": "string", "format": "date-time"},
        "reason": {"type": ["string", "null"]},
        "refundable_amount_snapshot": {"type": "number", "description": "Audit-only."},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
    }}

    # -------- Purchases --------
    s["Purchase"] = {"type": "object", "required": [
        "id", "purchase_date", "total_amount", "lifecycle_status",
        "paid_amount", "outstanding", "payment_state",
        "created_at", "updated_at", "version",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "reference_no": {"type": ["string", "null"], "maxLength": 50},
        "supplier_id": {"type": ["integer", "null"], "minimum": 1, "description": "Optional. Counter/cash buys allowed."},
        "supplier": {"$ref": "#/components/schemas/Contact"},
        "purchase_date": {"type": "string", "format": "date-time"},
        "received_date": {"type": ["string", "null"], "format": "date-time"},
        "total_amount": {"type": "number", "minimum": 0, "description": "Σ line_total + shipping.amount."},
        "lifecycle_status": {"type": "string", "enum": ["draft", "posted", "completed", "partially_returned", "returned", "cancelled"]},
        "payment_state": {"type": "string", "enum": ["unpaid", "partial", "paid"]},
        "paid_amount": {"type": "number"},
        "outstanding": {"type": "number"},
        "ap": {"type": "number"},
        "supplier_receivable": {"type": "number"},
        "posted_at": {"type": ["string", "null"], "format": "date-time"},
        "posted_by": {"type": ["integer", "null"]},
        "cancellation_date": {"type": ["string", "null"], "format": "date-time"},
        "cancellation_reason": {"type": ["string", "null"]},
        "cancelled_by": {"type": ["integer", "null"]},
        "notes": {"type": ["string", "null"], "maxLength": 2000},
        "lines": {"type": "array", "items": {"$ref": "#/components/schemas/PurchaseLine"}},
        "shipping": {"$ref": "#/components/schemas/PurchaseShipping"},
        "payments": {"type": "array", "items": {"$ref": "#/components/schemas/PurchasePayment"}},
        "returns": {"type": "array", "items": {"$ref": "#/components/schemas/PurchaseReturn"}},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
        "version": {"type": "integer"},
    }}
    s["PurchaseLineInput"] = {"type": "object", "required": ["product_id", "quantity", "unit_price"], "properties": {
        "product_id": {"type": "integer", "minimum": 1},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "unit_price": {"type": "number", "minimum": 0},
    }}
    s["PurchaseLine"] = {"type": "object", "required": [
        "id", "purchase_id", "product_id", "quantity", "unit_price",
        "line_subtotal", "allocated_shipping", "line_total", "line_number",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "purchase_id": {"type": "integer", "format": "int64"},
        "product_id": {"type": "integer"},
        "quantity": {"type": "number", "exclusiveMinimum": 0},
        "unit_price": {"type": "number", "minimum": 0},
        "line_subtotal": {"type": "number", "minimum": 0},
        "allocated_shipping": {"type": "number", "minimum": 0},
        "line_total": {"type": "number", "minimum": 0},
        "line_number": {"type": "integer", "minimum": 1},
    }}
    s["PurchaseLinePatch"] = {"type": "object", "properties": {
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "unit_price": {"type": "number", "minimum": 0},
    }}
    s["PurchaseShipping"] = {"type": "object", "required": ["id", "purchase_id", "amount", "paid_in_cash"], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "purchase_id": {"type": "integer", "format": "int64"},
        "amount": {"type": "number", "minimum": 0, "description": "Always capitalized into landed cost."},
        "paid_in_cash": {"type": "boolean", "description": "TRUE → cash_movement at post (Dr Inventory / Cr Cash). FALSE → AP."},
        "supplier_id": {"type": ["integer", "null"]},
        "description": {"type": ["string", "null"]},
    }}
    s["PurchaseShippingRequest"] = {"type": "object", "required": ["amount", "paid_in_cash"], "properties": {
        "amount": {"type": "number", "minimum": 0},
        "paid_in_cash": {"type": "boolean", "default": False},
        "supplier_id": {"type": ["integer", "null"], "minimum": 1},
        "description": {"type": "string", "maxLength": 500},
    }}
    s["PurchaseCreateRequest"] = {"type": "object", "required": ["lines"], "properties": {
        "supplier_id": {"type": ["integer", "null"], "minimum": 1},
        "purchase_date": {"type": "string", "format": "date-time"},
        "notes": {"type": "string", "maxLength": 2000},
        "reference_no": {"type": ["string", "null"], "maxLength": 50},
        "lines": {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/PurchaseLineInput"}},
        "shipping": {"$ref": "#/components/schemas/PurchaseShippingRequest"},
    }}
    s["PurchasePatch"] = {"type": "object", "properties": {
        "supplier_id": {"type": ["integer", "null"], "minimum": 1},
        "purchase_date": {"type": "string", "format": "date-time"},
        "notes": {"type": "string", "maxLength": 2000},
    }}
    s["PurchasePaymentInput"] = {"allOf": [
        {"$ref": "#/components/schemas/SalePaymentInput"},
        {"type": "object", "description": "Same shape as sale payment. Over-tender only for cash methods."},
    ]}
    s["PurchasePayment"] = {"type": "object", "required": ["id", "purchase_id", "payment_method_id", "amount", "payment_date", "created_at"], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "purchase_id": {"type": "integer", "format": "int64"},
        "payment_method_id": {"type": "integer"},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "tendered_amount": {"type": ["number", "null"], "minimum": 0},
        "change_amount": {"type": ["number", "null"], "minimum": 0},
        "payment_date": {"type": "string", "format": "date-time"},
        "reference": {"type": ["string", "null"], "maxLength": 100},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
    }}
    s["PurchaseCancelRequest"] = {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 1000}}}
    s["PurchaseReturnLineRequest"] = {"type": "object", "required": ["purchase_line_id", "quantity"], "properties": {
        "purchase_line_id": {"type": "integer", "format": "int64"},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
    }}
    s["PurchaseReturnRequest"] = {"type": "object", "required": ["reason", "lines"], "properties": {
        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
        "lines": {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/PurchaseReturnLineRequest"}},
    }}
    s["PurchaseReturn"] = {"type": "object", "required": [
        "id", "purchase_id", "return_date", "total_value_returned", "lifecycle_status",
        "lines", "created_at", "created_by",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "purchase_id": {"type": "integer", "format": "int64"},
        "return_date": {"type": "string", "format": "date-time"},
        "reason": {"type": ["string", "null"]},
        "total_value_returned": {"type": "number", "exclusiveMinimum": 0},
        "lifecycle_status": {"type": "string", "enum": ["posted", "cancelled"]},
        "lines": {"type": "array", "items": {"$ref": "#/components/schemas/PurchaseReturnLine"}},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
    }}
    s["PurchaseReturnLine"] = {"type": "object", "required": [
        "id", "purchase_return_id", "purchase_line_id", "product_id", "quantity",
        "unit_cost_snapshot", "line_value", "line_number",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "purchase_return_id": {"type": "integer", "format": "int64"},
        "purchase_line_id": {"type": "integer", "format": "int64"},
        "product_id": {"type": "integer"},
        "quantity": {"type": "number", "exclusiveMinimum": 0},
        "unit_cost_snapshot": {"type": "number"},
        "line_value": {"type": "number"},
        "line_number": {"type": "integer", "minimum": 1},
    }}
    s["PurchaseReturnCancelRequest"] = {"$ref": "#/components/schemas/SalesReturnCancelRequest"}

    s["SupplierRepaymentRequest"] = {"type": "object", "required": ["purchase_id", "amount", "payment_method_id"], "properties": {
        "purchase_id": {"type": "integer", "format": "int64"},
        "amount": {"type": "number", "exclusiveMinimum": 0, "description": "≤ outstanding and ≤ current cash balance."},
        "payment_method_id": {"type": "integer", "minimum": 1},
        "repayment_date": {"type": "string", "format": "date-time"},
        "reason": {"type": "string", "maxLength": 1000},
    }}
    s["SupplierRepayment"] = {"type": "object", "required": [
        "id", "purchase_id", "amount", "payment_method_id", "repayment_date",
        "refundable_amount_snapshot", "created_at", "created_by",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "purchase_id": {"type": "integer", "format": "int64"},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "payment_method_id": {"type": "integer"},
        "repayment_date": {"type": "string", "format": "date-time"},
        "reason": {"type": ["string", "null"]},
        "refundable_amount_snapshot": {"type": "number"},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
    }}

    # -------- Inventory --------
    s["StockMovement"] = {"type": "object", "required": [
        "id", "product_id", "movement_date", "trigger", "quantity", "created_at", "created_by",
    ], "description": "Immutable. Inserted only by server-side business actions.",
     "properties": {
         "id": {"type": "integer", "format": "int64"},
         "product_id": {"type": "integer"},
         "movement_date": {"type": "string", "format": "date-time"},
         "trigger": {"type": "string", "enum": [
             "purchase_receipt", "sale", "sales_return", "purchase_return",
             "production_input", "production_output", "stock_adjustment", "opening_balance",
             "sale_reversal", "purchase_reversal", "sales_return_reversal",
             "purchase_return_reversal", "production_input_reversal", "production_output_reversal",
             "adjustment_reversal", "value_adjustment",
         ]},
         "quantity": {"type": "number", "description": "0 only for `value_adjustment`."},
         "unit_cost_at_movement": {"type": ["number", "null"], "minimum": 0},
         "total_cost": {"type": ["number", "null"]},
         "reference_type": {"type": ["string", "null"], "enum": [
             "sale", "purchase", "production_run", "sales_return", "purchase_return", "stock_adjustment", "manual",
         ]},
         "reference_id": {"type": ["integer", "null"], "format": "int64"},
         "reference_line_id": {"type": ["integer", "null"], "format": "int64"},
         "reversal_of_movement_id": {"type": ["integer", "null"], "format": "int64"},
         "reversed_by_movement_id": {"type": ["integer", "null"], "format": "int64"},
         "reason": {"type": ["string", "null"]},
         "created_at": {"type": "string", "format": "date-time"},
         "created_by": {"type": "integer"},
     }}
    s["StockAdjustmentRequest"] = {"type": "object", "required": ["product_id", "quantity", "reason"], "properties": {
        "product_id": {"type": "integer", "minimum": 1},
        "quantity": {"type": "number", "exclusiveMinimum": -1e12, "maximum": 1e12, "description": "Signed delta; ≠ 0."},
        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
    }}
    s["InventorySummary"] = {"type": "object", "required": ["product_id", "on_hand_quantity", "inventory_value", "as_of"], "properties": {
        "product_id": {"type": "integer"},
        "on_hand_quantity": {"type": "number"},
        "moving_average_unit_cost": {"type": ["number", "null"]},
        "inventory_value": {"type": "number"},
        "low_stock": {"type": "boolean"},
        "as_of": {"type": "string", "format": "date-time"},
    }}

    # -------- Production --------
    s["ProductionRun"] = {"type": "object", "required": [
        "id", "run_date", "output_product_id", "output_quantity",
        "finished_unit_cost", "total_raw_cost", "total_overhead_cost",
        "lifecycle_status", "inputs", "output", "cost_lines",
        "created_at", "updated_at", "version",
    ], "description": "Single-step raw → finished. finished_unit_cost = (total_raw_cost + total_overhead_cost) / output_quantity. Overhead is capitalized into finished goods; cash_movements for overhead are NOT reversed on cancel.",
     "properties": {
         "id": {"type": "integer", "format": "int64"},
         "run_date": {"type": "string", "format": "date-time"},
         "output_product_id": {"type": "integer"},
         "output_quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
         "finished_unit_cost": {"type": "number", "exclusiveMinimum": 0},
         "total_raw_cost": {"type": "number", "minimum": 0},
         "total_overhead_cost": {"type": "number", "minimum": 0},
         "lifecycle_status": {"type": "string", "enum": ["draft", "posted", "completed", "cancelled"]},
         "posted_at": {"type": ["string", "null"], "format": "date-time"},
         "posted_by": {"type": ["integer", "null"]},
         "cancellation_date": {"type": ["string", "null"], "format": "date-time"},
         "cancellation_reason": {"type": ["string", "null"]},
         "cancelled_by": {"type": ["integer", "null"]},
         "notes": {"type": ["string", "null"], "maxLength": 2000},
         "inputs": {"type": "array", "items": {"$ref": "#/components/schemas/ProductionInput"}},
         "output": {"$ref": "#/components/schemas/ProductionOutput"},
         "cost_lines": {"type": "array", "items": {"$ref": "#/components/schemas/ProductionCostLine"}},
         "created_at": {"type": "string", "format": "date-time"},
         "updated_at": {"type": "string", "format": "date-time"},
         "created_by": {"type": "integer"},
         "version": {"type": "integer"},
     }}
    s["ProductionInput"] = {"type": "object", "required": [
        "id", "production_run_id", "product_id", "quantity", "unit_cost_snapshot", "line_cost", "line_number",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "production_run_id": {"type": "integer", "format": "int64"},
        "product_id": {"type": "integer"},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "unit_cost_snapshot": {"type": "number", "minimum": 0},
        "line_cost": {"type": "number", "minimum": 0},
        "line_number": {"type": "integer", "minimum": 1},
    }}
    s["ProductionInputRequest"] = {"type": "object", "required": ["product_id", "quantity"], "properties": {
        "product_id": {"type": "integer", "minimum": 1},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
    }}
    s["ProductionOutput"] = {"type": "object", "required": [
        "id", "production_run_id", "product_id", "quantity", "unit_cost_snapshot", "total_cost",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "production_run_id": {"type": "integer", "format": "int64"},
        "product_id": {"type": "integer"},
        "quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "unit_cost_snapshot": {"type": "number", "minimum": 0, "description": "= parent.finished_unit_cost."},
        "total_cost": {"type": "number", "minimum": 0},
    }}
    s["ProductionCostLine"] = {"type": "object", "required": [
        "id", "production_run_id", "cost_type_id", "amount", "paid_in_cash", "line_number",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "production_run_id": {"type": "integer", "format": "int64"},
        "cost_type_id": {"type": "integer"},
        "description": {"type": ["string", "null"]},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "paid_in_cash": {"type": "boolean"},
        "line_number": {"type": "integer", "minimum": 1},
    }}
    s["ProductionCostLineRequest"] = {"type": "object", "required": ["cost_type_id", "amount"], "properties": {
        "cost_type_id": {"type": "integer", "minimum": 1},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "paid_in_cash": {"type": "boolean", "default": True},
        "description": {"type": "string", "maxLength": 500},
    }}
    s["ProductionRunCreateRequest"] = {"type": "object", "required": ["output_product_id", "output_quantity", "inputs"], "properties": {
        "run_date": {"type": "string", "format": "date-time"},
        "output_product_id": {"type": "integer", "minimum": 1},
        "output_quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "notes": {"type": "string", "maxLength": 2000},
        "inputs": {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/ProductionInputRequest"}},
        "cost_lines": {"type": "array", "items": {"$ref": "#/components/schemas/ProductionCostLineRequest"}},
    }}
    s["ProductionRunPatch"] = {"type": "object", "properties": {
        "run_date": {"type": "string", "format": "date-time"},
        "output_product_id": {"type": "integer", "minimum": 1},
        "output_quantity": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e12},
        "notes": {"type": "string", "maxLength": 2000},
    }}
    s["ProductionCancelRequest"] = {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 1000}}}

    # -------- Manual entries --------
    s["ManualEntryRequest"] = {"type": "object", "required": ["category_id", "entry_type", "amount", "payment_method_id"], "properties": {
        "category_id": {"type": "integer", "minimum": 1, "description": "Must match `entry_type`. Category name may not duplicate derived categories."},
        "entry_type": {"type": "string", "enum": ["income", "expense"]},
        "amount": {"type": "number", "exclusiveMinimum": 0, "maximum": 1e13},
        "payment_method_id": {"type": "integer", "minimum": 1},
        "entry_date": {"type": "string", "format": "date-time"},
        "notes": {"type": "string", "maxLength": 2000},
    }}
    s["ManualEntry"] = {"type": "object", "required": [
        "id", "category_id", "entry_type", "amount", "payment_method_id",
        "entry_date", "lifecycle_status", "created_at", "created_by",
    ], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "category_id": {"type": "integer"},
        "category_name": {"type": "string"},
        "entry_type": {"type": "string", "enum": ["income", "expense"]},
        "amount": {"type": "number", "exclusiveMinimum": 0},
        "payment_method_id": {"type": "integer"},
        "entry_date": {"type": "string", "format": "date-time"},
        "notes": {"type": ["string", "null"]},
        "lifecycle_status": {"type": "string", "enum": ["posted", "cancelled"]},
        "cancellation_date": {"type": ["string", "null"], "format": "date-time"},
        "cancellation_reason": {"type": ["string", "null"]},
        "cancelled_by": {"type": ["integer", "null"]},
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "integer"},
    }}
    s["ManualEntryCancelRequest"] = {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string", "minLength": 1, "maxLength": 1000}}}

    # -------- Cash movements (read-only) --------
    s["CashMovement"] = {"type": "object", "required": [
        "id", "movement_date", "amount", "direction", "trigger", "payment_method_id",
        "created_at", "created_by",
    ], "description": "Immutable cash journal. Inserted only by server-side business actions.",
     "properties": {
         "id": {"type": "integer", "format": "int64"},
         "movement_date": {"type": "string", "format": "date-time"},
         "amount": {"type": "number", "description": "Signed: positive for inflows, negative for outflows."},
         "direction": {"type": "string", "enum": ["in", "out"]},
         "trigger": {"type": "string", "enum": [
             "sale_payment", "supplier_payment", "manual_income", "manual_expense",
             "refund", "supplier_repayment", "production_overhead", "freight_cash", "change_tendered",
         ]},
         "payment_method_id": {"type": "integer"},
         "reference_type": {"type": ["string", "null"]},
         "reference_id": {"type": ["integer", "null"], "format": "int64"},
         "created_at": {"type": "string", "format": "date-time"},
         "created_by": {"type": "integer"},
     }}
    s["CashBalance"] = {"type": "object", "required": ["balance", "as_of"], "properties": {
        "balance": {"type": "number", "description": "Derived: SUM(cash_movements.amount)."},
        "as_of": {"type": "string", "format": "date-time"},
    }}

    # -------- Notifications --------
    s["Notification"] = {"type": "object", "required": ["id", "type", "message", "is_read", "created_at"], "properties": {
        "id": {"type": "integer", "format": "int64"},
        "type": {"type": "string", "enum": ["low_stock", "out_of_stock", "below_cost_sale", "system", "action_confirmation"]},
        "message": {"type": "string"},
        "related_entity_type": {"type": ["string", "null"]},
        "related_entity_id": {"type": ["integer", "null"]},
        "is_read": {"type": "boolean"},
        "created_at": {"type": "string", "format": "date-time"},
    }}

    # -------- Settings --------
    s["SettingsMap"] = {"type": "object", "description": "Map of system setting key → value. `costing_method` is read-only in V1 (returns 400 on PATCH).",
                        "additionalProperties": {"oneOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "object", "additionalProperties": True}]},
                        "example": {"costing_method": "moving_average", "default_negative_stock_allowed": False}}
    s["SettingsPatch"] = {"type": "object", "description": "Partial update. Each key is validated against the known set.",
                          "additionalProperties": {"oneOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}, {"type": "object", "additionalProperties": True}]}}

    # -------- System --------
    s["HealthResponse"] = {"type": "object", "required": ["status", "server_time"], "properties": {
        "status": {"type": "string", "enum": ["ok"]},
        "version": {"type": "string"},
        "server_time": {"type": "string", "format": "date-time"},
    }}
    s["SystemVersionResponse"] = {"type": "object", "required": ["version", "api_version", "server_time"], "properties": {
        "version": {"type": "string"},
        "api_version": {"type": "string", "example": "v1"},
        "server_time": {"type": "string", "format": "date-time"},
    }}
    s["SystemInfoResponse"] = {"type": "object", "required": ["server_time"], "properties": {
        "company_name": {"type": ["string", "null"]},
        "company_address": {"type": ["string", "null"]},
        "timezone": {"type": "string", "example": "Asia/Jakarta"},
        "server_time": {"type": "string", "format": "date-time"},
    }}

    # -------- Reports / Dashboard / Exports --------
    s["PeriodComparison"] = {"type": "object", "properties": {
        "previous_period": {"type": "object", "properties": {
            "from": {"type": "string", "format": "date-time"},
            "to": {"type": "string", "format": "date-time"},
        }},
        "delta": {"type": "object", "additionalProperties": {"type": "number"}},
        "delta_pct": {"type": "object", "additionalProperties": {"type": "number"}},
    }}
    s["SalesReportResponse"] = {"type": "object", "properties": {
        "data": {"type": "object", "properties": {
            "revenue": {"type": "number"},
            "cogs": {"type": "number"},
            "gross_profit": {"type": "number"},
            "sales_count": {"type": "integer"},
            "average_ticket": {"type": "number"},
        }},
        "comparison": {"$ref": "#/components/schemas/PeriodComparison"},
    }}
    s["ProfitLossResponse"] = {"type": "object", "properties": {
        "data": {"type": "object", "properties": {
            "revenue": {"type": "number"},
            "cogs": {"type": "number"},
            "gross_profit": {"type": "number"},
            "other_income": {"type": "number"},
            "operating_expenses": {"type": "number"},
            "net_profit": {"type": "number"},
        }},
        "comparison": {"$ref": "#/components/schemas/PeriodComparison"},
    }}
    s["InventoryReportResponse"] = {"type": "object", "properties": {
        "data": {"type": "object", "properties": {
            "total_inventory_value": {"type": "number"},
            "products_count": {"type": "integer"},
            "low_stock_count": {"type": "integer"},
            "out_of_stock_count": {"type": "integer"},
        }},
    }}
    s["DashboardResponse"] = {"type": "object", "required": ["kpis", "as_of"], "properties": {
        "kpis": {"type": "object", "properties": {
            "total_sales": {"type": "number"},
            "total_purchases": {"type": "number"},
            "net_profit": {"type": ["number", "null"]},
            "cash_on_hand": {"type": ["number", "null"]},
            "inventory_value": {"type": "number"},
            "low_stock_count": {"type": "integer"},
        }},
        "charts": {"type": "object", "properties": {
            "sales_trend": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "best_sellers": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "expense_breakdown": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        }},
        "comparison": {"$ref": "#/components/schemas/PeriodComparison"},
        "as_of": {"type": "string", "format": "date-time"},
    }}
    s["ExportCreateRequest"] = {"type": "object", "required": ["report", "format"], "properties": {
        "report": {"type": "string", "enum": [
            "sales", "purchases", "inventory", "inventory-movements",
            "sales-returns", "purchase-returns", "production",
            "manual-income", "manual-expense", "refunds",
            "supplier-repayments", "cash-flow", "p-and-l",
            "receivables", "payables", "supplier-receivables", "customer-refund-liabilities",
        ]},
        "format": {"type": "string", "enum": ["pdf", "xlsx"]},
        "filters": {"type": "object", "additionalProperties": True},
    }}
    s["ExportJob"] = {"type": "object", "required": ["export_id", "status", "created_at"], "properties": {
        "export_id": {"type": "string", "format": "uuid"},
        "status": {"type": "string", "enum": ["queued", "processing", "complete", "failed"]},
        "download_url": {"type": ["string", "null"], "format": "uri"},
        "expires_at": {"type": ["string", "null"], "format": "date-time"},
        "error": {"type": ["string", "null"]},
        "created_at": {"type": "string", "format": "date-time"},
    }}

    s["DeactivateRequest"] = {"type": "object", "properties": {"reason": {"type": "string", "maxLength": 1000}}}

    s["PagedResponse"] = {
        "type": "object",
        "description": "Generic paged response used for report endpoints. `data` shape is report-specific.",
        "required": ["data", "pagination"],
        "properties": {
            "data": {"oneOf": [
                {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                {"type": "object", "additionalProperties": True},
            ]},
            "pagination": {"$ref": "#/components/schemas/Pagination"},
            "links": {"$ref": "#/components/schemas/Links"},
        },
    }

    return s


# ============================================================
# COMPONENTS (parameters, responses, security, schemas)
# ============================================================
def build_components():
    c = OrderedDict()
    c["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "Opaque",
            "description": "Session-token auth per API Architecture §2. Server-issued opaque random tokens after credential exchange. 256-bit random, base64url-encoded, stored hashed in the sessions table. Client does NOT parse them. Send `Authorization: Bearer <token>`. 401 = missing/expired/revoked. 403 = authenticated but missing capability.",
        },
    }
    c["parameters"] = {
        "IdempotencyKey": {
            "name": "Idempotency-Key", "in": "header", "required": False,
            "description": "Opaque client key (UUID recommended) for safe retries. Same key + same body returns the original response; different body returns 409 `idempotency_violation`.",
            "schema": {"type": "string", "format": "uuid", "maxLength": 100},
        },
        "IdempotencyKeyRequired": {
            "name": "Idempotency-Key", "in": "header", "required": True,
            "description": "Required. See `IdempotencyKey`.",
            "schema": {"type": "string", "format": "uuid", "maxLength": 100},
        },
        "IfMatch": {
            "name": "If-Match", "in": "header", "required": False,
            "description": "ETag for optimistic concurrency. Resource ETag is the quoted `updated_at` (ISO 8601). Mismatch returns 412 `version_mismatch`.",
            "schema": {"type": "string"},
        },
        "IfMatchRequired": {
            "name": "If-Match", "in": "header", "required": True,
            "description": "Required for lifecycle actions. See `IfMatch`.",
            "schema": {"type": "string"},
        },
        "Page": {"name": "page", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "default": 1}, "description": "1-based page number."},
        "PerPage": {"name": "per_page", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}, "description": "Page size. Hard cap 500."},
        "Sort": {"name": "sort", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Comma-separated list. Prefix `-` for descending."},
        "Q": {"name": "q", "in": "query", "required": False, "schema": {"type": "string", "maxLength": 200}, "description": "Case-insensitive substring search."},
        "From": {"name": "from", "in": "query", "required": False, "schema": {"type": "string", "format": "date-time"}, "description": "ISO 8601 lower bound."},
        "To": {"name": "to", "in": "query", "required": False, "schema": {"type": "string", "format": "date-time"}, "description": "ISO 8601 upper bound."},
        "Period": {"name": "period", "in": "query", "required": False, "schema": {"type": "string", "enum": ["today", "this_week", "this_month", "this_year", "custom"]}, "description": "Convenience period alias."},
        "CompareTo": {"name": "compare_to", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Period to compare against: `period` alias or `from,to`."},
        "Include": {"name": "include", "in": "query", "required": False, "schema": {"type": "string"}, "description": "Comma-separated sub-resources to embed (e.g., `lines,payments,returns`)."},
    }
    c["responses"] = {
        "BadRequest": {"description": "Validation failure (400).",
                       "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationErrorEnvelope"}}}},
        "Unauthorized": {"description": "Missing, invalid, or expired credentials (401).",
                         "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
        "Forbidden": {"description": "Authenticated but missing required capability (403).",
                      "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AuthorizationErrorEnvelope"}}}},
        "NotFound": {"description": "Resource not found (404).",
                     "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
        "Conflict": {"description": "State conflict (409).",
                     "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ConflictErrorEnvelope"}}}},
        "PreconditionFailed": {"description": "If-Match ETag mismatch (412).",
                                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
        "UnprocessableEntity": {"description": "Semantic conflict (422).",
                                 "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
        "RateLimited": {"description": "Rate-limited (429).",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
        "InternalServerError": {"description": "Server-side error (500).",
                                 "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
        "ServiceUnavailable": {"description": "Maintenance or transient (503).",
                                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}},
    }
    c["schemas"] = build_schemas()
    return c


# ============================================================
# PATHS
# ============================================================
def build_paths():
    p = OrderedDict()

    # ===== Auth & System =====
    p["/auth/login"] = {"post": post_op(
        "loginUser", "Authenticate user, issue access + refresh tokens. Required `Idempotency-Key` (30s TTL) prevents replay.",
        tags=["Auth"],
        request_schema="LoginRequest",
        responses_200="LoginResponse",
        idempotency_required=True,
        security=[],
        x_ratelimit="5 per 15 min per IP; 10 per hour per user",
        response_codes={
            401: ("Unauthorized", "ErrorEnvelope", "invalid_credentials / session_expired / session_revoked"),
            423: ("Locked", "ErrorEnvelope", "account_locked (5th attempt within 15 min)"),
            429: ("Too Many Requests", "ErrorEnvelope", "rate_limited"),
        })}
    p["/auth/refresh"] = {"post": post_op(
        "refreshToken", "Rotate access and refresh tokens. Old refresh is invalidated.",
        tags=["Auth"],
        request_schema="RefreshRequest",
        responses_200="LoginResponse",
        security=[],
        response_codes={401: ("Unauthorized", "ErrorEnvelope", "invalid_credentials / session_expired / session_revoked")})}
    p["/auth/logout"] = {"post": post_op("logoutSession", "Revoke the current session.",
                                          tags=["Auth"], responses_204=None)}
    p["/auth/logout-all"] = {"post": post_op("logoutAllSessions", "Revoke all sessions for the current user.",
                                              tags=["Auth"], responses_204=None, capability="user.manage")}
    p["/auth/me"] = {"get": get_op("getCurrentUser", "Return current user with effective capabilities.",
                                    "SessionUser", tags=["Auth"])}
    p["/system/health"] = {"get": get_op("getHealth", "Liveness probe (no DB).",
                                          "HealthResponse", tags=["System"], security=[])}
    p["/system/version"] = {"get": get_op("getVersion", "Server version info.",
                                           "SystemVersionResponse", tags=["System"], security=[])}
    p["/system/info"] = {"get": get_op("getSystemInfo", "Company info and server time.",
                                        "SystemInfoResponse", tags=["System"])}

    # ===== Users =====
    p["/users"] = {
        "get": collection_get_op("listUsers", "List users.", "User", tags=["Users"], capability="user.view",
                                  sortable=["username", "full_name", "created_at", "updated_at", "last_login_at"]),
        "post": post_op("createUser", "Create user.", tags=["Users"], capability="user.manage",
                        request_schema="UserCreateRequest", responses_201="User", idempotency_required=True,
                        response_codes={409: ("Conflict", "ErrorEnvelope", "username_exists / email_exists")}),
    }
    p["/users/{id}"] = {
        "parameters": [path_id_("id", "User id")],
        "get": get_op("getUser", "Get user.", "User", tags=["Users"], capability="user.view"),
        "patch": patch_op("updateUser", "Update user (full_name, email, is_active, role_id).",
                          tags=["Users"], capability="user.manage", request_schema="UserUpdateRequest",
                          if_match_required=True,
                          response_codes={412: ("Precondition Failed", "ErrorEnvelope", "version_mismatch")}),
    }
    p["/users/{id}/deactivate"] = {"post": post_op("deactivateUser", "Deactivate user. Revokes all sessions.",
                                                    tags=["Users"], capability="user.manage", has_path_id=True,
                                                    responses_204=None, idempotency_required=True,
                                                    response_codes={403: ("Forbidden", "ErrorEnvelope", "user is the only Owner")})}
    p["/users/{id}/reset-password"] = {"post": post_op("resetUserPassword", "Reset a user's password; revokes all sessions.",
                                                       tags=["Users"], capability="user.manage", has_path_id=True,
                                                       request_schema="PasswordResetRequest", responses_204=None,
                                                       idempotency_required=True)}
    p["/users/{id}/unlock"] = {"post": post_op("unlockUser", "Clear failed-attempt counter.",
                                                tags=["Users"], capability="user.manage", has_path_id=True,
                                                responses_204=None, idempotency_required=True)}
    p["/users/{id}/capabilities"] = {
        "parameters": [path_id_("id", "User id")],
        "get": get_op("listUserEffectiveCapabilities", "List role + overrides + effective.",
                      "UserEffectiveCapabilities", tags=["Users"], capability="user.view"),
        "post": post_op("grantUserCapability", "Grant or deny a capability for a user.",
                        tags=["Users"], capability="user.grant_capability",
                        request_schema="UserCapabilityOverrideRequest",
                        responses_204=None, idempotency_required=True, if_match_required=True,
                        response_codes={400: ("Bad Request", "ErrorEnvelope", "enum_value_invalid (unknown capability)")}),
    }
    p["/users/{id}/capabilities/{capability_code}"] = {
        "parameters": [path_id_("id", "User id"),
                       {"name": "capability_code", "in": "path", "required": True, "schema": {"type": "string"}}],
        "delete": delete_op("revokeUserCapability", "Revoke capability override for a user. Effect is immediate.",
                            tags=["Users"], capability="user.revoke_capability", idempotency_required=True),
    }

    # ===== Roles =====
    p["/roles"] = {
        "get": collection_get_op("listRoles", "List roles.", "Role", tags=["Roles"], capability="role.view",
                                  sortable=["name", "created_at", "updated_at"]),
        "post": post_op("createRole", "Create role. Starts with zero capabilities.",
                        tags=["Roles"], capability="role.manage",
                        request_schema="RoleCreateRequest", responses_201="Role", idempotency_required=True),
    }
    p["/roles/{id}"] = {
        "parameters": [path_id_("id", "Role id")],
        "get": get_op("getRole", "Get role.", "Role", tags=["Roles"], capability="role.view"),
        "patch": patch_op("updateRole", "Update role name/description.",
                          tags=["Roles"], capability="role.manage", request_schema="RoleUpdateRequest",
                          if_match_required=True,
                          response_codes={403: ("Forbidden", "ErrorEnvelope", "system_role_immutable")}),
        "delete": delete_op("deleteRole", "Delete role.",
                            tags=["Roles"], capability="role.manage", idempotency_required=True,
                            response_codes={
                                403: ("Forbidden", "ErrorEnvelope", "system_role_immutable"),
                                409: ("Conflict", "ErrorEnvelope", "referenced_by_history (users assigned)"),
                            }),
    }
    p["/roles/{id}/capabilities"] = {
        "parameters": [path_id_("id", "Role id")],
        "put": put_op("setRoleCapabilities", "Replace the role's full capability set.",
                      tags=["Roles"], capability="role.manage", request_schema="RoleCapabilitiesRequest",
                      responses_200="Role", idempotency_required=True,
                      response_codes={403: ("Forbidden", "ErrorEnvelope", "system_role_immutable")}),
    }

    # ===== Capabilities catalog =====
    p["/capabilities"] = {"get": collection_get_op("listCapabilities", "List canonical capability codes.", "Capability",
                                            tags=["Capabilities"], capability="user.view",
                                            sortable=["domain", "code"])}

    # ===== Audit =====
    p["/audit"] = {"get": collection_get_op("listAudit", "List audit entries. Read-only.", "AuditEntry",
                                     tags=["Audit"], capability="audit.view",
                                     filterable_extra={
                                         "filter[user_id]": {"schema": {"type": "integer"}},
                                         "filter[action]": {"schema": {"type": "string"}},
                                         "filter[entity_type]": {"schema": {"type": "string"}},
                                         "filter[entity_id]": {"schema": {"type": "integer"}},
                                     },
                                     sortable=["event_time", "user_id", "action"])}

    # ===== Master data loop (categories, units, payment methods, cost types, financial categories) =====
    for resource, tag, schema, req_schema, patch_schema, cap in [
        ("categories", "Categories", "Category", "CategoryRequest", "CategoryPatch", "category"),
        ("units", "Units", "Unit", "UnitRequest", "UnitPatch", "unit"),
        ("payment-methods", "Payment Methods", "PaymentMethod", "PaymentMethodRequest", "PaymentMethodPatch", "payment_method"),
        ("cost-types", "Cost Types", "CostType", "CostTypeRequest", "CostTypePatch", "cost_type"),
        ("financial-categories", "Financial Categories", "FinancialCategory", "FinancialCategoryRequest", "FinancialCategoryPatch", "financial_category"),
    ]:
        cap_view = f"{cap}.view"
        cap_manage = f"{cap}.manage"
        singular = tag[:-1]
        # plural list
        p[f"/{resource}"] = {
            "get": collection_get_op(f"list{tag.replace(' ', '')}", f"List {tag.lower()}.", schema,
                                      tags=[tag], capability=cap_view,
                                      sortable=["name", "code", "created_at", "updated_at"]),
            "post": post_op(f"create{tag.replace(' ', '')}", f"Create {singular.lower()}.",
                            tags=[tag], capability=cap_manage,
                            request_schema=req_schema, responses_201=schema, idempotency_required=True),
        }
        # single
        p[f"/{resource}/{{id}}"] = {
            "parameters": [path_id_("id", f"{singular} id")],
            "get": get_op(f"get{tag.replace(' ', '')}", f"Get {singular.lower()}.", schema,
                          tags=[tag], capability=cap_view),
            "patch": patch_op(f"update{tag.replace(' ', '')}", f"Update {singular.lower()}.",
                              tags=[tag], capability=cap_manage, request_schema=patch_schema, if_match_required=True),
            "delete": delete_op(f"delete{tag.replace(' ', '')}", f"Hard delete {singular.lower()} (only if unreferenced).",
                                tags=[tag], capability=cap_manage, idempotency_required=True,
                                response_codes={409: ("Conflict", "ErrorEnvelope", "referenced_by_history")}),
        }
        p[f"/{resource}/{{id}}/deactivate"] = {
            "post": post_op(f"deactivate{tag.replace(' ', '')}", f"Deactivate {singular.lower()}. Preserves history.",
                            tags=[tag], capability=cap_manage, has_path_id=True,
                            request_schema="DeactivateRequest", responses_204=None, idempotency_required=True),
        }

    # ===== Products =====
    p["/products"] = {
        "get": collection_get_op("listProducts", "List products (with derived stock fields).", "Product",
                                  tags=["Products"], capability="product.view",
                                  filterable_extra={
                                      "filter[category_id]": {"schema": {"type": "integer"}},
                                      "filter[is_active]": {"schema": {"type": "boolean"}},
                                      "filter[is_sellable]": {"schema": {"type": "boolean"}},
                                      "filter[is_purchasable]": {"schema": {"type": "boolean"}},
                                      "filter[is_producible]": {"schema": {"type": "boolean"}},
                                  },
                                  extra_query_params={
                                      "low_stock": {"name": "low_stock", "in": "query", "required": False,
                                                    "schema": {"type": "boolean"},
                                                    "description": "Return only products at or below low-stock threshold."},
                                  },
                                  sortable=["name", "code", "created_at", "updated_at", "selling_price"]),
        "post": post_op("createProduct",
                        "Create product. Initial stock is zero; use `POST /inventory/adjustments` to set opening stock.",
                        tags=["Products"], capability="product.create",
                        request_schema="ProductCreateRequest", responses_201="Product", idempotency_required=True),
    }
    p["/products/{id}"] = {
        "parameters": [path_id_("id", "Product id")],
        "get": get_op("getProduct", "Get product with last 10 price-history rows.",
                      "Product", tags=["Products"], capability="product.view"),
        "patch": patch_op("updateProduct",
                          "Update product master. Price changes insert a `product_price_history` row; historical cost is unchanged.",
                          tags=["Products"], capability="product.edit", request_schema="ProductPatch",
                          if_match_required=True),
        "delete": delete_op("deleteProduct", "Hard delete (only if unreferenced).",
                            tags=["Products"], capability="product.manage", idempotency_required=True,
                            response_codes={409: ("Conflict", "ErrorEnvelope", "referenced_by_history")}),
    }
    p["/products/{id}/deactivate"] = {"post": post_op(
        "deactivateProduct",
        "Deactivate product. Not sellable in new transactions; still counted in stock value and historical reports.",
        tags=["Products"], capability="product.deactivate", has_path_id=True,
        request_schema="DeactivateRequest", responses_204=None, idempotency_required=True)}
    p["/products/{id}/price-history"] = {
        "parameters": [path_id_("id", "Product id")],
        "get": collection_get_op("getProductPriceHistory", "Price history (newest first).",
                                  "ProductPriceHistory", tags=["Products"], capability="product.view",
                                  sortable=["effective_at"]),
    }
    p["/products/{id}/stock-movements"] = {
        "parameters": [path_id_("id", "Product id")],
        "get": collection_get_op("getProductStockMovements", "Stock movements for a product.",
                                  "StockMovement", tags=["Products"], capability="inventory.view",
                                  sortable=["movement_date", "id"]),
    }
    p["/products/{id}/valuation"] = {
        "parameters": [path_id_("id", "Product id")],
        "get": get_op("getProductValuation", "Per-product valuation snapshot.",
                      "ProductValuation", tags=["Products"], capability="inventory.view"),
    }

    # ===== Contacts =====
    p["/contacts"] = {
        "get": collection_get_op("listContacts", "List contacts (customers, suppliers, both).", "Contact",
                                  tags=["Contacts"], capability="contact.view",
                                  filterable_extra={
                                      "filter[type]": {"schema": {"type": "string", "enum": ["customer", "supplier", "both"]}},
                                      "filter[is_active]": {"schema": {"type": "boolean"}},
                                  },
                                  sortable=["name", "created_at", "updated_at"]),
        "post": post_op("createContact", "Create contact.", tags=["Contacts"], capability="contact.create",
                        request_schema="ContactRequest", responses_201="Contact", idempotency_required=True),
    }
    p["/contacts/{id}"] = {
        "parameters": [path_id_("id", "Contact id")],
        "get": get_op("getContact", "Get contact.", "Contact", tags=["Contacts"], capability="contact.view"),
        "patch": patch_op("updateContact", "Update contact.", tags=["Contacts"], capability="contact.edit",
                          request_schema="ContactPatch", if_match_required=True),
        "delete": delete_op("deleteContact", "Hard delete contact (only if unreferenced).",
                            tags=["Contacts"], capability="contact.manage", idempotency_required=True,
                            response_codes={409: ("Conflict", "ErrorEnvelope", "referenced_by_history")}),
    }
    p["/contacts/{id}/deactivate"] = {"post": post_op("deactivateContact", "Deactivate contact.",
                                                       tags=["Contacts"], capability="contact.manage", has_path_id=True,
                                                       request_schema="DeactivateRequest", responses_204=None,
                                                       idempotency_required=True)}

    # ===== Sales =====
    p["/sales"] = {
        "get": collection_get_op("listSales", "List sales.", "Sale",
                                  tags=["Sales"], capability="sale.view",
                                  filterable_extra={
                                      "filter[lifecycle_status]": {"schema": {"type": "string",
                                                                              "enum": ["draft", "posted", "completed", "partially_returned", "returned", "cancelled"]}},
                                      "filter[customer_id]": {"schema": {"type": "integer"}},
                                      "filter[payment_state]": {"schema": {"type": "string",
                                                                            "enum": ["unpaid", "partial", "paid"]}},
                                  },
                                  sortable=["sale_date", "id", "total_amount", "created_at"]),
        "post": post_op("createSale", "Create sale in Draft. Server computes line_total and total_amount.",
                        tags=["Sales"], capability="sale.create",
                        request_schema="SaleCreateRequest", responses_201="Sale", idempotency_required=True),
    }
    p["/sales/{id}"] = {
        "parameters": [path_id_("id", "Sale id", "int64")],
        "get": get_op("getSale",
                      "Get sale. Use `?include=lines,payments,returns` to embed sub-resources.",
                      "Sale", tags=["Sales"], capability="sale.view"),
        "patch": patch_op("updateSaleDraft",
                          "Update sale in Draft. Author: owner of draft or `sale.edit_own_draft`.",
                          tags=["Sales"], capability="sale.edit_own_draft",
                          request_schema="SalePatch", if_match_required=True,
                          response_codes={403: ("Forbidden", "ErrorEnvelope", "lifecycle_state_invalid (not draft)")}),
        "delete": delete_op("deleteSaleDraft", "Hard delete a Draft sale (only if no effects).",
                            tags=["Sales"], capability="sale.create", idempotency_required=True,
                            response_codes={409: ("Conflict", "ErrorEnvelope", "lifecycle_state_invalid (not draft)")}),
    }
    p["/sales/{id}/lines"] = {
        "parameters": [path_id_("id", "Sale id", "int64")],
        "get": collection_get_op("listSaleLines", "List lines for a sale.", "SaleLine",
                                  tags=["Sale Lines"], capability="sale.view", sortable=["line_number"]),
        "post": post_op("addSaleLine", "Add a line to a Draft sale. Mutates parent draft totals; requires If-Match.",
                        tags=["Sale Lines"], capability="sale.edit_own_draft",
                        request_schema="SaleLineInput", responses_201="SaleLine",
                        idempotency_required=True, if_match_required=True),
    }
    p["/sales/{id}/lines/{line_id}"] = {
        "parameters": [path_id_("id", "Sale id", "int64"), path_id_("line_id", "Line id", "int64")],
        "patch": patch_op("updateSaleLine", "Update a Draft sale line.",
                          tags=["Sale Lines"], capability="sale.edit_own_draft",
                          request_schema="SaleLinePatch", if_match_required=True),
        "delete": delete_op("deleteSaleLine", "Delete a Draft sale line.",
                            tags=["Sale Lines"], capability="sale.edit_own_draft", idempotency_required=True),
    }
    p["/sales/{id}/post"] = {"post": post_op(
        "postSale",
        "Post sale: snapshot COGS, deduct stock, recognize revenue, create audit. Idempotent. Requires If-Match.",
        tags=["Sales"], capability="sale.post", has_path_id=True, responses_200="Sale",
        idempotency_required=True, if_match_required=True,
        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                              "lifecycle_state_invalid / insufficient_stock / negative_stock_disallowed / version_mismatch")})}
    p["/sales/{id}/payments"] = {
        "parameters": [path_id_("id", "Sale id", "int64")],
        "get": collection_get_op("listSalePayments", "List payments on a sale.", "SalePayment",
                                  tags=["Sale Payments"], capability="sale.view", sortable=["payment_date", "id"]),
        "post": post_op("addSalePayment",
                        "Record a payment on a sale. Over-tender: set `tendered_amount` (only on `is_cash = true`). Mutates parent; requires If-Match.",
                        tags=["Sale Payments"], capability="sale.create", has_path_id=True,
                        request_schema="SalePaymentInput", responses_201="SalePayment",
                        idempotency_required=True, if_match_required=True,
                        response_codes={
                            400: ("Bad Request", "ErrorEnvelope", "tendered_not_allowed_for_non_cash / change_amount_mismatch"),
                            409: ("Conflict", "ConflictErrorEnvelope", "payment_exceeds_total / cash_insufficient / version_mismatch"),
                        }),
    }
    p["/sales/{id}/cancel"] = {"post": post_op(
        "cancelSale", "Cancel a posted/completed/returned sale. Reverses stock + revenue; no cash movement (CRL preserved).",
        tags=["Sales"], capability="sale.cancel", has_path_id=True, request_schema="SaleCancelRequest",
        responses_200="Sale", idempotency_required=True, if_match_required=True,
        response_codes={409: ("Conflict", "ConflictErrorEnvelope", "already_cancelled / lifecycle_state_invalid / version_mismatch")})}
    p["/sales/{id}/returns"] = {
        "parameters": [path_id_("id", "Sale id", "int64")],
        "get": collection_get_op("listSaleReturns", "List returns on a sale.", "SalesReturn",
                                  tags=["Sales Returns"], capability="sale.view", sortable=["return_date", "id"]),
        "post": post_op("createSaleReturn", "Process a return: increase stock at original cost; update lifecycle.",
                        tags=["Sales Returns"], capability="sale.return", has_path_id=True,
                        request_schema="SaleReturnRequest", responses_201="SalesReturn",
                        idempotency_required=True, if_match_required=True,
                        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                                              "lifecycle_state_invalid / return_quantity_exceeds_original / version_mismatch")}),
    }
    p["/sales-returns"] = {"get": collection_get_op("listSalesReturns", "List all sales returns.", "SalesReturn",
                                             tags=["Sales Returns"], capability="sale.view",
                                             filterable_extra={
                                                 "filter[sale_id]": {"schema": {"type": "integer"}},
                                                 "filter[customer_id]": {"schema": {"type": "integer"}},
                                                 "filter[lifecycle_status]": {"schema": {"type": "string",
                                                                                          "enum": ["posted", "cancelled"]}},
                                             },
                                             sortable=["return_date", "id"])}
    p["/sales-returns/{id}"] = {
        "parameters": [path_id_("id", "Sales return id", "int64")],
        "get": get_op("getSalesReturn", "Get sales return.", "SalesReturn",
                      tags=["Sales Returns"], capability="sale.view"),
    }
    p["/sales-returns/{id}/cancel"] = {"post": post_op(
        "cancelSalesReturn", "Cancel a sales return: insert reversal movements, update lifecycle.",
        tags=["Sales Returns"], capability="sale.return", has_path_id=True,
        request_schema="SalesReturnCancelRequest", responses_200="SalesReturn", idempotency_required=True, if_match_required=True)}

    # ===== Refunds =====
    p["/refunds"] = {
        "get": collection_get_op("listRefunds", "List refunds (customer disbursements). Refunds are cash-out, never income.",
                                  "Refund", tags=["Refunds"], capability="sale.view",
                                  filterable_extra={
                                      "filter[sale_id]": {"schema": {"type": "integer"}},
                                      "filter[payment_method_id]": {"schema": {"type": "integer"}},
                                  },
                                  sortable=["refund_date", "id", "amount"]),
        "post": post_op("createRefund",
                        "Disburse a refund. Bounded by current CRL and current cash balance. Mutates parent; requires If-Match.",
                        tags=["Refunds"], capability="sale.refund",
                        request_schema="RefundRequest", responses_201="Refund",
                        idempotency_required=True, if_match_required=True,
                        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                                              "refund_exceeds_crl / cash_insufficient / version_mismatch")}),
    }
    p["/refunds/{id}"] = {
        "parameters": [path_id_("id", "Refund id", "int64")],
        "get": get_op("getRefund", "Get refund.", "Refund", tags=["Refunds"], capability="sale.view"),
    }

    # ===== Purchases =====
    p["/purchases"] = {
        "get": collection_get_op("listPurchases", "List purchases.", "Purchase",
                                  tags=["Purchases"], capability="purchase.view",
                                  filterable_extra={
                                      "filter[lifecycle_status]": {"schema": {"type": "string",
                                                                              "enum": ["draft", "posted", "completed", "partially_returned", "returned", "cancelled"]}},
                                      "filter[supplier_id]": {"schema": {"type": "integer"}},
                                      "filter[payment_state]": {"schema": {"type": "string",
                                                                            "enum": ["unpaid", "partial", "paid"]}},
                                  },
                                  sortable=["purchase_date", "id", "total_amount", "created_at"]),
        "post": post_op("createPurchase", "Create purchase in Draft. Supplier optional (counter/cash buys allowed).",
                        tags=["Purchases"], capability="purchase.create",
                        request_schema="PurchaseCreateRequest", responses_201="Purchase", idempotency_required=True),
    }
    p["/purchases/{id}"] = {
        "parameters": [path_id_("id", "Purchase id", "int64")],
        "get": get_op("getPurchase", "Get purchase. Use `?include=lines,shipping,payments,returns`.",
                      "Purchase", tags=["Purchases"], capability="purchase.view"),
        "patch": patch_op("updatePurchaseDraft", "Update Draft purchase.",
                          tags=["Purchases"], capability="purchase.edit_own_draft",
                          request_schema="PurchasePatch", if_match_required=True,
                          response_codes={403: ("Forbidden", "ErrorEnvelope", "lifecycle_state_invalid (not draft)")}),
        "delete": delete_op("deletePurchaseDraft", "Hard delete Draft purchase.",
                            tags=["Purchases"], capability="purchase.create", idempotency_required=True,
                            response_codes={409: ("Conflict", "ErrorEnvelope", "lifecycle_state_invalid (not draft)")}),
    }
    p["/purchases/{id}/lines"] = {
        "parameters": [path_id_("id", "Purchase id", "int64")],
        "get": collection_get_op("listPurchaseLines", "List purchase lines.", "PurchaseLine",
                                  tags=["Purchase Lines"], capability="purchase.view", sortable=["line_number"]),
        "post": post_op("addPurchaseLine", "Add a Draft purchase line. Mutates draft totals; requires If-Match.",
                        tags=["Purchase Lines"], capability="purchase.edit_own_draft",
                        request_schema="PurchaseLineInput", responses_201="PurchaseLine",
                        idempotency_required=True, if_match_required=True),
    }
    p["/purchases/{id}/lines/{line_id}"] = {
        "parameters": [path_id_("id", "Purchase id", "int64"), path_id_("line_id", "Purchase line id", "int64")],
        "patch": patch_op("updatePurchaseLine", "Update a Draft purchase line.",
                          tags=["Purchase Lines"], capability="purchase.edit_own_draft",
                          request_schema="PurchaseLinePatch", if_match_required=True),
        "delete": delete_op("deletePurchaseLine", "Delete a Draft purchase line.",
                            tags=["Purchase Lines"], capability="purchase.edit_own_draft", idempotency_required=True),
    }
    p["/purchases/{id}/shipping"] = {
        "parameters": [path_id_("id", "Purchase id", "int64")],
        "post": post_op("setPurchaseShipping",
                        "Add or replace shipping on a Draft purchase. Always capitalized into landed cost. Requires If-Match.",
                        tags=["Purchase Shipping"], capability="purchase.edit_own_draft", has_path_id=True,
                        request_schema="PurchaseShippingRequest", responses_200="PurchaseShipping",
                        idempotency_required=True, if_match_required=True),
        "patch": patch_op("updatePurchaseShipping", "Update shipping on a Draft purchase.",
                          tags=["Purchase Shipping"], capability="purchase.edit_own_draft",
                          request_schema="PurchaseShippingRequest", if_match_required=True),
    }
    p["/purchases/{id}/post"] = {"post": post_op(
        "postPurchase",
        "Post purchase: capitalize shipping into landed cost, receive stock, create AP. Idempotent. Requires If-Match.",
        tags=["Purchases"], capability="purchase.post", has_path_id=True, responses_200="Purchase",
        idempotency_required=True, if_match_required=True,
        response_codes={409: ("Conflict", "ConflictErrorEnvelope", "lifecycle_state_invalid / version_mismatch")})}
    p["/purchases/{id}/payments"] = {
        "parameters": [path_id_("id", "Purchase id", "int64")],
        "get": collection_get_op("listPurchasePayments", "List payments on a purchase.", "PurchasePayment",
                                  tags=["Purchase Payments"], capability="purchase.view", sortable=["payment_date", "id"]),
        "post": post_op("addPurchasePayment",
                        "Record a payment on a purchase. Over-tender supported (cash only). Mutates parent; requires If-Match.",
                        tags=["Purchase Payments"], capability="purchase.create", has_path_id=True,
                        request_schema="PurchasePaymentInput", responses_201="PurchasePayment",
                        idempotency_required=True, if_match_required=True,
                        response_codes={
                            400: ("Bad Request", "ErrorEnvelope", "tendered_not_allowed_for_non_cash / change_amount_mismatch"),
                            409: ("Conflict", "ConflictErrorEnvelope", "allocation_exceeds_payable / cash_insufficient / version_mismatch"),
                        }),
    }
    p["/purchases/{id}/cancel"] = {"post": post_op(
        "cancelPurchase",
        "Cancel a posted purchase. On-hand portion reverses via `purchase_reversal`; consumed portion via `value_adjustment` (qty=0).",
        tags=["Purchases"], capability="purchase.cancel", has_path_id=True,
        request_schema="PurchaseCancelRequest", responses_200="Purchase",
        idempotency_required=True, if_match_required=True,
        response_codes={409: ("Conflict", "ConflictErrorEnvelope", "already_cancelled / lifecycle_state_invalid / version_mismatch")})}
    p["/purchases/{id}/returns"] = {
        "parameters": [path_id_("id", "Purchase id", "int64")],
        "post": post_op("createPurchaseReturn", "Return goods to a supplier. Stock decreases at original landed cost.",
                        tags=["Purchase Returns"], capability="purchase.return", has_path_id=True,
                        request_schema="PurchaseReturnRequest", responses_201="PurchaseReturn",
                        idempotency_required=True, if_match_required=True,
                        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                                              "lifecycle_state_invalid / purchase_return_quantity_exceeds_original / version_mismatch")}),
    }
    p["/purchase-returns"] = {"get": collection_get_op("listPurchaseReturns", "List all purchase returns.", "PurchaseReturn",
                                                tags=["Purchase Returns"], capability="purchase.view",
                                                filterable_extra={
                                                    "filter[purchase_id]": {"schema": {"type": "integer"}},
                                                    "filter[supplier_id]": {"schema": {"type": "integer"}},
                                                    "filter[lifecycle_status]": {"schema": {"type": "string",
                                                                                         "enum": ["posted", "cancelled"]}},
                                                },
                                                sortable=["return_date", "id"])}
    p["/purchase-returns/{id}"] = {
        "parameters": [path_id_("id", "Purchase return id", "int64")],
        "get": get_op("getPurchaseReturn", "Get purchase return.", "PurchaseReturn",
                      tags=["Purchase Returns"], capability="purchase.view"),
    }
    p["/purchase-returns/{id}/cancel"] = {"post": post_op(
        "cancelPurchaseReturn", "Cancel a purchase return: insert reversal movements, update lifecycle.",
        tags=["Purchase Returns"], capability="purchase.return", has_path_id=True,
        request_schema="PurchaseReturnCancelRequest", responses_200="PurchaseReturn", idempotency_required=True, if_match_required=True)}

    # ===== Supplier repayments =====
    p["/supplier-repayments"] = {
        "get": collection_get_op("listSupplierRepayments",
                                  "List supplier repayments (cash received from suppliers; clears Supplier Receivable).",
                                  "SupplierRepayment", tags=["Supplier Repayments"], capability="purchase.view",
                                  filterable_extra={
                                      "filter[purchase_id]": {"schema": {"type": "integer"}},
                                      "filter[payment_method_id]": {"schema": {"type": "integer"}},
                                  },
                                  sortable=["repayment_date", "id", "amount"]),
        "post": post_op("createSupplierRepayment",
                        "Record cash received from a supplier. Mutates parent purchase (SRec) and cash ledger; requires If-Match.",
                        tags=["Supplier Repayments"], capability="purchase.refund",
                        request_schema="SupplierRepaymentRequest", responses_201="SupplierRepayment",
                        idempotency_required=True, if_match_required=True,
                        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                                              "repayment_exceeds_srec / cash_insufficient / version_mismatch")}),
    }
    p["/supplier-repayments/{id}"] = {
        "parameters": [path_id_("id", "Supplier repayment id", "int64")],
        "get": get_op("getSupplierRepayment", "Get supplier repayment.", "SupplierRepayment",
                      tags=["Supplier Repayments"], capability="purchase.view"),
    }

    # ===== Inventory =====
    p["/stock-movements"] = {"get": collection_get_op("listStockMovements",
                                              "List stock movements (read-only). The single inventory ledger.",
                                              "StockMovement", tags=["Stock Movements"], capability="inventory.view",
                                              filterable_extra={
                                                  "filter[product_id]": {"schema": {"type": "integer"}},
                                                  "filter[trigger]": {"schema": {"type": "string"}},
                                                  "filter[reference_type]": {"schema": {"type": "string"}},
                                                  "filter[reference_id]": {"schema": {"type": "integer"}},
                                              },
                                              sortable=["movement_date", "id"])}
    p["/stock-movements/{id}"] = {
        "parameters": [path_id_("id", "Stock movement id", "int64")],
        "get": get_op("getStockMovement", "Get stock movement.", "StockMovement",
                      tags=["Stock Movements"], capability="inventory.view"),
    }
    p["/inventory"] = {"get": collection_get_op("listInventory", "Per-product inventory summary (paginated).",
                                         "InventorySummary", tags=["Inventory"], capability="inventory.view",
                                         filterable_extra={
                                             "filter[category_id]": {"schema": {"type": "integer"}},
                                             "filter[is_active]": {"schema": {"type": "boolean"}},
                                         },
                                         sortable=["on_hand_quantity", "inventory_value"])}
    p["/inventory/products/{product_id}"] = {
        "parameters": [path_id_("product_id", "Product id")],
        "get": get_op("getProductStock",
                      "Per-product stock snapshot (on-hand, moving avg, low_stock).",
                      "ProductStock", tags=["Inventory"], capability="inventory.view"),
    }
    p["/inventory/low-stock"] = {"get": collection_get_op("listLowStock",
                                                   "Products at or below their low-stock threshold.",
                                                   "InventorySummary", tags=["Inventory"], capability="inventory.view")}
    p["/inventory/adjustments"] = {"post": post_op(
        "createStockAdjustment",
        "Apply a stock adjustment (signed qty). Reason required and logged in audit. P&L effect is NOT auto-posted in V1. Mutates product; requires If-Match.",
        tags=["Inventory"], capability="inventory.adjust",
        request_schema="StockAdjustmentRequest", responses_201="StockMovement",
        idempotency_required=True, if_match_required=True,
        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                              "insufficient_stock / negative_stock_disallowed / version_mismatch")})}

    # ===== Production =====
    p["/production-runs"] = {
        "get": collection_get_op("listProductionRuns", "List production runs.", "ProductionRun",
                                  tags=["Production Runs"], capability="production.view",
                                  filterable_extra={
                                      "filter[lifecycle_status]": {"schema": {"type": "string",
                                                                              "enum": ["draft", "posted", "completed", "cancelled"]}},
                                      "filter[output_product_id]": {"schema": {"type": "integer"}},
                                  },
                                  sortable=["run_date", "id", "created_at"]),
        "post": post_op("createProductionRun", "Create production run in Draft.",
                        tags=["Production Runs"], capability="production.create",
                        request_schema="ProductionRunCreateRequest", responses_201="ProductionRun",
                        idempotency_required=True),
    }
    p["/production-runs/{id}"] = {
        "parameters": [path_id_("id", "Production run id", "int64")],
        "get": get_op("getProductionRun", "Get production run. Use `?include=inputs,cost_lines,output`.",
                      "ProductionRun", tags=["Production Runs"], capability="production.view"),
        "patch": patch_op("updateProductionRunDraft", "Update Draft production run.",
                          tags=["Production Runs"], capability="production.edit_own_draft",
                          request_schema="ProductionRunPatch", if_match_required=True),
    }
    p["/production-runs/{id}/inputs"] = {
        "parameters": [path_id_("id", "Production run id", "int64")],
        "get": collection_get_op("listProductionInputs", "List raw-material inputs.", "ProductionInput",
                                  tags=["Production Inputs"], capability="production.view", sortable=["line_number"]),
        "post": post_op("addProductionInput", "Add a raw-material input to a Draft run. Mutates draft; requires If-Match.",
                        tags=["Production Inputs"], capability="production.edit_own_draft", has_path_id=True,
                        request_schema="ProductionInputRequest", responses_201="ProductionInput",
                        idempotency_required=True, if_match_required=True,
                        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                                              "production_inputs_exceed_stock / version_mismatch")}),
    }
    p["/production-runs/{id}/inputs/{input_id}"] = {
        "parameters": [path_id_("id", "Production run id", "int64"), path_id_("input_id", "Input id", "int64")],
        "patch": patch_op("updateProductionInput", "Update a Draft input.",
                          tags=["Production Inputs"], capability="production.edit_own_draft",
                          request_schema="ProductionInputRequest", if_match_required=True),
        "delete": delete_op("deleteProductionInput", "Delete a Draft input.",
                            tags=["Production Inputs"], capability="production.edit_own_draft",
                            idempotency_required=True),
    }
    p["/production-runs/{id}/cost-lines"] = {
        "parameters": [path_id_("id", "Production run id", "int64")],
        "get": collection_get_op("listProductionCostLines", "List overhead cost lines.", "ProductionCostLine",
                                  tags=["Production Cost Lines"], capability="production.view", sortable=["line_number"]),
        "post": post_op("addProductionCostLine",
                        "Add an overhead cost line to a Draft run. Mutates draft; requires If-Match.",
                        tags=["Production Cost Lines"], capability="production.edit_own_draft", has_path_id=True,
                        request_schema="ProductionCostLineRequest", responses_201="ProductionCostLine",
                        idempotency_required=True, if_match_required=True),
    }
    p["/production-runs/{id}/cost-lines/{line_id}"] = {
        "parameters": [path_id_("id", "Production run id", "int64"), path_id_("line_id", "Cost line id", "int64")],
        "patch": patch_op("updateProductionCostLine", "Update a Draft cost line.",
                          tags=["Production Cost Lines"], capability="production.edit_own_draft",
                          request_schema="ProductionCostLineRequest", if_match_required=True),
        "delete": delete_op("deleteProductionCostLine", "Delete a Draft cost line.",
                            tags=["Production Cost Lines"], capability="production.edit_own_draft",
                            idempotency_required=True),
    }
    p["/production-runs/{id}/post"] = {"post": post_op(
        "postProductionRun",
        "Post production run: raw down, finished up, cash for paid overhead. finished_unit_cost = (raw + overhead) / output_qty. Idempotent. Requires If-Match.",
        tags=["Production Runs"], capability="production.post", has_path_id=True, responses_200="ProductionRun",
        idempotency_required=True, if_match_required=True,
        response_codes={409: ("Conflict", "ConflictErrorEnvelope",
                              "lifecycle_state_invalid / production_inputs_exceed_stock / production_finished_cost_invalid / version_mismatch")})}
    p["/production-runs/{id}/cancel"] = {"post": post_op(
        "cancelProductionRun",
        "Cancel a posted production run. Overhead cash_movements are NOT reversed (cash immutability).",
        tags=["Production Runs"], capability="production.cancel", has_path_id=True,
        request_schema="ProductionCancelRequest", responses_200="ProductionRun",
        idempotency_required=True, if_match_required=True)}

    # ===== Manual entries =====
    p["/manual-entries"] = {
        "get": collection_get_op("listManualEntries", "List manual income/expense entries.", "ManualEntry",
                                  tags=["Manual Entries"], capability="manual_entry.view",
                                  filterable_extra={
                                      "filter[entry_type]": {"schema": {"type": "string", "enum": ["income", "expense"]}},
                                      "filter[category_id]": {"schema": {"type": "integer"}},
                                      "filter[lifecycle_status]": {"schema": {"type": "string",
                                                                              "enum": ["posted", "cancelled"]}},
                                  },
                                  sortable=["entry_date", "id", "amount"]),
        "post": post_op("createManualEntry", "Create manual income or expense entry. Cash is moved atomically.",
                        tags=["Manual Entries"], capability_dual=("manual_entry.create_income", "manual_entry.create_expense"),
                        request_schema="ManualEntryRequest", responses_201="ManualEntry", idempotency_required=True,
                        response_codes={400: ("Bad Request", "ErrorEnvelope", "manual_entry_duplicate_of_derived")}),
    }
    p["/manual-entries/{id}"] = {
        "parameters": [path_id_("id", "Manual entry id", "int64")],
        "get": get_op("getManualEntry", "Get manual entry.", "ManualEntry",
                      tags=["Manual Entries"], capability="manual_entry.view"),
    }
    p["/manual-entries/{id}/cancel"] = {"post": post_op(
        "cancelManualEntry", "Cancel a manual entry. Creates a reversing cash_movement.",
        tags=["Manual Entries"], capability="manual_entry.cancel", has_path_id=True,
        request_schema="ManualEntryCancelRequest", responses_200="ManualEntry", idempotency_required=True, if_match_required=True)}

    # ===== Cash movements (read-only) =====
    p["/cash-movements"] = {"get": collection_get_op("listCashMovements",
                                              "List cash movements (read-only). The single cash ledger.",
                                              "CashMovement", tags=["Cash Movements"], capability="finance.view_cash",
                                              filterable_extra={
                                                  "filter[trigger]": {"schema": {"type": "string"}},
                                                  "filter[payment_method_id]": {"schema": {"type": "integer"}},
                                                  "filter[reference_type]": {"schema": {"type": "string"}},
                                                  "filter[reference_id]": {"schema": {"type": "integer"}},
                                              },
                                              sortable=["movement_date", "id"])}
    p["/cash-movements/balance"] = {"get": get_op("getCashBalance",
                                                   "Current cash balance (derived: SUM(cash_movements.amount)).",
                                                   "CashBalance", tags=["Cash Movements"], capability="finance.view_cash")}

    # ===== Reports =====
    report_endpoints = [
        ("sales", "salesReport", "Sales report (revenue, COGS, gross, count, avg ticket).", "SalesReportResponse", "report.view"),
        ("purchases", "purchasesReport", "Purchase report.", "PagedResponse", "report.view"),
        ("inventory", "inventoryReport", "Inventory valuation + low-stock summary.", "InventoryReportResponse", "report.view"),
        ("inventory-movements", "inventoryMovementsReport", "Stock movement history.", "PagedResponse", "report.view"),
        ("sales-returns", "salesReturnsReport", "Sales return report.", "PagedResponse", "report.view"),
        ("purchase-returns", "purchaseReturnsReport", "Purchase return report.", "PagedResponse", "report.view"),
        ("production", "productionReport", "Production runs with cost lines.", "PagedResponse", "report.view"),
        ("manual-income", "manualIncomeReport", "Manual income entries.", "PagedResponse", "report.view"),
        ("manual-expense", "manualExpenseReport", "Manual expense entries.", "PagedResponse", "report.view"),
        ("refunds", "refundsReport", "Refunds (customer disbursements).", "PagedResponse", "report.view"),
        ("supplier-repayments", "supplierRepaymentsReport", "Supplier repayments.", "PagedResponse", "report.view"),
        ("cash-flow", "cashFlowReport", "Cash movements.", "PagedResponse", "report.view"),
        ("p-and-l", "profitAndLoss", "P&L (revenue, COGS, gross, expenses, net).", "ProfitLossResponse", "finance.view_profit"),
        ("receivables", "receivables", "Receivables per sale.", "PagedResponse", "finance.view_payables_receivables"),
        ("payables", "payables", "Payables per purchase.", "PagedResponse", "finance.view_payables_receivables"),
        ("supplier-receivables", "supplierReceivables", "Open Supplier Receivable per purchase.", "PagedResponse", "finance.view_payables_receivables"),
        ("customer-refund-liabilities", "customerRefundLiabilities", "Open CRL per sale.", "PagedResponse", "finance.view_payables_receivables"),
    ]
    for slug, op_id, desc, schema, cap in report_endpoints:
        p[f"/reports/{slug}"] = {"get": get_op(
            op_id, desc, schema, tags=["Reports"], capability=cap,
            parameters=[{"$ref": "#/components/parameters/Period"},
                        {"$ref": "#/components/parameters/From"},
                        {"$ref": "#/components/parameters/To"},
                        {"$ref": "#/components/parameters/CompareTo"}])}

    # ===== Dashboard =====
    p["/dashboard"] = {"get": get_op("getDashboard",
                                       "KPI cards + charts. Requires `finance.view_profit` and `finance.view_cash` (return 403 if missing).",
                                       "DashboardResponse", tags=["Dashboard"], capability="finance.view_profit",
                                       parameters=[{"$ref": "#/components/parameters/Period"},
                                                   {"$ref": "#/components/parameters/From"},
                                                   {"$ref": "#/components/parameters/To"},
                                                   {"$ref": "#/components/parameters/CompareTo"}])}
    p["/dashboard/inventory"] = {"get": get_op("getDashboardInventory",
                                                "Inventory-only KPIs (low-stock count, total value).",
                                                "InventoryReportResponse", tags=["Dashboard"],
                                                capability="inventory.view",
                                                parameters=[{"$ref": "#/components/parameters/Period"}])}

    # ===== Exports =====
    p["/exports"] = {"post": post_op("createExport",
                                      "Create an export job (PDF/XLSX). Rate-limited 5/hour/user.",
                                      tags=["Exports"], capability="export.data",
                                      request_schema="ExportCreateRequest", responses_201="ExportJob",
                                      idempotency_required=True)}
    p["/exports/{id}"] = {
        "parameters": [path_id_("id", "Export job id (UUID)", format_="uuid")],
        "get": get_op("getExport", "Get export job status and (when complete) download URL.",
                      "ExportJob", tags=["Exports"], capability="export.data"),
    }
    p["/exports/{id}/download"] = {
        "parameters": [path_id_("id", "Export job id (UUID)", format_="uuid")],
        "get": {
            "summary": "Download export file (pre-signed URL returned in 302 or streamed body).",
            "description": "Returns a 302 redirect to the pre-signed download URL, or streams the file.",
            "operationId": "downloadExport",
            "tags": ["Exports"],
            "security": [{"bearerAuth": []}],
            "responses": {
                "200": {"description": "File binary.",
                        "content": {
                            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}},
                        }},
                "302": {"description": "Redirect to pre-signed URL."},
                "401": {"$ref": "#/components/responses/Unauthorized"},
                "403": {"$ref": "#/components/responses/Forbidden"},
                "404": {"$ref": "#/components/responses/NotFound"},
                "410": {"description": "Export expired or pruned."},
            },
        },
    }

    # ===== Notifications =====
    p["/notifications"] = {"get": collection_get_op("listNotifications", "List in-app notifications (computed on read for low-stock).",
                                             "Notification", tags=["Notifications"], capability="notification.view",
                                             filterable_extra={"filter[is_read]": {"schema": {"type": "boolean"}}},
                                             sortable=["created_at", "id"])}
    p["/notifications/{id}/mark-read"] = {"post": post_op("markNotificationRead", "Mark notification as read.",
                                                           tags=["Notifications"], capability="notification.mark_read",
                                                           has_path_id=True, responses_204=None)}
    p["/notifications/mark-all-read"] = {"post": post_op("markAllNotificationsRead",
                                                          "Mark all notifications as read for the current user.",
                                                          tags=["Notifications"], capability="notification.mark_read",
                                                          responses_204=None)}

    # ===== Settings =====
    p["/settings"] = {
        "get": get_op("getSettings", "Get system settings map.",
                      "SettingsMap", tags=["Settings"], capability="settings.view"),
        "patch": patch_op("updateSettings",
                          "Update system settings. `costing_method` is read-only in V1 (returns 400).",
                          tags=["Settings"], capability="settings.manage",
                          request_schema="SettingsPatch", responses_200="SettingsMap",
                          if_match_required=True, idempotency_required=True),
    }

    return p


# ============================================================
# ROOT DOCUMENT
# ============================================================
def build_openapi():
    doc = OrderedDict()
    doc["openapi"] = "3.1.0"
    doc["info"] = OrderedDict([
        ("title", "Business Management & POS System API"),
        ("version", "1.0.0"),
        ("summary", "REST + JSON contract for the single-business web-based POS and operations platform."),
        ("description", (
            "Authoritative machine-readable contract derived from API-Architecture-V1.0.md. "
            "Server is the only authority for business rules, accounting, and inventory. "
            "All derived balances (COGS, payment_state, AR/AP/CRL/SRec, cash_balance, inventory_value, "
            "moving_average_unit_cost, on_hand_quantity) are server-computed. Posted records are immutable; "
            "corrections go through lifecycle actions (cancel, return, refund). Cancellation, return, and refund "
            "are distinct events. All amounts use NUMERIC(15,2); quantities NUMERIC(15,4). Single currency (IDR). "
            "All timestamps are ISO 8601 with offset; server normalizes to UTC. "
            "Session-based auth (Bearer opaque tokens) per §2 of the API Architecture."
        )),
        ("contact", OrderedDict([("name", "Business POS System API Team")])),
        ("license", OrderedDict([("name", "Proprietary")])),
    ])

    doc["servers"] = [
        OrderedDict([("url", "https://api.example.com/api/v1"), ("description", "Production")]),
        OrderedDict([("url", "https://staging-api.example.com/api/v1"), ("description", "Staging")]),
        OrderedDict([("url", "http://localhost:8000/api/v1"), ("description", "Local development")]),
    ]

    doc["tags"] = [
        {"name": "Auth", "description": "Login, refresh, logout, current session."},
        {"name": "System", "description": "Health, version, server info."},
        {"name": "Users", "description": "User accounts, password reset, unlock, capability grants."},
        {"name": "Roles", "description": "Role management and capability assignment."},
        {"name": "Capabilities", "description": "Canonical capability catalog (read-only)."},
        {"name": "Audit", "description": "Append-only audit log (read-only)."},
        {"name": "Categories", "description": "Product categories (master data)."},
        {"name": "Units", "description": "Units of measure (master data)."},
        {"name": "Products", "description": "Product master, price history, valuation."},
        {"name": "Contacts", "description": "Customers and suppliers."},
        {"name": "Payment Methods", "description": "Cash, bank transfer, e-wallet, other."},
        {"name": "Cost Types", "description": "Production overhead cost types."},
        {"name": "Financial Categories", "description": "Income and expense categories."},
        {"name": "Sales", "description": "Sales/POS lifecycle: draft, post, pay, cancel, return."},
        {"name": "Sale Lines", "description": "Lines on a sale (sub-resource)."},
        {"name": "Sale Payments", "description": "Payment allocations on a sale (sub-resource)."},
        {"name": "Sales Returns", "description": "Physical goods returns against sales."},
        {"name": "Refunds", "description": "Cash disbursements to customers (cash-out, never income)."},
        {"name": "Purchases", "description": "Purchase lifecycle: draft, receive, pay, cancel, return."},
        {"name": "Purchase Lines", "description": "Lines on a purchase (sub-resource)."},
        {"name": "Purchase Payments", "description": "Payment allocations on a purchase (sub-resource)."},
        {"name": "Purchase Shipping", "description": "Landed cost shipping on a purchase (sub-resource)."},
        {"name": "Purchase Returns", "description": "Physical goods returns to suppliers."},
        {"name": "Supplier Repayments", "description": "Cash received from suppliers (clears Supplier Receivable)."},
        {"name": "Inventory", "description": "Stock movements, per-product stock summary, low-stock list."},
        {"name": "Stock Movements", "description": "Immutable stock movement ledger (read-only)."},
        {"name": "Production Runs", "description": "Raw-to-finished production runs and cost lines."},
        {"name": "Production Inputs", "description": "Raw materials consumed (sub-resource)."},
        {"name": "Production Cost Lines", "description": "Overhead costs (sub-resource)."},
        {"name": "Manual Entries", "description": "Manual income and expense entries."},
        {"name": "Cash Movements", "description": "Immutable cash journal (read-only)."},
        {"name": "Reports", "description": "In-app reports."},
        {"name": "Dashboard", "description": "Dashboard KPIs, trends, best-sellers, expense breakdown."},
        {"name": "Exports", "description": "PDF/Excel export jobs."},
        {"name": "Notifications", "description": "In-app notifications."},
        {"name": "Settings", "description": "System configuration."},
    ]
    doc["externalDocs"] = OrderedDict([
        ("description", "Authoritative narrative API Architecture"),
        ("url", "./API-Architecture-V1.0.md"),
    ])
    doc["paths"] = build_paths()
    doc["components"] = build_components()
    doc["security"] = [{"bearerAuth": []}]
    return doc


def main():
    doc = build_openapi()
    out_path = "C:/Users/ratus/Desktop/ello/projects/Business-POS-System/openapi.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, default_flow_style=False, sort_keys=False, allow_unicode=True, width=120, Dumper=yaml.SafeDumper)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
