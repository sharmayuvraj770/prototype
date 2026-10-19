"""One-shot init job: keys, migrations, reference data, synthetic data."""
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from seedgen.clear import clear_synthetic
from seedgen.fixtures import SEED_VERSION
from seedgen.generate import generate
from seedgen.keys import ensure_keys
from seedgen.migrate import MIGRATIONS_DIR, apply_migrations
from seedgen.reference import SEED_DIR, load_reference

__all__ = ["clear_synthetic", "main", "run_init", "wait_for_db"]
STALE_AFTER = timedelta(hours=12)


def wait_for_db(
    url: str,
    *,
    attempts: int = 30,
    delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Retry connecting until Postgres accepts connections (first-boot init can lag the
    healthcheck). Re-raises the last OperationalError after `attempts` tries."""
    for attempt in range(1, attempts + 1):
        try:
            with psycopg.connect(url):
                return
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            sleep(delay)


def run_init(
    *, database_url: str, seed_dir: Path, keys_dir: Path, demo_now: datetime, force: bool = False
) -> str:
    """Seed if missing or SEED_VERSION changed; regenerate synthetic data if older than
    STALE_AFTER or if `force` (reference data is reloaded only on a version change)."""
    ensure_keys(keys_dir)
    apply_migrations(database_url, MIGRATIONS_DIR)
    with psycopg.connect(database_url) as conn:  # autocommit=False; commits on clean exit
        meta = conn.execute("select seed_version, demo_now from tms.seed_meta where id = 1").fetchone()
        if meta is None or meta[0] != SEED_VERSION:
            clear_synthetic(conn)  # load_reference needs synthetic rows gone
            load_reference(conn, seed_dir)
            generate(conn, demo_now=demo_now)
            outcome = "seeded"
        elif force or meta[1] < demo_now - STALE_AFTER:
            generate(conn, demo_now=demo_now)  # clears its own synthetic rows via clear_synthetic
            outcome = "reseeded"
        else:
            return "skipped"
        conn.execute(
            "insert into tms.seed_meta (id, seed_version, demo_now, seeded_at) values (1, %s, %s, now()) "
            "on conflict (id) do update set seed_version = excluded.seed_version, "
            "demo_now = excluded.demo_now, seeded_at = excluded.seeded_at",
            (SEED_VERSION, demo_now),
        )
    return outcome


def _demo_now() -> datetime:
    raw = os.environ.get("DEMO_NOW", "now")
    if raw == "now":
        return datetime.now(UTC).replace(second=0, microsecond=0)
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def main() -> None:
    try:
        url = os.environ["DATABASE_URL"]
        demo_now = _demo_now()
        wait_for_db(url)
        outcome = run_init(
            database_url=url,
            seed_dir=Path(os.environ.get("SEED_DIR") or SEED_DIR),
            keys_dir=Path(os.environ.get("KEYS_DIR", "/keys")),
            demo_now=demo_now,
            force=os.environ.get("FORCE_RESEED") == "1",
        )
        print(f"init: {outcome} (demo_now={demo_now.isoformat()})")
    except Exception as e:  # noqa: BLE001
        print(f"init failed: {e!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
