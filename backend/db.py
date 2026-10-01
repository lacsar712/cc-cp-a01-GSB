import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS slot_reservations (
    id serial PRIMARY KEY,
    slot_no text NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    lit boolean NOT NULL DEFAULT true,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    extinguished_at timestamptz,
    extinguished_by text,
    CONSTRAINT chk_slot_window CHECK (ends_at > starts_at)
);
-- 同一舱号至多只能有一条亮舱：重复预约必须先熄后约
CREATE UNIQUE INDEX IF NOT EXISTS uq_slot_lit
    ON slot_reservations (slot_no) WHERE lit;

CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    slot_no text,
    reservation_id integer REFERENCES slot_reservations (id),
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);
-- 一笔预约至多挂一笔单：挂单后该舱即非“空选”，撞车由数据库兜底拒收。
-- reservation_id 为 NULL 的旧种子单据不受唯一约束影响（PG 中 NULL 互不相等）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_reading_reservation
    ON probe_readings (reservation_id);

-- 兼容既有库：补列
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS slot_no text;
ALTER TABLE probe_readings
    ADD COLUMN IF NOT EXISTS reservation_id integer REFERENCES slot_reservations (id);
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=2, max_size=8)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2),
            ("探头B02", 12.5),
        ]
        for probe_id, temp_c in samples:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, 'done', 'logger', now())
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2),
        ("探头B02", 12.5),
    ]
    for probe_id, temp_c in samples:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, 'done', 'logger', now())
            """,
            (probe_id, temp_c, verdict, reason),
        )
    conn.commit()
