import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import judge_temp, slot_writable

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可操作（值班员只读）"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def _json_error(klass, detail: str):
    return klass(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


def _parse_dt(value, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise _json_error(web.HTTPBadRequest, f"{field}不能为空")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise _json_error(web.HTTPBadRequest, f"{field}时间格式无法识别：{value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def reading_out(r) -> dict:
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "slot_no": r["slot_no"],
        "reservation_id": r["reservation_id"],
        "temp_c": r["temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


def slot_out(r) -> dict:
    return {
        "id": r["id"],
        "slot_no": r["slot_no"],
        "starts_at": r["starts_at"].isoformat() if r["starts_at"] else None,
        "ends_at": r["ends_at"].isoformat() if r["ends_at"] else None,
        "lit": r["lit"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "extinguished_at": r["extinguished_at"].isoformat() if r["extinguished_at"] else None,
        "extinguished_by": r["extinguished_by"],
        "reading_id": r["reading_id"],
        "reading_status": r["reading_status"],
    }


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, slot_no, reservation_id, temp_c, verdict, reason,
               status, created_by, created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    return web.json_response([reading_out(r) for r in rows])


async def create_reading(request: web.Request) -> web.Response:
    """挂舱入队：锁亮舱 → 同口径校验 → 舱号随单原子落库，全程一个事务。"""
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise _json_error(web.HTTPBadRequest, "探头编号不能为空")
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise _json_error(web.HTTPBadRequest, "温度必须是数字") from exc
    try:
        reservation_id = int(body.get("reservation_id"))
    except (TypeError, ValueError) as exc:
        raise _json_error(web.HTTPBadRequest, "必须点选一个已点亮的舱位，未选舱位不能提交温度") from exc

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        # 整个“锁舱 → 判定 → 舱号随单落库”在一个事务里原子完成，
        # 不存在先入队后改/补舱号的半截单。
        async with conn.transaction():
            slot = await conn.fetchrow(
                """
                SELECT id, slot_no, lit, starts_at, ends_at
                FROM slot_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not slot:
                raise _json_error(
                    web.HTTPBadRequest,
                    "未找到该舱位预约：请先在舱位预约页登记舱号并点亮，再提交温度",
                )

            ok, why = slot_writable(
                lit=slot["lit"],
                slot_no=slot["slot_no"],
                starts_at=slot["starts_at"],
                ends_at=slot["ends_at"],
            )
            if not ok:
                raise _json_error(web.HTTPUnprocessableEntity, why)

            occupied = await conn.fetchval(
                "SELECT id FROM probe_readings WHERE reservation_id = $1",
                reservation_id,
            )
            if occupied is not None:
                raise _json_error(
                    web.HTTPConflict,
                    f"舱位「{slot['slot_no']}」已挂在途/已结单据 #{occupied}，"
                    "不是空选舱位，本笔温度当场退回",
                )

            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO probe_readings
                        (probe_id, slot_no, reservation_id, temp_c, status, created_by, created_at)
                    VALUES ($1, $2, $3, $4, 'pending', $5, now())
                    RETURNING id, probe_id, slot_no, reservation_id, temp_c, verdict, reason,
                              status, created_by, created_at, processed_at
                    """,
                    probe_id,
                    slot["slot_no"],
                    slot["id"],
                    temp_c,
                    user["username"],
                )
            except asyncpg.UniqueViolationError as exc:
                # 并发撞车兜底：两名记录员同挂一亮舱，至多一笔入队
                raise _json_error(
                    web.HTTPConflict,
                    f"舱位「{slot['slot_no']}」刚被另一笔单据占用，本笔撞车当场退回",
                ) from exc

    out = reading_out(row)
    out["message"] = f"已挂亮舱「{out['slot_no']}」入队候审，舱号已随单冻结"
    return web.json_response(out, status=201)


async def list_slots(request: web.Request) -> web.Response:
    """预约表：亮舱表与熄舱流水一起返回，前端分页签展示。"""
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT s.id, s.slot_no, s.starts_at, s.ends_at, s.lit, s.created_by,
               s.created_at, s.extinguished_at, s.extinguished_by,
               r.id AS reading_id, r.status AS reading_status
        FROM slot_reservations s
        LEFT JOIN probe_readings r ON r.reservation_id = s.id
        ORDER BY s.id DESC
        """
    )
    return web.json_response([slot_out(r) for r in rows])


async def create_slot(request: web.Request) -> web.Response:
    """登记舱号与预约时段并点亮；同舱已有亮舱时拒绝（须先熄后约）。"""
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    slot_no = str(body.get("slot_no", "")).strip()
    if not slot_no:
        raise _json_error(web.HTTPBadRequest, "舱号不能为空")
    starts_at = _parse_dt(body.get("starts_at"), "预约开始时间")
    ends_at = _parse_dt(body.get("ends_at"), "预约结束时间")
    if ends_at <= starts_at:
        raise _json_error(web.HTTPBadRequest, "预约结束时间必须晚于开始时间")

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                "SELECT id FROM slot_reservations WHERE slot_no = $1 AND lit FOR UPDATE",
                slot_no,
            )
            if existing is not None:
                raise _json_error(
                    web.HTTPConflict,
                    f"舱位「{slot_no}」当前仍亮着（预约 #{existing}），请先熄灭后再重新登记",
                )
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO slot_reservations
                        (slot_no, starts_at, ends_at, lit, created_by, created_at)
                    VALUES ($1, $2, $3, true, $4, now())
                    RETURNING id, slot_no, starts_at, ends_at, lit, created_by,
                              created_at, extinguished_at, extinguished_by
                    """,
                    slot_no,
                    starts_at,
                    ends_at,
                    user["username"],
                )
            except asyncpg.UniqueViolationError as exc:
                raise _json_error(
                    web.HTTPConflict,
                    f"舱位「{slot_no}」已有一条亮舱预约，请先熄灭后再登记",
                ) from exc

    out = slot_out({**dict(row), "reading_id": None, "reading_status": None})
    out["message"] = f"舱位「{slot_no}」已点亮，可写时段 {_fmt_short(starts_at)} 至 {_fmt_short(ends_at)}"
    return web.json_response(out, status=201)


async def extinguish_slot(request: web.Request) -> web.Response:
    """熄舱：只动预约表，绝不回写已落档单据的舱号。"""
    user = require_writer(request)
    slot_id = int(request.match_info["id"])
    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, slot_no, lit
                FROM slot_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                slot_id,
            )
            if not row:
                raise _json_error(web.HTTPNotFound, "未找到该舱位预约")
            if not row["lit"]:
                raise _json_error(
                    web.HTTPConflict, f"舱位「{row['slot_no']}」已经熄灭，无需重复操作"
                )
            await conn.execute(
                """
                UPDATE slot_reservations
                SET lit = false, extinguished_at = now(), extinguished_by = $2
                WHERE id = $1
                """,
                slot_id,
                user["username"],
            )
    return web.json_response({"message": f"舱位「{row['slot_no']}」已熄灭，新提交将被整笔退回；旧单舱号保持冻结"})


def _fmt_short(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_get("/api/slots", list_slots)
    app.router.add_post("/api/slots", create_slot)
    app.router.add_post(r"/api/slots/{id:\d+}/extinguish", extinguish_slot)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
