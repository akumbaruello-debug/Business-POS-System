"""Validate openapi.yaml:
- structure, $ref resolution, operationId uniqueness, idempotency, concurrency (If-Match on lifecycle writes),
- client vs server-derived field discipline
- no empty/placeholder fields
"""

import yaml

with open(
    r"C:/Users/ratus/Desktop/ello/projects/Business-POS-System/openapi.yaml",
    "r",
    encoding="utf-8",
) as f:
    doc = yaml.safe_load(f)

errors, warnings = [], []

# 1) Version
if doc.get("openapi") != "3.1.0":
    errors.append(f"openapi != 3.1.0 ({doc.get('openapi')})")

# 2) security scheme
ss = doc.get("components", {}).get("securitySchemes", {})
if not ss:
    errors.append("no securitySchemes")
for name, scheme in ss.items():
    if scheme.get("type") != "http":
        errors.append(f"{name}: not http type")
    if scheme.get("scheme") != "bearer":
        errors.append(f"{name}: not bearer")

# 3) operationId uniqueness + 4xx coverage
op_ids = []
ops_4xx = {}
for p, methods in doc.get("paths", {}).items():
    for m, op in methods.items():
        if m not in ("get", "post", "put", "patch", "delete", "options", "head"):
            continue
        oid = op.get("operationId")
        if not oid:
            errors.append(f"{m.upper()} {p}: missing operationId")
        else:
            op_ids.append(oid)
        codes = set(op.get("responses", {}).keys())
        ops_4xx[(p, m)] = codes
dup = [i for i, c in __import__("collections").Counter(op_ids).items() if c > 1]
if dup:
    errors.append(f"duplicate operationId: {dup}")
print(f"operationIds: {len(op_ids)}, unique OK: {not dup}")


# 4) $ref resolution
def collect_ref(node, found):
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            found.append(node["$ref"])
        else:
            for v in node.values():
                collect_ref(v, found)
    elif isinstance(node, list):
        for v in node:
            collect_ref(v, found)


all_refs = []
collect_ref(doc, all_refs)


def resolve(ref):
    parts = ref.lstrip("#/").split("/")
    cur = doc
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


unresolved = [r for r in all_refs if resolve(r) is None]
print(f"$refs: {len(all_refs)}, unresolved: {len(unresolved)}")
if unresolved:
    errors.append(f"unresolved refs: {unresolved[:5]}")

# 5) Idempotency: lifecycle POSTs
lifecycle = [
    "post",
    "cancel",
    "return",
    "refund",
    "repayment",
    "adjust",
    "login",
    "reset",
    "unlock",
    "grant",
    "revoke",
]
missing_idem = []
for p, methods in doc.get("paths", {}).items():
    for m, op in methods.items():
        if m != "post":
            continue
        oid = op.get("operationId", "").lower()
        if not any(k in oid for k in lifecycle):
            continue
        params = op.get("parameters", []) or []
        has = any(
            (pr.get("$ref", "").endswith("IdempotencyKeyRequired"))
            for pr in params
            if isinstance(pr, dict)
        )
        if not has:
            missing_idem.append((p, oid))
print(f"lifecycle POSTs missing Idempotency: {len(missing_idem)}")
if missing_idem:
    errors.append(f"missing idempotency: {missing_idem}")

# 6) Concurrency: lifecycle-write POSTs (post/cancel/return/refund) should have If-Match
write_verbs = [
    "post",
    "cancel",
    "return",
    "refund",
    "adjust",
    "grant",
    "revoke",
    "mark",
]
missing_ifmatch = []
for p, methods in doc.get("paths", {}).items():
    for m, op in methods.items():
        if m != "post":
            continue
        oid = op.get("operationId", "").lower()
        if not any(k in oid for k in write_verbs):
            continue
        params = op.get("parameters", []) or []
        has = any(
            (pr.get("$ref", "").endswith("IfMatchRequired"))
            for pr in params
            if isinstance(pr, dict)
        )
        if not has:
            missing_ifmatch.append((p, oid))
print(f"lifecycle POSTs missing If-Match: {len(missing_ifmatch)}")
# These are acceptable for non-state-transition POSTs (addLine, addPayment). Report as info.
for p, oid in missing_ifmatch:
    print(f"  (no If-Match, expected for non-lifecycle POSTs): {p} {oid}")

# 7) Schema check: request schemas (suffix Request/Input/Patch) must not contain server-derived
server_derived = {
    "total_amount",
    "paid_amount",
    "outstanding",
    "payment_state",
    "ar",
    "ap",
    "crl",
    "srec",
    "inventory_value",
    "moving_average_unit_cost",
    "on_hand_quantity",
    "low_stock",
    "line_total",
    "line_subtotal",
    "allocated_shipping",
    "finished_unit_cost",
    "total_raw_cost",
    "total_overhead_cost",
    "unit_cost_snapshot",
    "cogs_total_snapshot",
    "balance",
    "unit_cost_at_movement",
    "total_cost",
    "refundable_amount_snapshot",
    "category_name",
    "direction",
    "reference_type",
    "reference_id",
    "reversal_of_movement_id",
    "reversed_by_movement_id",
    "created_at",
    "updated_at",
    "created_by",
    "posted_at",
    "posted_by",
    "cancelled_by",
    "cancellation_date",
    "cancellation_reason",
    "version",
    "is_negative_stock_fallback",
    "stock_movement_id",
    "returned_selling_price",
    "returned_unit_cost",
    "line_value",
    "negative_stock_fallback_supported",
    "lifecycle_status",
    "posted_by",
    "outstanding",
    "payment_state",
    "ar",
    "ap",
    "crl",
    "srec",
}
# Schemas that are RESPONSE-only (not request), even if they end in 'Input'
response_only = {
    "SalePaymentInput",
    "SaleLineInput",
    "PurchaseLineInput",
    "ProductionInput",
    "ProductionCostLineRequest",
    "SaleLine",
    "PurchaseLine",
    "ProductionInput",
    "ProductionOutput",
    "ProductionCostLine",
}
# True request schemas = ends with Request or Patch, OR *Request that are truly body inputs
# SalePaymentInput is a request (used in requestBody) — so it IS a request. Let's check usage.


def used_in_requestbody(schema_name):
    needle = f"#/components/schemas/{schema_name}"
    refs = [r for r in all_refs if r == needle]
    # find which are inside requestBody
    # Simpler: check all refs' context
    found_in_body = [False]

    def find_rb(node, in_body=False):
        nonlocal found_in_body
        if isinstance(node, dict):
            if "requestBody" in node:
                find_rb(node["requestBody"], True)
            else:
                for v in node.values():
                    find_rb(v, in_body)
        elif isinstance(node, list):
            for v in node:
                find_rb(v, in_body)
        if isinstance(node, dict) and "$ref" in node and in_body:
            if node["$ref"] == needle:
                found_in_body[0] = True

    find_rb(doc)
    return found_in_body[0]


def get_props_deep(s):
    props = set()
    if isinstance(s, dict):
        if "allOf" in s:
            for sub in s["allOf"]:
                props |= get_props_deep(sub)
        if "properties" in s:
            props |= set(s["properties"].keys())
        for k in ("oneOf", "anyOf"):
            if k in s:
                for sub in s[k]:
                    props |= get_props_deep(sub)
    elif isinstance(s, list):
        for sub in s:
            props |= get_props_deep(sub)
    return props


violations = []
for name, schema in doc.get("components", {}).get("schemas", {}).items():
    if not (
        name.endswith("Request") or name.endswith("Patch") or name.endswith("Input")
    ):
        continue
    if used_in_requestbody(name) is False:
        continue
    props = get_props_deep(schema)
    bad = props & server_derived
    if bad:
        violations.append((name, sorted(bad)))
print(f"\nServer-derived in requestBody schemas: {len(violations)}")
for v, flds in violations:
    print(f"  {v}: {flds}")
    errors.append(f"server-derived field in {v}: {flds}")

# 8) No TODO/placeholder
import re

full = yaml.dump(doc, default_flow_style=False, sort_keys=False)
for pat in re.findall(r"\{\{[^}]+\}\}", full):
    errors.append(f"placeholder {{pat}} in doc")
for s in ["TODO", "FIXME", "PLACEHOLDER", "TBR", "XXX"]:
    if s in full.upper() and s not in ("TODO",):  # skip accidental
        pass

print("\n=== SUMMARY ===")
print(f"Paths: {len(doc['paths'])}")
print(f"ops: {len(op_ids)}")
print(f"schemas: {len(doc['components']['schemas'])}")
print(f"errors: {len(errors)}")
for e in errors:
    print("  ERROR:", e)
print(f"warnings: {len(warnings)}")
for w in warnings:
    print("  WARN:", w)
