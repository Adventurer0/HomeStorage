"""SQLite storage, kept on the NAS's local persistent data volume."""

import sqlite3
import secrets
from contextlib import closing
from contextlib import contextmanager
from pathlib import Path


DEFAULT_CATEGORIES = [
    "日常用品", "电子设备", "摄影器材", "工具耗材", "骑行用品", "健身用品",
    "商品库存", "食品", "药品", "其他",
]


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL,
    parent_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '其他',
    category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
    location_id INTEGER NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
    unit TEXT NOT NULL DEFAULT '件',
    image_url TEXT,
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    stored_date TEXT NOT NULL,
    purchase_date TEXT,
    purchase_price REAL CHECK (purchase_price >= 0),
    expires_on TEXT,
    warning_days INTEGER NOT NULL DEFAULT 7 CHECK (warning_days BETWEEN 0 AND 3650),
    status TEXT NOT NULL DEFAULT 'in_stock' CHECK (
        status IN ('in_stock', 'in_use', 'lent', 'depleted', 'discarded', 'lost')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,
    item_id INTEGER REFERENCES items(id) ON DELETE RESTRICT,
    item_name TEXT NOT NULL DEFAULT '',
    from_location TEXT,
    to_location TEXT,
    quantity_delta INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_location ON items(location_id);
CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE INDEX IF NOT EXISTS logs_item ON logs(item_id, id DESC);
"""


def connect(path: Path) -> sqlite3.Connection:
    # FastAPI may resume a sync dependency/endpoint in another pool thread.
    # Connections stay request-scoped; BEGIN IMMEDIATE serializes writers.
    connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize(path: Path) -> None:
    with closing(connect(path)) as connection:
        with connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.executescript(SCHEMA)
            migrate_location_ids(connection)
            migrate_categories_and_archive(connection)
            if not connection.execute("SELECT 1 FROM locations LIMIT 1").fetchone():
                connection.execute(
                    "INSERT INTO locations (name, type) VALUES (?, ?)", ("家", "home")
                )


def migrate_categories_and_archive(connection: sqlite3.Connection) -> None:
    """Add stable category identities and archive state without rebuilding item data."""
    with transaction(connection):
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
        if "category_id" not in columns:
            connection.execute(
                "ALTER TABLE items ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE RESTRICT"
            )
        if "archived_at" not in columns:
            connection.execute("ALTER TABLE items ADD COLUMN archived_at TEXT")
        if not connection.execute(
            "SELECT 1 FROM schema_migrations WHERE name='categories_archive_v2'"
        ).fetchone():
            for index, name in enumerate(DEFAULT_CATEGORIES):
                connection.execute(
                    "INSERT OR IGNORE INTO categories(name,sort_order) VALUES (?,?)", (name, index)
                )
            connection.execute("INSERT INTO schema_migrations(name) VALUES ('categories_archive_v2')")
        # Also recover unassigned legacy rows from supported backups. Existing IDs,
        # statuses, quantities, timestamps, photos and audit records stay untouched.
        legacy = connection.execute(
            "SELECT DISTINCT category FROM items WHERE category_id IS NULL ORDER BY category"
        ).fetchall()
        for row in legacy:
            name = row["category"]
            connection.execute(
                "INSERT OR IGNORE INTO categories(name,sort_order) VALUES (?, "
                "(SELECT COALESCE(MAX(sort_order),-1)+1 FROM categories))", (name,)
            )
            category_id = connection.execute("SELECT id FROM categories WHERE name=?", (name,)).fetchone()[0]
            connection.execute("UPDATE items SET category_id=? WHERE category_id IS NULL AND category=?", (category_id, name))
        connection.execute("CREATE INDEX IF NOT EXISTS items_category ON items(category_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS items_archived ON items(archived_at)")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("物品数据库迁移失败：已有数据存在无效关联")


def migrate_location_ids(connection: sqlite3.Connection) -> None:
    """Preserve old data while preventing a deleted box's QR ID being reassigned."""
    definition = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='locations'"
    ).fetchone()[0]
    if "AUTOINCREMENT" in definition.upper():
        return
    # Bootstrap home was the only creation without a log. User-created location
    # logs provide a conservative upper bound on IDs used by the old allocator,
    # including a deleted highest ID no longer present in the locations table.
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        with transaction(connection):
            high_water = max(
                connection.execute("SELECT COALESCE(MAX(id),0) FROM locations").fetchone()[0],
                connection.execute("SELECT COUNT(*) + 1 FROM logs WHERE action='location_create'").fetchone()[0],
            )
            connection.execute(
                "CREATE TABLE locations_next (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, code TEXT NOT NULL DEFAULT '', type TEXT NOT NULL, "
                "parent_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT)"
            )
            connection.execute("INSERT INTO locations_next SELECT id,name,code,type,parent_id FROM locations")
            connection.execute("DROP TABLE locations")
            connection.execute("ALTER TABLE locations_next RENAME TO locations")
            connection.execute("DELETE FROM sqlite_sequence WHERE name='locations'")
            connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('locations',?)", (high_water,))
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("位置数据库迁移失败：已有数据存在无效关联")
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


@contextmanager
def transaction(connection: sqlite3.Connection):
    """Nest via savepoints so a confirmed AI batch and its used marker are atomic."""
    if connection.in_transaction:
        savepoint = "nested_" + secrets.token_hex(8)
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except BaseException:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
