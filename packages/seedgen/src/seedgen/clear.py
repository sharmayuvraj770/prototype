"""Single source of truth for removing synthetic rows."""
import psycopg

# FK-safe order; reference tables and real carriers are left alone.
CLEAR_STATEMENTS = [
    "delete from tms.quotes",
    "delete from tms.api_idempotency",
    "delete from tms.load_events", "delete from tms.loads", "delete from tms.capacity_posts",
    "delete from tms.tenant_carriers", "delete from tms.carrier_compliance",
    "delete from tms.carriers where is_fictional", "delete from tms.users", "delete from tms.tenants",
]


def clear_synthetic(conn: psycopg.Connection) -> None:
    for stmt in CLEAR_STATEMENTS:
        conn.execute(stmt)
