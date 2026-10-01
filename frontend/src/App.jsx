import { useCallback, useEffect, useState } from "preact/hooks";

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

function fmtLocal(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(
    d.getHours()
  )}:${p(d.getMinutes())}`;
}

// datetime-local 输入框值：按浏览器本地时区。
function datetimeLocalValue(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(
    d.getHours()
  )}:${p(d.getMinutes())}`;
}

async function apiJson(path, { token, method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
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
  const [route, setRoute] = useState(() => window.location.hash || "#/");

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash || "#/");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }

  if (!token) {
    return <Login onLogin={(t, u) => { setToken(t); setUser(u); }} />;
  }

  const isWriter = user?.role === "writer";
  const isReservationsPage = route.startsWith("#/reservations");

  return (
    <div class={isReservationsPage ? "wrap page-full" : "wrap"}>
      <div class="topbar">
        <div class="brand">
          <h1>冷链舱位温度台</h1>
          <p class="sub">先预约点亮舱位，再选亮舱提交温度；舱号随单冻结。</p>
        </div>
        <div class="user">
          <nav class="nav">
            <a
              href="#/"
              class={isReservationsPage ? "navlink" : "navlink active"}
            >
              温度总览
            </a>
            <a
              href="#/reservations"
              class={isReservationsPage ? "navlink active" : "navlink"}
            >
              舱位预约
            </a>
          </nav>
          <span>
            {user?.username}（{isWriter ? "记录员" : "值班员"}）
          </span>
          <button type="button" class="secondary" onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {isReservationsPage ? (
        <ReservationsPage token={token} isWriter={isWriter} />
      ) : (
        <DeskPage token={token} isWriter={isWriter} />
      )}
    </div>
  );
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
      const { ok, data } = await apiJson("/api/auth/login", {
        method: "POST",
        body: loginForm,
      });
      if (!ok) {
        setError(data.detail || "用户名或密码错误");
        return;
      }
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
      <h1>冷链舱位温度台</h1>
      <p class="sub">记录员先预约舱位点亮，再选亮舱提交摄氏温度；值班员只读。</p>
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
          记录员 logger / log123456 · 记录员 logger2 / log2123456 · 值班员
          watcher / watch123456
        </p>
      </div>
    </div>
  );
}

function DeskPage({ token, isWriter }) {
  const [rows, setRows] = useState([]);
  const [reservations, setReservations] = useState([]);
  const [reservationId, setReservationId] = useState("");
  const [tempC, setTempC] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const loadAll = useCallback(async () => {
    const [r1, r2] = await Promise.all([
      apiJson("/api/readings", { token }),
      apiJson("/api/reservations", { token }),
    ]);
    if (r1.ok) setRows(r1.data);
    if (r2.ok) setReservations(r2.data);
  }, [token]);

  useEffect(() => {
    loadAll();
    const t = setInterval(loadAll, 3000);
    return () => clearInterval(t);
  }, [loadAll]);

  // 亮灭完全以后端 lit 字段为准，前端不自判。
  const litCabins = reservations.filter((r) => r.lit);
  const selectedStillLit = litCabins.some((r) => String(r.id) === String(reservationId));

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    if (!reservationId) {
      setError("必须点选一个仍亮着的舱号：未选舱号，整笔退回");
      return;
    }
    if (!selectedStillLit) {
      setError("所选舱号已熄灭，本笔整笔退回，未入队");
      return;
    }
    setLoading(true);
    try {
      const { ok, data } = await apiJson("/api/readings", {
        token,
        method: "POST",
        body: { reservation_id: Number(reservationId), temp_c: parseFloat(tempC) },
      });
      if (!ok) {
        setError(data.detail || "提交失败，整笔退回");
        return;
      }
      setMsg(data.message || "已入队候审");
      setReservationId("");
      setTempC("");
      await loadAll();
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交舱温</h2>
          <form onSubmit={onSubmit}>
            <div class="field-block">
              <span class="field-label">
                舱号（点选仍亮着的舱位；点到已熄舱整笔退回）
              </span>
              {litCabins.length === 0 && (
                <p class="err" style={{ margin: "0.35rem 0" }}>
                  当前没有亮舱，请先到顶栏「舱位预约」登记并点亮舱位。
                </p>
              )}
              <div class="cabin-grid">
                {litCabins.map((r) => (
                  <label
                    key={r.id}
                    class={
                      String(reservationId) === String(r.id)
                        ? "cabin lit selected"
                        : "cabin lit"
                    }
                  >
                    <input
                      type="radio"
                      name="reservation_id"
                      value={r.id}
                      checked={String(reservationId) === String(r.id)}
                      onChange={() => setReservationId(r.id)}
                    />
                    <span class="cabin-no">{r.cabin_no}</span>
                    <span class="cabin-meta">
                      {fmtLocal(r.starts_at)} – {fmtLocal(r.ends_at)}
                      {r.bound_reading_id ? ` · 已挂单#${r.bound_reading_id}` : ""}
                    </span>
                  </label>
                ))}
              </div>
              {reservationId && !selectedStillLit && (
                <p class="err" style={{ marginBottom: 0 }}>
                  该舱刚熄灭，不能提交，本笔整笔退回。
                </p>
              )}
            </div>
            <div class="row" style={{ marginTop: "0.75rem" }}>
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
                提交入队候审
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      )}

      <div class="card">
        <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>温度单流水</h2>
        <table>
          <thead>
            <tr>
              <th>单号</th>
              <th>舱号（随单冻结）</th>
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
                <td>
                  <strong>{r.cabin_no || r.probe_id}</strong>
                </td>
                <td>{r.temp_c}</td>
                <td>
                  <span class={verdictClass(r.verdict, r.status)}>
                    {displayVerdict(r)}
                  </span>
                </td>
                <td>{r.reason || "—"}</td>
                <td>{r.status === "done" ? "已办结" : r.status === "pending" ? "候审" : r.status}</td>
                <td>{r.created_by}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colspan="7">暂无数据</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ReservationsPage({ token, isWriter }) {
  const [reservations, setReservations] = useState([]);
  const now = new Date();
  const [form, setForm] = useState({
    cabin_no: "",
    starts_at: datetimeLocalValue(now),
    ends_at: datetimeLocalValue(new Date(now.getTime() + 2 * 3600 * 1000)),
  });
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    const r = await apiJson("/api/reservations", { token });
    if (r.ok) setReservations(r.data);
  }, [token]);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  const lit = reservations.filter((r) => r.lit);
  const out = reservations.filter((r) => !r.lit);

  async function onRegister(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const { ok, data } = await apiJson("/api/reservations", {
        token,
        method: "POST",
        body: form,
      });
      if (!ok) {
        setError(data.detail || "登记失败");
        return;
      }
      setMsg(`舱位「${data.cabin_no}」已登记并点亮`);
      setForm({ ...form, cabin_no: "" });
      await load();
    } finally {
      setLoading(false);
    }
  }

  async function onExtinguish(r) {
    setError("");
    setMsg("");
    const { ok, data } = await apiJson(`/api/reservations/${r.id}/extinguish`, {
      token,
      method: "POST",
    });
    if (!ok) {
      setError(data.detail || "熄舱失败");
      return;
    }
    setMsg(`舱位「${data.cabin_no}」已熄灭`);
    await load();
  }

  return (
    <div class="landing">
      {isWriter ? (
        <div class="card register-card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>登记舱位预约</h2>
          <form onSubmit={onRegister}>
            <div class="row">
              <label>
                舱号
                <input
                  required
                  value={form.cabin_no}
                  placeholder="例如 舱甲零一"
                  onInput={(e) => setForm({ ...form, cabin_no: e.target.value })}
                />
              </label>
              <label>
                预约开始
                <input
                  required
                  type="datetime-local"
                  value={form.starts_at}
                  onInput={(e) => setForm({ ...form, starts_at: e.target.value })}
                />
              </label>
              <label>
                预约结束
                <input
                  required
                  type="datetime-local"
                  value={form.ends_at}
                  onInput={(e) => setForm({ ...form, ends_at: e.target.value })}
                />
              </label>
              <button type="submit" disabled={loading}>
                登记并点亮
              </button>
            </div>
            {error && <p class="err">{error}</p>}
            {msg && <p class="ok">{msg}</p>}
          </form>
        </div>
      ) : (
        <div class="card readonly-note">
          值班员只读：可翻阅预约表与冻结舱号，不能登记或熄舱，也不能提交温度。
        </div>
      )}

      <div class="split">
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
            亮舱表（{lit.length}）
          </h2>
          <table>
            <thead>
              <tr>
                <th>舱号</th>
                <th>预约时段</th>
                <th>登记人</th>
                <th>挂单</th>
                {isWriter && <th>操作</th>}
              </tr>
            </thead>
            <tbody>
              {lit.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span class="lamp on"></span>
                    <strong>{r.cabin_no}</strong>
                  </td>
                  <td>
                    {fmtLocal(r.starts_at)} – {fmtLocal(r.ends_at)}
                  </td>
                  <td>{r.created_by}</td>
                  <td>{r.bound_reading_id ? `#${r.bound_reading_id}（舱号已冻）` : "未挂单"}</td>
                  {isWriter && (
                    <td>
                      <button type="button" class="danger" onClick={() => onExtinguish(r)}>
                        熄舱
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {lit.length === 0 && (
                <tr>
                  <td colspan={isWriter ? 5 : 4}>当前没有亮舱</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
            熄舱流水（{out.length}）
          </h2>
          <table>
            <thead>
              <tr>
                <th>舱号</th>
                <th>灭灯原因</th>
                <th>灭灯时间</th>
                <th>操作人</th>
                <th>原预约时段</th>
              </tr>
            </thead>
            <tbody>
              {out.map((r) => (
                <tr key={r.id}>
                  <td>
                    <span class="lamp off"></span>
                    <strong>{r.cabin_no}</strong>
                  </td>
                  <td>{r.status === "out" ? "人工熄舱" : "时段到期自动灭"}</td>
                  <td>
                    {r.status === "out" ? fmtLocal(r.extinguished_at) : fmtLocal(r.ends_at)}
                  </td>
                  <td>{r.status === "out" ? r.extinguished_by || "—" : "系统"}</td>
                  <td>
                    {fmtLocal(r.starts_at)} – {fmtLocal(r.ends_at)}
                  </td>
                </tr>
              ))}
              {out.length === 0 && (
                <tr>
                  <td colspan="5">暂无熄舱记录</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
