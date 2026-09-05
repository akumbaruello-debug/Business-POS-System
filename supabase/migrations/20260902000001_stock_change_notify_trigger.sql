-- m0002: Add pg_notify('stock_change', ...) trigger on stock_movements.
-- Per Backend-Architecture §21.2 + Backend-Implementation-Plan §5 E.10.
--
-- Every committed INSERT into stock_movements fires a NOTIFY on the
-- 'stock_change' channel with a JSON payload containing the product_id
-- and the new movement's id.  The E.10 asyncio worker LISTENs on this
-- channel and evaluates whether the affected product crossed a
-- low-stock or out-of-stock threshold.
--
-- The NOTIFY is emitted by the trigger function, which runs AFTER
-- INSERT.  PostgreSQL guarantees that NOTIFY payloads are only
-- delivered to listeners after the emitting transaction commits —
-- rolled-back INSERTs never produce observable notifications.

CREATE OR REPLACE FUNCTION fn_stock_change_notify() RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify(
        'stock_change',
        json_build_object(
            'product_id', NEW.product_id,
            'movement_id', NEW.id
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_stock_change_notify
AFTER INSERT ON stock_movements
FOR EACH ROW EXECUTE FUNCTION fn_stock_change_notify();

COMMENT ON FUNCTION fn_stock_change_notify() IS 'E.10: emit pg_notify on stock_change channel after every stock_movements INSERT. Payload: {"product_id": N, "movement_id": N}.';
