import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import DONE, LIT, judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 幂等迁移：旧库（A01 基线）也能平滑升到舱位预约结构。
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cabin_reservations (
    id serial PRIMARY KEY,
    cabin_no text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'lit',
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    extinguished_at timestamptz,
    extinguished_by text
);

CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    reservation_id integer REFERENCES cabin_reservations(id),
    cabin_no text
);

ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS reservation_id integer
    REFERENCES cabin_reservations(id);
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS cabin_no text;

CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

-- 每笔亮舱预约至多挂一单；撞单时第二条 INSERT 在此唯一索引上失败（舱号已随单冻结）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_reading_one_per_lit_reservation
    ON probe_readings (reservation_id)
    WHERE reservation_id IS NOT NULL;
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=10)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


SEED_RESERVATION = ("舱甲零一", 2)  # 舱号, 预约时长（小时）
SEED_READINGS = [
    ("探头A01", 4.2),
    ("探头B02", 12.5),
]


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM cabin_reservations")
        if not n:
            cabin_no, hours = SEED_RESERVATION
            await conn.execute(
                """
                INSERT INTO cabin_reservations
                    (cabin_no, status, starts_at, ends_at, created_by, created_at)
                VALUES ($1, $2, now() - interval '1 hour', now() + make_interval(hours => $3),
                        'logger', now() - interval '1 hour')
                """,
                cabin_no,
                LIT,
                hours,
            )
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        for probe_id, temp_c in SEED_READINGS:
            verdict, reason = judge_temp(temp_c)
            await conn.execute(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, $5, 'logger', now())
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
                DONE,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM cabin_reservations").fetchone()
    if not row["n"]:
        cabin_no, hours = SEED_RESERVATION
        conn.execute(
            """
            INSERT INTO cabin_reservations
                (cabin_no, status, starts_at, ends_at, created_by, created_at)
            VALUES (%s, %s, now() - interval '1 hour', now() + %s * interval '1 hour',
                    'logger', now() - interval '1 hour')
            """,
            (cabin_no, LIT, hours),
        )
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        conn.commit()
        return
    for probe_id, temp_c in SEED_READINGS:
        verdict, reason = judge_temp(temp_c)
        conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, %s, 'logger', now())
            """,
            (probe_id, temp_c, verdict, reason, DONE),
        )
    conn.commit()
