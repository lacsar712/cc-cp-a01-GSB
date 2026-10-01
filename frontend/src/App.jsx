import { useCallback, useEffect, useMemo, useState } from "preact/hooks";
import { fmtUTC, slotState, slotWritable, isFreeSelect } from "./slotRule.js";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "候审";
  if (row.status === "processing") return "处理中";
  return "—";
}

function localInputValue(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(
    d.getHours()
  )}:${p(d.getMinutes())}`;
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [route, setRoute] = useState(
    () => (window.location.hash === "#/slots" ? "slots" : "overview")
  );

  useEffect(() => {
    const onHash = () =>
      setRoute(window.location.hash === "#/slots" ? "slots" : "overview");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (!token) return <Login onLogin={(t, u) => { setToken(t); setUser(u); }} />;

  const isWriter = user?.role === "writer";

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">冷柜舱位先预约点亮，点选仍亮着的空舱方可提交温度。</p>
        </div>
        <div class="user">
          <a
            class={`navlink ${route === "overview" ? "active" : ""}`}
            href="#/"
          >
            温度总览
          </a>
          <a
            class={`navlink ${route === "slots" ? "active" : ""}`}
            href="#/slots"
          >
            舱位预约
          </a>
          <span style="margin-left: .75rem">
            {user?.username}（{isWriter ? "记录员" : "值班员"}）
          </span>
          <button
            type="button"
            class="secondary"
            style={{ marginLeft: "0.5rem" }}
            onClick={() => {
              localStorage.removeItem(TOKEN_KEY);
              localStorage.removeItem(USER_KEY);
              setToken(null);
              setUser(null);
            }}
          >
            退出
          </button>
        </div>
      </div>

      {route === "slots" ? (
        <SlotsPage token={token} isWriter={isWriter} />
      ) : (
        <OverviewPage token={token} isWriter={isWriter} />
      )}
    </div>
  );
}

function useApi(token) {
  const authHeaders = useCallback(
    () => ({
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }),
    [token]
  );
  return authHeaders;
}

function Login({ onLogin }) {
  const [loginForm, setLoginForm] = useState({
    username: "logger",
    password: "log123456",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      onLogin(data.access_token, {
        username: data.username,
        role: data.role,
      });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div class="wrap">
      <h1>冷链探头超温台</h1>
      <p class="sub">记录员先预约舱位点亮，再提交舱内温度；值班员只读。</p>
      <div class="card">
        <form onSubmit={onSubmit}>
          <div class="row">
            <label>
              用户名
              <input
                value={loginForm.username}
                onInput={(e) =>
                  setLoginForm({ ...loginForm, username: e.target.value })
                }
              />
            </label>
            <label>
              密码
              <input
                type="password"
                value={loginForm.password}
                onInput={(e) =>
                  setLoginForm({ ...loginForm, password: e.target.value })
                }
              />
            </label>
            <button type="submit" disabled={loading}>
              登录
            </button>
          </div>
          {error && <p class="err">{error}</p>}
        </form>
        <p class="sub" style={{ marginBottom: 0 }}>
          记录员 logger / log123456 · 值班员 watcher / watch123456
        </p>
      </div>
    </div>
  );
}

function useSlots(token, paused) {
  const authHeaders = useApi(token);
  const [slots, setSlots] = useState([]);
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    const res = await fetch("/api/slots", { headers: authHeaders() });
    if (res.ok) setSlots(await res.json());
  }, [authHeaders]);

  useEffect(() => {
    if (paused) return undefined;
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load, paused]);

  // 本地每秒重算时段状态（到点自动变“时段已过”），口径仍在 slotRule
  useEffect(() => {
    if (paused) return undefined;
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [paused]);

  return { slots, reload: load, tick };
}

function useReadings(token) {
  const authHeaders = useApi(token);
  const [rows, setRows] = useState([]);
  const load = useCallback(async () => {
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (res.ok) setRows(await res.json());
  }, [authHeaders]);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  return { rows, reload: load };
}

function OverviewPage({ token, isWriter }) {
  const authHeaders = useApi(token);
  const { rows, reload } = useReadings(token);
  const { slots, reload: reloadSlots } = useSlots(token, false);
  const [probeId, setProbeId] = useState("");
  const [tempC, setTempC] = useState("");
  const [reservationId, setReservationId] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const freeSlots = useMemo(
    () => slots.filter((s) => isFreeSelect(s)),
    [slots]
  );
  const selected = slots.find((s) => s.id === reservationId) || null;

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");

    // 前端先按与后端同口径的 slotRule 拦一道；最终以后端事务内判定为准。
    if (reservationId == null) {
      setError("必须点选一个仍亮着的空舱，未选舱位不能提交温度（整笔未提交）");
      return;
    }
    if (!selected) {
      setError("所选舱位预约不存在，请改点当前亮舱表中的空舱（整笔退回）");
      setReservationId(null);
      return;
    }
    if (!isFreeSelect(selected)) {
      const w = slotWritable(selected);
      setError(
        selected.reading_id != null
          ? `舱位「${selected.slot_no}」已挂单据 #${selected.reading_id}，不是空选舱位（整笔退回）`
          : w.reason
      );
      setReservationId(null);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: probeId,
          temp_c: parseFloat(tempC),
          reservation_id: reservationId,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败，温度整笔退回");
        setReservationId(null);
        await reloadSlots();
        return;
      }
      setMsg(data.message || "已入队候审");
      setProbeId("");
      setTempC("");
      setReservationId(null);
      await Promise.all([reload(), reloadSlots()]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
          <p class="sub" style={{ margin: "0 0 .75rem" }}>
            必须点选一个<b>仍亮着的空舱</b>；点到已熄舱/非时段/已挂单舱，温度整笔退回。
          </p>

          <div class="slotpick">
            {freeSlots.length === 0 && (
              <span class="sub">
                当前没有可写的亮空舱，请先到
                <a href="#/slots" style={{ margin: "0 .25rem" }}>
                  舱位预约
                </a>
                登记并点亮
              </span>
            )}
            {freeSlots.map((s) => (
              <button
                type="button"
                key={s.id}
                class={`chip ${reservationId === s.id ? "on" : ""}`}
                onClick={() => {
                  setReservationId(s.id);
                  setError("");
                }}
                title={`${fmtUTC(new Date(s.starts_at))} ~ ${fmtUTC(
                  new Date(s.ends_at)
                )}`}
              >
                {s.slot_no}
                <small>
                  {new Date(s.ends_at).getUTCHours()}:
                  {String(new Date(s.ends_at).getUTCMinutes()).padStart(2, "0")}{" "}
                  UTC 止
                </small>
              </button>
            ))}
            {selected && !isFreeSelect(selected) && (
              <span class="err">
                {selected.reading_id != null
                  ? `舱位「${selected.slot_no}」已挂单据 #${selected.reading_id}`
                  : slotWritable(selected).reason}
              </span>
            )}
          </div>

          <form onSubmit={onSubmit} style={{ marginTop: ".75rem" }}>
            <div class="row">
              <label>
                探头编号
                <input
                  required
                  value={probeId}
                  onInput={(e) => setProbeId(e.target.value)}
                  placeholder="例如 探头C03"
                />
              </label>
              <label>
                温度（℃）
                <input
                  required
                  type="number"
                  step="0.1"
                  value={tempC}
                  onInput={(e) => setTempC(e.target.value)}
                />
              </label>
              <button type="submit" disabled={loading}>
                提交
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表（舱号随单冻结）</h2>
        <table>
          <thead>
            <tr>
              <th>编号</th>
              <th>舱号</th>
              <th>探头</th>
              <th>温度℃</th>
              <th>结论</th>
              <th>说明</th>
              <th>状态</th>
              <th>提交人</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.id}</td>
                <td>{r.slot_no || "—"}</td>
                <td>{r.probe_id}</td>
                <td>{r.temp_c}</td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>
                    {displayVerdict(r)}
                  </span>
                </td>
                <td>{r.reason || "—"}</td>
                <td>{r.status}</td>
                <td>{r.created_by}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colspan="8">暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SlotsPage({ token, isWriter }) {
  const authHeaders = useApi(token);
  const { slots, reload } = useSlots(token, false);
  const now = new Date();
  const [form, setForm] = useState(() => {
    const start = new Date();
    start.setMinutes(0, 0, 0);
    start.setHours(start.getHours() + 1);
    const end = new Date(start.getTime() + 2 * 3600 * 1000);
    return {
      slot_no: "",
      starts_at: localInputValue(start),
      ends_at: localInputValue(end),
    };
  });
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const lit = slots.filter((s) => s.lit);
  const off = slots.filter((s) => !s.lit);

  async function reserve(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    if (!form.slot_no.trim()) {
      setError("舱号不能为空");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/slots", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          slot_no: form.slot_no.trim(),
          starts_at: new Date(form.starts_at).toISOString(),
          ends_at: new Date(form.ends_at).toISOString(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "登记失败");
        return;
      }
      setMsg(data.message || "舱位已点亮");
      setForm({ ...form, slot_no: "" });
      await reload();
    } finally {
      setBusy(false);
    }
  }

  async function extinguish(s) {
    setError("");
    setMsg("");
    setBusy(true);
    try {
      const res = await fetch(`/api/slots/${s.id}/extinguish`, {
        method: "POST",
        headers: authHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "熄灭失败");
        return;
      }
      setMsg(data.message || "舱位已熄灭");
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="card slots-page">
      <h2 style={{ marginTop: 0, fontSize: "1.15rem" }}>舱位预约</h2>

      {isWriter ? (
        <form onSubmit={reserve} class="reserve-form">
          <div class="row">
            <label>
              舱号
              <input
                value={form.slot_no}
                onInput={(e) => setForm({ ...form, slot_no: e.target.value })}
                placeholder="例如 舱甲零一"
              />
            </label>
            <label>
              预约开始（本地时间）
              <input
                type="datetime-local"
                value={form.starts_at}
                onInput={(e) =>
                  setForm({ ...form, starts_at: e.target.value })
                }
              />
            </label>
            <label>
              预约结束（本地时间）
              <input
                type="datetime-local"
                value={form.ends_at}
                onInput={(e) => setForm({ ...form, ends_at: e.target.value })}
              />
            </label>
            <button type="submit" disabled={busy}>
              登记并点亮
            </button>
          </div>
          {error && <p class="err">{error}</p>}
          {msg && <p class="ok">{msg}</p>}
        </form>
      ) : (
        <p class="sub">值班员只读：可翻阅预约表与冻结舱号，不能改亮灭。</p>
      )}

      <h3 class="section-title">亮舱表</h3>
      <table>
        <thead>
          <tr>
            <th>舱号</th>
            <th>预约时段（UTC）</th>
            <th>状态</th>
            <th>挂单</th>
            <th>登记人</th>
            {isWriter && <th>操作</th>}
          </tr>
        </thead>
        <tbody>
          {lit.map((s) => {
            const st = slotState(s, now);
            return (
              <tr key={s.id}>
                <td>
                  <span class={`slotlamp ${st.key}`}>{s.slot_no}</span>
                </td>
                <td>
                  {fmtUTC(new Date(s.starts_at))} ～{" "}
                  {fmtUTC(new Date(s.ends_at))}
                </td>
                <td>
                  <span class={`tag lamp-${st.key}`}>{st.text}</span>
                </td>
                <td>{s.reading_id != null ? `#${s.reading_id}` : "空选"}</td>
                <td>{s.created_by}</td>
                {isWriter && (
                  <td>
                    <button
                      type="button"
                      class="secondary small"
                      disabled={busy}
                      onClick={() => extinguish(s)}
                    >
                      熄灭
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
          {lit.length === 0 && (
            <tr>
              <td colspan={isWriter ? 6 : 5}>暂无亮舱</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 class="section-title">熄舱流水</h3>
      <table>
        <thead>
          <tr>
            <th>舱号</th>
            <th>原预约时段（UTC）</th>
            <th>熄灭时间（UTC）</th>
            <th>熄灭人</th>
            <th>落档单据</th>
          </tr>
        </thead>
        <tbody>
          {off.map((s) => (
            <tr key={s.id}>
              <td>{s.slot_no}</td>
              <td>
                {fmtUTC(new Date(s.starts_at))} ～{" "}
                {fmtUTC(new Date(s.ends_at))}
              </td>
              <td>
                {s.extinguished_at
                  ? fmtUTC(new Date(s.extinguished_at))
                  : "—"}
              </td>
              <td>{s.extinguished_by || "—"}</td>
              <td>{s.reading_id != null ? `#${s.reading_id}（舱号已冻结）` : "无"}</td>
            </tr>
          ))}
          {off.length === 0 && (
            <tr>
              <td colspan="5">暂无熄舱记录</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
