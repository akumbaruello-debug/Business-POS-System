# Business-POS V3 starter data templates

These are **editable starter/reference CSVs** prepared from the planning conversation. They are not proof that the current application supports CSV import, and they are not a command to insert rows into production. The agent must inspect the current APIs and master-data model before mapping them to any importer or seed process.

## Files

- `units.csv`: kg, kuintal, ton, and buah. `dimension` prevents mixing mass and count. `factor_to_base` is meaningful only within the same dimension. Suggested mass normalization: kg is base; kuintal = 100 kg; ton = 1,000 kg. `buah` is count-based and cannot be converted to kilograms without a specific, approved product-level rule.
- `production_cost_types.csv`: examples for the existing Settings → Cost Types concept, which was previously observed to serve production cost lines. Confirm current model and existing values before import.
- `purchase_landed_cost_types.csv`: suggested purchase-cost categories. `default_capitalize_to_inventory` is a proposal, not a universal accounting instruction. In particular, general operating expenses must not be capitalized automatically. Preserve current V1 allocation behavior until the owner approves a policy change.
- `product_attribute_values.csv`: example Jenis/Size/Kualitas options. `Layang`, `Kecil`, `Sedang`, `Besar`, `PP`, and `Mco` are examples and require owner review. Do not create every possible attribute combination by default.

## Safe setup/import requirements

1. Let the owner edit/remove rows before use.
2. Preview rows and validation errors before applying anything.
3. Detect duplicates and make repeated imports idempotent.
4. Permit hard deletion only when the value is unreferenced. If used by products or historical records, block hard deletion and offer deactivation/archive while preserving historical labels.
5. Import only after an explicit owner action. Never auto-import these rows during application startup or on every deployment.
6. Never use direct production SQL to bulk-add these starter values.
7. Confirm permission checks on both frontend and backend.

The CSV columns are planning/template columns, not guaranteed API field names. The implementer must document the safe mapping to the actual current schema or provide a manual-entry checklist if an importer is not warranted.
