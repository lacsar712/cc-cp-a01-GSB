// 舱位可写判定：与 backend/rules.py 的 slot_writable 逐条对应，
// 亮灭表提交校验与落库共用同一口径，改判定必须两边一起改。

function pad2(n) {
  return String(n).padStart(2, "0");
}

export function fmtUTC(d) {
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(
    d.getUTCDate()
  )} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`;
}

// 返回 { writable: boolean, reason: string }
export function slotWritable(slot, now = new Date()) {
  const starts = new Date(slot.starts_at);
  const ends = new Date(slot.ends_at);
  const no = slot.slot_no;
  if (!slot.lit) {
    return {
      writable: false,
      reason: `舱位「${no}」已熄灭，温度整笔退回：须先重新预约并点亮`,
    };
  }
  if (now < starts) {
    return {
      writable: false,
      reason: `舱位「${no}」未到预约开始时间（${fmtUTC(starts)}），温度整笔退回`,
    };
  }
  if (now >= ends) {
    return {
      writable: false,
      reason: `舱位「${no}」预约时段已于 ${fmtUTC(ends)} 结束，温度整笔退回`,
    };
  }
  return { writable: true, reason: "" };
}

// 可点选的“仍亮着的空选”：灯亮、在时段内、且未挂任何单据
export function isFreeSelect(slot, now = new Date()) {
  return slotWritable(slot, now).writable && slot.reading_id == null;
}

export function slotState(slot, now = new Date()) {
  if (!slot.lit) return { key: "off", text: "已熄灭" };
  if (slot.reading_id != null) {
    return {
      key: "busy",
      text: `已挂单 #${slot.reading_id}`,
    };
  }
  const w = slotWritable(slot, now);
  if (w.writable) return { key: "writable", text: "可写" };
  if (now < new Date(slot.starts_at)) return { key: "soon", text: "未到时段" };
  return { key: "expired", text: "时段已过" };
}
