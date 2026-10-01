import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import LIT, OUT, PENDING, is_reservation_lit, judge_temp

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "logger2": {"role": "writer", "password_hash": pwd.hash("log2123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _json_error(status: int, detail: str) -> web.HTTPException:
    cls = {
        400: web.HTTPBadRequest,
        401: web.HTTPUnauthorized,
        403: web.HTTPForbidden,
        404: web.HTTPNotFound,
        409: web.HTTPConflict,
    }[status]
    return cls(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


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
        raise _json_error(401, "未登录")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise _json_error(403, "值班员只读：不能登记/熄舱，也不能提交温度")
    return user


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
        raise _json_error(401, "用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


def _parse_dt(value, field: str):
    if value is None or str(value).strip() == "":
        raise _json_error(400, f"{field}不能为空")
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise _json_error(400, f"{field}时间格式无效") from exc
    if dt.tzinfo is None:
        # 页面 datetime-local 不带时区，按本地时区解释后统一存 UTC。
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(timezone.utc)


def _reservation_out(r, now, bound_reading_id=None) -> dict:
    lit = is_reservation_lit(r["status"], r["starts_at"], r["ends_at"], now)
    return {
        "id": r["id"],
        "cabin_no": r["cabin_no"],
        "status": r["status"],
        "lit": lit,
        "starts_at": r["starts_at"].isoformat(),
        "ends_at": r["ends_at"].isoformat(),
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "extinguished_at": r["extinguished_at"].isoformat() if r["extinguished_at"] else None,
        "extinguished_by": r["extinguished_by"],
        "bound_reading_id": bound_reading_id,
    }


async def list_reservations(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT r.*, pr.id AS bound_reading_id
        FROM cabin_reservations r
        LEFT JOIN probe_readings pr ON pr.reservation_id = r.id
        ORDER BY (r.status = 'lit') DESC, r.id DESC
        """
    )
    now = datetime.now(timezone.utc)
    return web.json_response([_reservation_out(r, now) for r in rows])


async def create_reservation(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    cabin_no = str(body.get("cabin_no", "")).strip()
    if not cabin_no:
        raise _json_error(400, "舱号不能为空")
    starts_at = _parse_dt(body.get("starts_at"), "预约开始")
    ends_at = _parse_dt(body.get("ends_at"), "预约结束")
    if ends_at <= starts_at:
        raise _json_error(400, "预约结束时间必须晚于开始时间")

    pool: asyncpg.Pool = request.app["pool"]
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO cabin_reservations (cabin_no, status, starts_at, ends_at, created_by)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            cabin_no,
            LIT,
            starts_at,
            ends_at,
            user["username"],
        )
    except asyncpg.UniqueViolationError as exc:
        raise _json_error(409, f"舱号「{cabin_no}」已有预约，不能重复登记") from exc
    now = datetime.now(timezone.utc)
    return web.json_response(_reservation_out(row, now), status=201)


async def extinguish_reservation(request: web.Request) -> web.Response:
    user = require_writer(request)
    reservation_id = int(request.match_info["id"])
    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        UPDATE cabin_reservations
        SET status = $1, extinguished_at = now(), extinguished_by = $2
        WHERE id = $3
        RETURNING *
        """,
        OUT,
        user["username"],
        reservation_id,
    )
    if not row:
        raise _json_error(404, "预约不存在")
    bound = await pool.fetchval(
        "SELECT id FROM probe_readings WHERE reservation_id = $1", reservation_id
    )
    now = datetime.now(timezone.utc)
    return web.json_response(_reservation_out(row, now, bound))


def _reading_out(r) -> dict:
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "cabin_no": r["cabin_no"],
        "reservation_id": r["reservation_id"],
        "temp_c": r["temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


READING_COLS = (
    "id, probe_id, cabin_no, reservation_id, temp_c, verdict, reason, "
    "status, created_by, created_at, processed_at"
)


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(f"SELECT {READING_COLS} FROM probe_readings ORDER BY id DESC")
    return web.json_response([_reading_out(r) for r in rows])


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc

    if body.get("reservation_id") in (None, ""):
        raise _json_error(400, "必须点选仍亮着的舱号：未选舱号，整笔退回")
    try:
        reservation_id = int(body.get("reservation_id"))
    except (TypeError, ValueError) as exc:
        raise _json_error(400, "舱号选择无效，整笔退回") from exc
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise _json_error(400, "温度必须是数字，整笔退回") from exc

    pool: asyncpg.Pool = request.app["pool"]
    # 入队与舱号冻结在同一事务内一次落库：
    # 行锁把同舱并发撞单串行化，唯一索引兜底，外部看不到“可改舱号的半截单”。
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(
                """
                SELECT id, cabin_no, status, starts_at, ends_at
                FROM cabin_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not r:
                raise _json_error(400, "所选舱号不存在，整笔退回")
            now = datetime.now(timezone.utc)
            if not is_reservation_lit(r["status"], r["starts_at"], r["ends_at"], now):
                if r["status"] == OUT:
                    raise _json_error(
                        400,
                        f"舱号「{r['cabin_no']}」已熄舱，不能写温度，整笔退回（本笔未入队）",
                    )
                raise _json_error(
                    400,
                    f"舱号「{r['cabin_no']}」当前不在预约时段内、未点亮，整笔退回（本笔未入队）",
                )
            existing = await conn.fetchval(
                "SELECT id FROM probe_readings WHERE reservation_id = $1",
                reservation_id,
            )
            if existing:
                raise _json_error(
                    409,
                    f"亮舱「{r['cabin_no']}」已挂温度单（#{existing}），撞单当场拒收，本笔未入队",
                )
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO probe_readings
                        (probe_id, cabin_no, reservation_id, temp_c,
                         status, created_by, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, now())
                    RETURNING {READING_COLS}
                    """,
                    r["cabin_no"],
                    r["cabin_no"],
                    r["id"],
                    temp_c,
                    PENDING,
                    user["username"],
                )
            except asyncpg.UniqueViolationError as exc:
                raise _json_error(
                    409,
                    f"亮舱「{r['cabin_no']}」已被另一笔单抢先挂走，撞单当场拒收，本笔未入队",
                ) from exc

    return web.json_response(
        {
            **_reading_out(row),
            "message": f"已按亮舱「{row['cabin_no']}」入队候审，舱号随单冻住",
        },
        status=201,
    )


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
    app.router.add_get("/api/reservations", list_reservations)
    app.router.add_post("/api/reservations", create_reservation)
    app.router.add_post("/api/reservations/{id:\\d+}/extinguish", extinguish_reservation)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
