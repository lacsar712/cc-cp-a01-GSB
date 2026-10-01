"""冷链探头读数判定与舱位亮灭口径。

亮灭判定只有本文件 ``is_reservation_lit`` 一个事实源：
预约落地页亮舱表、提交温度时的入库校验、熄舱流水分类全部走它，
前端不自行判定，只消费后端按本函数算出的 ``lit`` 字段。
"""

# 舱位预约状态
LIT = "lit"  # 已预约点亮：在预约时段内且未被熄舱，可写温度
OUT = "out"  # 已人工熄舱：不可再写，已入队旧单的舱号不受影响

# 温度单状态
PENDING = "pending"
PROCESSING = "processing"
DONE = "done"


def judge_temp(temp_c: float) -> tuple[str, str]:
    """摄氏温度不超过 8 为合格，否则超温。"""
    if temp_c <= 8:
        return "合格", "探头温度未超过 8℃ 上限"
    return "超温", "探头温度超过 8℃ 冷链上限"


def is_reservation_lit(status: str, starts_at, ends_at, now) -> bool:
    """亮舱唯一判定口径：状态为亮，且当前时间落在半开预约时段 [starts_at, ends_at) 内。"""
    if status != LIT:
        return False
    if starts_at is not None and now < starts_at:
        return False
    if ends_at is not None and now >= ends_at:
        return False
    return True


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == PENDING:
        return "待处理"
    if status == PROCESSING:
        return "处理中"
    return "—"
