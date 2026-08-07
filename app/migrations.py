"""Tiny additive-only schema fixups, run at startup after Base.metadata.create_all().

create_all() only creates missing tables — it never alters an existing table
to add a new column. Since there's no Alembic set up here, new nullable
columns get added this way instead: safe (never touches existing rows),
idempotent (checks what's already there first), and works the same on
SQLite (dev) and Postgres (prod).
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# table -> {column_name: SQL type}, additive only.
_ADDITIVE_COLUMNS = {
    "niche_templates": {
        "service_description": "TEXT",
        "problem_domain": "TEXT",
        "collect_list": "TEXT",
    },
    "tenants": {
        "domain": "TEXT",
        "target_keyword": "TEXT",
    },
}


def run_additive_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            for column_name, sql_type in columns.items():
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column_name} {sql_type}"))
