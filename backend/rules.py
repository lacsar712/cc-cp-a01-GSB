"""冷链探头读数判定：摄氏温度不超过 8 为合格，否则超温。"""

from datetime import datetime, timezone


def judge_temp(temp_c: float) -> tuple[str, str]:
    if temp_c <= 8:
        return "合格", "探头温度未超过 8℃ 上限"
    return "超温", "探头温度超过 8℃ 冷链上限"


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "候审"
    if status == "processing":
        return "处理中"
    return "—"


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def slot_writable(
    *,
    lit: bool,
    slot_no: str,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """舱位可否写入温度的唯一判定口径。

    可写 = 灯仍亮 且 当前时刻落在预约时段内。
    前端 frontend/src/slotRule.js 必须与本函数逐条对应，不得各判各的。

    返回 (是否可写, 退回原因)。
    """
    now = now or datetime.now(timezone.utc)
    if not lit:
        return False, f"舱位「{slot_no}」已熄灭，温度整笔退回：须先重新预约并点亮"
    if now < starts_at:
        return False, (
            f"舱位「{slot_no}」未到预约开始时间（{_fmt(starts_at)}），温度整笔退回"
        )
    if now >= ends_at:
        return False, (
            f"舱位「{slot_no}」预约时段已于 {_fmt(ends_at)} 结束，温度整笔退回"
        )
    return True, ""
