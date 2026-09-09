// AI 项目需求管家 —— 前端逻辑
const state = {
  project: null,
  draft: { requirements: [], questions: [] },
  changes: [],
  decisions: {},   // change_index -> { decision, revised_after }
  lastRunId: null, // 最近一次“分析新沟通”的 analysis_run_id
  changesDemoMode: false,
  authMode: "login", // login | register
};

const $ = (id) => document.getElementById(id);

function getToken() {
  return localStorage.getItem("token") || "";
}
function setToken(t) {
  if (t) localStorage.setItem("token", t);
  else localStorage.removeItem("token");
}

const api = async (path, opts = {}) => {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch(path, {
    headers,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    // 会话失效，回到登录页
    setToken("");
    show("auth");
    toast(data.detail || "请先登录", "error");
    throw Object.assign(new Error(data.detail || "未登录"), { status: 401 });
  }
  if (!res.ok) {
    const err = new Error(data.detail || "请求失败");
    err.status = res.status;
    throw err;
  }
  return data;
};

function toast(msg, kind = "") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.hidden = true), 3500);
}

function intentLabel(intent) {
  return { requested: "提出", hypothetical: "设想", excluded: "排除", unclear: "待澄清" }[intent] || intent;
}

function kindLabel(kind) {
  return {
    added: "新增", modified: "修改", removed: "删除",
    clarification: "澄清", unchanged: "未变", uncertain: "待定",
  }[kind] || kind;
}

function show(view) {
  $("view-auth").hidden = view !== "auth";
  $("view-list").hidden = view !== "list";
  $("view-workbench").hidden = view !== "workbench";
}

// ---------------- 登录 / 注册 ----------------
function updateAuthUI() {
  $("auth-title").textContent = state.authMode === "login" ? "登录" : "注册";
  $("btn-auth-submit").textContent = state.authMode === "login" ? "登录" : "注册";
  $("btn-auth-toggle").textContent = state.authMode === "login" ? "没有账号？注册" : "已有账号？登录";
}

async function submitAuth() {
  const username = $("auth-username").value.trim();
  const password = $("auth-password").value;
  if (!username || !password) return toast("请输入用户名和密码", "error");
  const path = state.authMode === "login" ? "/api/auth/login" : "/api/auth/register";
  try {
    const r = await api(path, { method: "POST", body: { username, password } });
    setToken(r.token);
    $("auth-password").value = "";
    $("auth-msg").textContent = "";
    await afterLogin();
  } catch (e) {
    $("auth-msg").textContent = e.message;
  }
}

async function afterLogin() {
  try {
    const me = await api("/api/auth/me");
    $("user-name").textContent = me.user.username;
    $("user-box").hidden = false;
  } catch (e) { /* 忽略 */ }
  show("list");
  await loadProjects();
}

function logout() {
  const token = getToken();
  if (token) api("/api/auth/logout", { method: "POST" }).catch(() => {});
  setToken("");
  $("user-box").hidden = true;
  show("auth");
}

// ---------------- 项目列表 ----------------
async function loadProjects() {
  const { projects } = await api("/api/projects");
  const el = $("project-list");
  el.innerHTML = "";
  $("project-empty").hidden = projects.length > 0;
  projects.forEach((p) => {
    const div = document.createElement("div");
    div.className = "project-item";
    div.innerHTML = `
      <div>
        <div class="p-name">${escapeHtml(p.name)}</div>
        <div class="p-meta">最新版本：${p.current_version_seq ? "V" + p.current_version_seq : "无"} · 最近修改：${fmtTime(p.updated_at)}</div>
      </div>`;
    div.onclick = () => openProject(p.id);
    el.appendChild(div);
  });
}

async function newProject() {
  const name = prompt("请输入项目名称：");
  if (!name || !name.trim()) return;
  await api("/api/projects", { method: "POST", body: { name } });
  toast("项目已创建");
  await loadProjects();
}

async function openProject(id) {
  state.project = await api(`/api/projects/${id}`);
  state.changes = [];
  state.decisions = {};
  state.lastRunId = null;
  state.changesDemoMode = false;
  $("wb-project-name").textContent = state.project.name;
  show("workbench");
  renderWorkbench();
}

// ---------------- 工作台 ----------------
function renderWorkbench() {
  const p = state.project;
  const latest = p.versions.length ? p.versions[p.versions.length - 1] : null;
  $("wb-current-version").textContent = "当前版本：" + (latest ? "V" + latest.seq : "无");
  state.draft = p.draft || { requirements: [], questions: [] };
  renderSources();
  renderDraft();
  renderChanges();
  renderVersions();
}

function renderSources() {
  const el = $("source-list");
  el.innerHTML = "";
  const p = state.project;
  p.sources.forEach((s) => {
    const chip = document.createElement("span");
    chip.className = "chip" + (s.is_demo ? " demo" : "");
    chip.textContent = `${s.id} · ${s.label}`;
    chip.title = "点击查看原文";
    chip.onclick = () => alert(`【${s.id}】${s.label}\n\n${s.text}`);
    el.appendChild(chip);
  });
}

async function addSource() {
  const text = $("source-input").value;
  const label = $("source-label").value;
  if (!text.trim()) return toast("请先粘贴材料内容", "error");
  const src = await api(`/api/projects/${state.project.id}/sources`, {
    method: "POST",
    body: { text, label },
  });
  state.project.sources.push(src);
  $("source-input").value = "";
  $("source-label").value = "";
  renderSources();
  toast(src.is_demo ? "已保存（示例材料）" : "已保存材料", src.is_demo ? "demo" : "");
}

function loadDemo() {
  $("source-input").value = DEMO_SRC_001;
  $("source-label").value = "SRC-001 初始沟通";
  toast("已填入示例材料 SRC-001，请点“保存材料”", "demo");
}

async function analyze() {
  const srcs = state.project.sources;
  if (!srcs.length) return toast("请先保存材料", "error");
  const body = { source_ids: srcs.map((s) => s.id) };
  const btn = $("btn-analyze");
  btn.disabled = true;
  btn.textContent = "处理中…";
  try {
    const r = await api(`/api/projects/${state.project.id}/analyze`, { method: "POST", body });
    if (r.demo_mode) {
      state.draft = { requirements: r.requirements, questions: r.questions, demo_mode: true };
      toast("已生成需求草稿（示例模式）", "demo");
    } else if (r.status === "ok") {
      state.draft = { requirements: r.requirements, questions: r.questions, demo_mode: false };
      toast(`已完成真实分析（模型：${r.model || "未知"}）`);
    } else {
      state.draft = { requirements: [], questions: [], demo_mode: false };
      toast(r.message, "error");
    }
    renderDraft();
  } finally {
    btn.disabled = false;
    btn.textContent = "整理需求";
  }
}

function renderDraft() {
  const el = $("candidate-list");
  el.innerHTML = "";
  const reqs = state.draft.requirements || [];
  $("draft-empty").hidden = reqs.length > 0;
  $("draft-editable-note").textContent = "草稿（可编辑）";

  reqs.forEach((c, i) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="c-title">
        <button class="delete-cand" data-i="${i}" title="删除">✕</button>
        ${escapeHtml(c.title)}
        <span class="intent-tag ${c.intent}">${intentLabel(c.intent)}</span>
        ${c.id ? `<span class="req-id">${escapeHtml(c.id)}</span>` : ""}
      </div>
      <textarea class="edit-field" data-i="${i}" data-field="description" rows="2">${escapeHtml(c.description || "")}</textarea>
      <div class="c-evidence">${evidenceHtml(c.evidence || [])}</div>
      <div class="c-questions">${(c.questions || []).map((q) => "❓ " + escapeHtml(q)).join("<br>")}</div>`;
    el.appendChild(card);
  });

  el.querySelectorAll(".edit-field").forEach((ta) => {
    ta.oninput = () => {
      const i = +ta.dataset.i;
      reqs[i][ta.dataset.field] = ta.value;
    };
  });
  el.querySelectorAll(".delete-cand").forEach((btn) => {
    btn.onclick = () => {
      reqs.splice(+btn.dataset.i, 1);
      renderDraft();
    };
  });

  if (state.draft.demo_mode) {
    const banner = document.createElement("div");
    banner.className = "demo-banner";
    banner.textContent = "⚠️ 演示模式：以下需求草稿来自内置示例材料，非真实模型分析结果。";
    el.prepend(banner);
  }
}

function evidenceHtml(evidence) {
  if (!evidence || !evidence.length) return "";
  return evidence
    .map(
      (e) => `<blockquote><span class="src-id">${escapeHtml(e.source_id)}</span>：「${escapeHtml(e.quote)}」</blockquote>`
    )
    .join("");
}

async function confirmVersion() {
  if (!(state.draft.requirements || []).length) return toast("草稿为空，请先整理需求", "error");
  try {
    const ver = await api(`/api/projects/${state.project.id}/confirm`, {
      method: "POST",
      body: { request_id: newRequestId() },
    });
    state.project = await api(`/api/projects/${state.project.id}`);
    toast(`已确认版本 V${ver.seq}（不可修改）`);
    renderWorkbench();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function compare() {
  const srcs = state.project.sources;
  if (!srcs.length) return toast("请先保存新材料", "error");
  const body = { source_ids: srcs.map((s) => s.id) };
  const btn = $("btn-compare");
  btn.disabled = true;
  btn.textContent = "处理中…";
  try {
    const r = await api(`/api/projects/${state.project.id}/compare`, { method: "POST", body });
    if (r.demo_mode) {
      state.changes = r.changes;
      state.changesDemoMode = true;
      state.decisions = {};
      state.lastRunId = r.run_id;
      toast("已生成变更候选（示例模式）", "demo");
    } else if (r.status === "ok") {
      state.changes = r.changes;
      state.changesDemoMode = false;
      state.decisions = {};
      state.lastRunId = r.run_id;
      toast(`已完成真实比较（模型：${r.model || "未知"}）`);
    } else {
      state.changes = [];
      state.changesDemoMode = false;
      state.lastRunId = r.run_id || null;
      toast(r.message, "error");
    }
    renderChanges();
  } finally {
    btn.disabled = false;
    btn.textContent = "分析新沟通";
  }
}

function renderChanges() {
  const el = $("change-list");
  el.innerHTML = "";
  const changes = state.changes || [];
  $("change-empty").hidden = changes.length > 0;
  $("apply-row").hidden = changes.length === 0;

  if (state.changesDemoMode) {
    const banner = document.createElement("div");
    banner.className = "demo-banner";
    banner.textContent = "⚠️ 演示模式：以下变更候选来自内置示例材料，非真实模型分析结果。";
    el.appendChild(banner);
  }

  changes.forEach((c) => {
    const idx = c.change_index;
    const dec = state.decisions[idx] || {};
    const card = document.createElement("div");
    card.className = "card change-card";
    card.innerHTML = `
      <div class="c-title">
        <span class="kind-tag ${c.kind}">${kindLabel(c.kind)}</span>
        ${c.related_requirement_ids && c.related_requirement_ids.length
          ? "<small>关联：" + escapeHtml(c.related_requirement_ids.join(", ")) + "</small>" : ""}
      </div>
      <div class="c-after">建议修改后：${escapeHtml(c.proposed_after)}</div>
      <div class="c-desc">说明：${escapeHtml(c.reason)}</div>
      <div class="c-evidence">${evidenceHtml(c.evidence || [])}</div>
      <div class="c-questions">${(c.questions || []).map((q) => "❓ " + escapeHtml(q)).join("<br>")}</div>
      <div class="decision-row">
        <button class="btn small ${dec.decision === "accept" ? "primary" : ""}" data-idx="${idx}" data-dec="accept">采纳</button>
        <button class="btn small ${dec.decision === "ignore" ? "primary" : ""}" data-idx="${idx}" data-dec="ignore">忽略</button>
        <input class="revise" data-idx="${idx}" placeholder="可选：人工修订内容" value="${escapeHtml(dec.revised_after || "")}" />
        <span style="font-size:13px;color:var(--muted)">${dec.decision ? "已选：" + (dec.decision === "accept" ? "采纳" : "忽略") : ""}</span>
      </div>`;
    el.appendChild(card);
  });

  el.querySelectorAll("button[data-dec]").forEach((b) => {
    b.onclick = () => {
      const idx = +b.dataset.idx;
      const cur = state.decisions[idx] || {};
      cur.decision = b.dataset.dec;
      state.decisions[idx] = cur;
      renderChanges();
    };
  });
  el.querySelectorAll("input.revise").forEach((inp) => {
    inp.oninput = () => {
      const idx = +inp.dataset.idx;
      const cur = state.decisions[idx] || {};
      cur.revised_after = inp.value;
      if (inp.value) cur.decision = "accept";
      state.decisions[idx] = cur;
    };
  });
}

async function applyChanges() {
  if (!state.lastRunId) return toast("请先“分析新沟通”", "error");
  const decisions = Object.entries(state.decisions)
    .map(([idx, d]) => ({ change_index: +idx, decision: d.decision || "ignore", revised_after: d.revised_after || "" }));
  if (!decisions.length) return toast("请至少选择一条变更", "error");
  if (!decisions.some((d) => d.decision === "accept")) return toast("请至少采纳一条变更", "error");
  try {
    const ver = await api(`/api/projects/${state.project.id}/apply`, {
      method: "POST",
      body: { run_id: state.lastRunId, request_id: newRequestId(), decisions },
    });
    state.project = await api(`/api/projects/${state.project.id}`);
    state.draft = state.project.draft;
    toast(`已根据采纳项生成新版本 V${ver.seq}`, "demo");
    renderWorkbench();
  } catch (e) {
    if (e.status === 409) toast("版本已变化，请重新“分析新沟通”", "error");
    else toast(e.message, "error");
  }
}

// ---------------- 历史版本 ----------------
function renderVersions() {
  const el = $("version-list");
  el.innerHTML = "";
  const versions = state.project.versions || [];
  $("version-empty").hidden = versions.length > 0;
  versions.forEach((v) => {
    const item = document.createElement("div");
    item.className = "version-item";
    const isCurrent = v.id === state.project.current_version_id;
    item.innerHTML = `
      <div class="v-left">
        <span class="badge version">V${v.seq}</span>
        ${isCurrent ? '<span class="badge demo">当前</span>' : ""}
        <span class="v-time">${fmtTime(v.confirmed_at)}</span>
        <span class="v-count">${v.snapshot.requirements.length} 条需求</span>
      </div>
      <div class="v-actions">
        <button class="btn small" data-vid="${v.id}" data-act="view">查看</button>
        <button class="btn small" data-vid="${v.id}" data-act="export">导出</button>
      </div>`;
    el.appendChild(item);
  });

  el.querySelectorAll("button[data-vid]").forEach((b) => {
    b.onclick = () => {
      const vid = b.dataset.vid;
      const act = b.dataset.act;
      if (act === "view") showVersion(vid);
      else if (act === "export") downloadExport(vid);
    };
  });
}

function showVersion(vid) {
  const v = (state.project.versions || []).find((x) => x.id === vid);
  if (!v) return;
  const lines = [`版本 V${v.seq}（确认于 ${fmtTime(v.confirmed_at)}，操作人：${v.confirmed_by}）`, ""];
  v.snapshot.requirements.forEach((r, i) => {
    lines.push(`${i + 1}. [${intentLabel(r.intent)}] ${r.title}${r.id ? "（" + r.id + "）" : ""}`);
    if (r.description) lines.push(`   ${r.description}`);
  });
  alert(lines.join("\n"));
}

async function downloadExport(vid) {
  const url = vid
    ? `/api/projects/${state.project.id}/export?version_id=${encodeURIComponent(vid)}`
    : `/api/projects/${state.project.id}/export`;
  const token = getToken();
  const res = await fetch(url, { headers: token ? { Authorization: "Bearer " + token } : {} });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    return toast(data.detail || "导出失败", "error");
  }
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `需求文档_V${latestSeqFor(vid)}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

function latestSeqFor(vid) {
  const v = (state.project.versions || []).find((x) => x.id === vid);
  return v ? v.seq : "latest";
}

// ---------------- 工具 ----------------
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function newRequestId() {
  return "req-" + Math.random().toString(36).slice(2) + "-" + Date.now().toString(36);
}

// 固定示例材料（前端“载入示例材料”按钮使用 SRC-001）
const DEMO_SRC_001 =
  "客户：做一个官网，展示我们的产品，让客户能联系我们。\n" +
  "开发者：第一版是首页、产品列表、产品详情和联系我们四类页面，对吗？\n" +
  "客户：对。先不做在线支付，也不做会员。\n" +
  "开发者：产品信息需要你们自己在后台维护吗？\n" +
  "客户：这个还没想好，我们下次确定。";

// ---------------- 事件绑定 ----------------
$("btn-new-project").onclick = newProject;
$("btn-back").onclick = () => { show("list"); loadProjects(); };
$("btn-load-demo").onclick = loadDemo;
$("btn-add-source").onclick = addSource;
$("btn-analyze").onclick = analyze;
$("btn-confirm").onclick = confirmVersion;
$("btn-compare").onclick = compare;
$("btn-apply").onclick = applyChanges;
$("btn-export").onclick = () => downloadExport(null);
$("btn-auth-submit").onclick = submitAuth;
$("btn-auth-toggle").onclick = () => { state.authMode = state.authMode === "login" ? "register" : "login"; updateAuthUI(); };
$("btn-logout").onclick = logout;
$("auth-username").addEventListener("keydown", (e) => { if (e.key === "Enter") $("auth-password").focus(); });
$("auth-password").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });

// ---------------- 启动 ----------------
async function initModelStatus() {
  const el = $("llm-status");
  try {
    const h = await api("/api/health");
    const cfg = h.llm || {};
    if (cfg.configured) {
      el.textContent = "模型已配置：" + (cfg.model || "未知");
      el.classList.add("ok");
    } else {
      el.textContent = "模型未配置（仅示例模式可用）";
      el.classList.add("warn");
    }
  } catch (e) {
    el.textContent = "无法检测模型状态";
    el.classList.add("warn");
  }
}

async function boot() {
  updateAuthUI();
  initModelStatus();
  const token = getToken();
  if (!token) {
    show("auth");
    return;
  }
  // 校验 token 是否有效
  try {
    await afterLogin();
  } catch (e) {
    show("auth");
  }
}

boot();
