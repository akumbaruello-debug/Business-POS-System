"""Validate the OpenAPI 3.1 spec with openapi_spec_validator."""
import sys
from openapi_spec_validator import validate, OpenAPIV31SpecValidator
import yaml

try:
    with open("openapi.yaml", "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    # Iterate all errors instead of raising
    v = OpenAPIV31SpecValidator(spec)
    errs = list(v.iter_errors())
    if not errs:
        print("PASS: OpenAPI 3.1 valid (openapi_spec_validator)")
    else:
        print(f"FAIL: {len(errs)} validation error(s)")
        for i, e in enumerate(errs[:40]):
            path = "/".join(str(x) for x in e.absolute_path) if e.absolute_path else ""
            print(f"  [{i+1}] {path}: {e.message}")
except Exception as ex:
    print("EXCEPTION:", ex)
