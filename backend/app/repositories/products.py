"""Data-access layer for the ``products`` table.

Per DB spec §2.4 + Database-Design §3.1 (locked schema, schema.sql §5.3):

* Columns: ``id SERIAL PK``, ``code VARCHAR(64) NULL`` (pattern-validated in app;
  partial UNIQUE via ``ux_products_code``), ``name VARCHAR(200) NOT NULL``,
  ``category_id INT NULL FK -> categories(id) ON DELETE RESTRICT``,
  ``unit_id INT NULL FK -> units(id) ON DELETE RESTRICT``,
  ``purchase_price NUMERIC(15,2) NOT NULL DEFAULT 0 CHECK (>= 0)``,
  ``selling_price NUMERIC(15,2) NOT NULL DEFAULT 0 CHECK (>= 0)``,
  ``low_stock_threshold NUMERIC(15,4) NULL CHECK (>= 0)``,
  ``allow_negative_stock BOOLEAN NULL`` (NULL = use system default),
  ``is_sellable/is_purchasable/is_producible/is_active BOOLEAN NOT NULL DEFAULT TRUE``,
  ``notes TEXT NULL``, ``created_by/updated_by INT NOT NULL FK -> users(id)``,
  ``created_at``/``updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()``.
* Triggers: ``trg_products_touch_updated`` (BEFORE UPDATE on products).
* FK constraints:
  - ``products.category_id`` -> ``categories(id)`` ON DELETE RESTRICT
  - ``products.unit_id`` -> ``units(id)`` ON DELETE RESTRICT
  - ``products.created_by`` / ``products.updated_by`` -> ``users(id)`` ON DELETE RESTRICT
* Partial unique index ``ux_products_code``: ``UNIQUE (code) WHERE code IS NOT NULL``.
  Multiple NULL codes allowed; when present, unique across active+inactive.

Derived/computed fields are NOT stored on this table:
  - ``on_hand_quantity`` (from ``product_valuation`` view, LEFT JOIN stock_movements)
  - ``moving_average_unit_cost`` (computed in app per inventory.valuation)
  - ``low_stock`` (computed: on_hand <= threshold when threshold is set)

NOTE: ``created_by`` and ``updated_by`` are NOT NULL on the products table —
the service layer MUST always supply a valid user id (the authenticated
principal's id, or 0/owner). There is no anonymous creation.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork

# Columns returned by every SELECT/RETURNING in this repository.
# Derived fields (on_hand_quantity, low_stock, etc.) live on the
# ``product_valuation`` view and are composed by the service layer.
_COLS = (
    "id, name, code, category_id, unit_id, purchase_price, "
    "selling_price, low_stock_threshold, allow_negative_stock, "
    "notes, is_sellable, is_purchasable, is_producible, is_active, "
    "created_at, updated_at"
)


class ProductRepository:
    """Raw-SQL repository for ``products``. All UoW-scoped."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
        category_id: int | None = None,
        unit_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List products with pagination, search, and optional filters.

        Dynamic fragments use closed-whitelist literals; user data is parameterized.
        """

        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": per_page,
            "offset": (page - 1) * per_page,
        }

        if active_only is True:
            clauses.append("is_active = TRUE")
        elif active_only is False:
            clauses.append("is_active = FALSE")
        if category_id is not None:
            clauses.append("category_id = :category_id")
            params["category_id"] = category_id
        if unit_id is not None:
            clauses.append("unit_id = :unit_id")
            params["unit_id"] = unit_id
        if q:
            clauses.append("(name ILIKE :q OR code ILIKE :q)")
            params["q"] = f"%{q}%"

        # Allowlist for sort — never interpolate raw user input into ORDER BY.
        sort_map = {
            "id": "id",
            "name": "name",
            "code": "code",
            "purchase_price": "purchase_price",
            "selling_price": "selling_price",
            "created_at": "created_at",
            "updated_at": "updated_at",
        }
        order = sort_map.get(sort, "id")

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM products {where}",  # noqa: S608 — closed whitelist literals only
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT {_COLS}
            FROM products
            {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist: `where` from fixed literals, `order` from sort_map allowlist
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get a single product by id, or None if not found."""
        return await self._uow.first_row(
            f"SELECT {_COLS} FROM products WHERE id = :id",  # noqa: S608
            {"id": id},
        )

    async def get_by_code(self, code: str) -> dict[str, Any] | None:
        """Look up by code (for uniqueness check — code is nullable + partial unique)."""
        return await self._uow.first_row(
            f"SELECT {_COLS} FROM products WHERE code = :code",  # noqa: S608
            {"code": code},
        )

    async def create(
        self,
        *,
        name: str,
        code: str | None,
        category_id: int | None,
        unit_id: int | None,
        purchase_price: float,
        selling_price: float,
        low_stock_threshold: float | None,
        allow_negative_stock: bool | None,
        notes: str | None,
        is_sellable: bool,
        is_purchasable: bool,
        is_producible: bool,
        is_active: bool,
        created_by: int,
        updated_by: int,
    ) -> dict[str, Any]:
        """Insert a new product and return the new row.

        ``created_by`` / ``updated_by`` are NOT NULL on the DB — callers
        must always supply a valid user id.
        """
        row = await self._uow.first_row(
            f"""
            INSERT INTO products (
                code, name, category_id, unit_id, purchase_price,
                selling_price, low_stock_threshold, allow_negative_stock,
                notes, is_sellable, is_purchasable, is_producible, is_active,
                created_by, updated_by
            ) VALUES (
                :code, :name, :category_id, :unit_id, :purchase_price,
                :selling_price, :low_stock_threshold, :allow_negative_stock,
                :notes, :is_sellable, :is_purchasable, :is_producible,
                :is_active, :created_by, :updated_by
            )
            RETURNING {_COLS}
            """,  # noqa: S608
            {
                "code": code,
                "name": name,
                "category_id": category_id,
                "unit_id": unit_id,
                "purchase_price": purchase_price,
                "selling_price": selling_price,
                "low_stock_threshold": low_stock_threshold,
                "allow_negative_stock": allow_negative_stock,
                "notes": notes,
                "is_sellable": is_sellable,
                "is_purchasable": is_purchasable,
                "is_producible": is_producible,
                "is_active": is_active,
                "created_by": created_by,
                "updated_by": updated_by,
            },
        )
        if row is None:
            raise RuntimeError("Product insert returned no row")
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        category_id: int | None = None,
        unit_id: int | None = None,
        purchase_price: float | None = None,
        selling_price: float | None = None,
        low_stock_threshold: float | None = None,
        allow_negative_stock: bool | None = None,
        notes: str | None = None,
        is_sellable: bool | None = None,
        is_purchasable: bool | None = None,
        is_producible: bool | None = None,
        is_active: bool | None = None,
        updated_by: int | None = None,
    ) -> dict[str, Any] | None:
        """Update provided fields. ``code`` is NOT accepted (immutable via API).

        Returns the updated row or None if not found. SET clause uses hardcoded
        column literals only — user data parameterized.
        """

        fields: list[str] = []
        params: dict[str, Any] = {"id": id}
        if name is not None:
            fields.append("name = :name")
            params["name"] = name
        if category_id is not None:
            fields.append("category_id = :category_id")
            params["category_id"] = category_id
        if unit_id is not None:
            fields.append("unit_id = :unit_id")
            params["unit_id"] = unit_id
        if purchase_price is not None:
            fields.append("purchase_price = :purchase_price")
            params["purchase_price"] = purchase_price
        if selling_price is not None:
            fields.append("selling_price = :selling_price")
            params["selling_price"] = selling_price
        if low_stock_threshold is not None:
            fields.append("low_stock_threshold = :low_stock_threshold")
            params["low_stock_threshold"] = low_stock_threshold
        if allow_negative_stock is not None:
            fields.append("allow_negative_stock = :allow_negative_stock")
            params["allow_negative_stock"] = allow_negative_stock
        if notes is not None:
            fields.append("notes = :notes")
            params["notes"] = notes
        if is_sellable is not None:
            fields.append("is_sellable = :is_sellable")
            params["is_sellable"] = is_sellable
        if is_purchasable is not None:
            fields.append("is_purchasable = :is_purchasable")
            params["is_purchasable"] = is_purchasable
        if is_producible is not None:
            fields.append("is_producible = :is_producible")
            params["is_producible"] = is_producible
        if is_active is not None:
            fields.append("is_active = :is_active")
            params["is_active"] = is_active
        if updated_by is not None:
            fields.append("updated_by = :updated_by")
            params["updated_by"] = updated_by

        if not fields:
            return await self.get(id)

        fields.append("updated_at = NOW()")

        row = await self._uow.first_row(
            f"""
            UPDATE products
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING {_COLS}
            """,  # noqa: S608 — SET fields are hardcoded whitelist literals, user data parameterized
            params,
        )
        return row

    async def deactivate(self, id: int, *, updated_by: int | None = None) -> dict[str, Any] | None:
        """Set is_active=FALSE; return the updated row or None if not found.

        ``updated_by`` is set so audit can track who deactivated.
        """
        params: dict[str, Any] = {"id": id}
        set_clause = "is_active = FALSE, updated_at = NOW()"
        if updated_by is not None:
            set_clause += ", updated_by = :updated_by"
            params["updated_by"] = updated_by
        row = await self._uow.first_row(
            f"""
            UPDATE products
            SET {set_clause}
            WHERE id = :id
            RETURNING {_COLS}
            """,  # noqa: S608 — set_clause is a closed-whitelist literal
            params,
        )
        return row

    async def hard_delete(self, id: int) -> bool:
        """Hard-delete a product by id. Returns True if a row was deleted.

        The DB FK constraints (``products.category_id``, ``products.unit_id``
        ON DELETE RESTRICT plus references from sales/purchases) may raise
        a FK violation — the caller (service) translates that to
        ``referenced_by_history``.
        """
        result = await self._uow.execute(
            "DELETE FROM products WHERE id = :id",
            {"id": id},
        )
        return getattr(result, "rowcount", 0) > 0


__all__ = ["ProductRepository"]
