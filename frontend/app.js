const $ = (id) => document.getElementById(id);
const clone = (value) => JSON.parse(JSON.stringify(value));
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const intents = {
  requested: "提出",
  excluded: "排除",
  hypothetical: "设想",
  unclear: "待澄清",
};
const kinds = {
  added: "新增",
  modified: "修改",
  removed: "删除",
  clarification: "澄清",
  unchanged: "未变",
  uncertain: "待定",
};
const sourceTypes = {
  chat: "聊天记录",
  meeting: "会议纪要",
  email: "邮件",
  other: "其他",
};
const state = {
  project: null,
  draft: null,
  user: null,
  selected: new Set(),
  review: null,
  decisions: {},
  dirty: false,
  busy: false,
  authMode: "login",
  pendingConfirm: null,
  pendingApply: null,
  tab: "overview",
  requirementMode: "draft",
  projects: [],
};
// 会话只保存在本标签页，迁移旧版本时清除长期存储的令牌。
localStorage.removeItem("token");
const getToken = () => sessionStorage.getItem("token") || "";
const setToken = (token) =>
  token
    ? sessionStorage.setItem("token", token)
    : sessionStorage.removeItem("token");
const readonly = () => Boolean(state.project?.read_only);
const latest = () => state.project?.versions.at(-1);
const requestId = () => crypto.randomUUID();
const fmtTime = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const url = (suffix) => `/api/projects/${state.project.id}${suffix}`;

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  if (getToken()) headers.Authorization = "Bearer " + getToken();
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new Error("网络连接失败，内容仍保留在当前页面，请重试");
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    setToken("");
    state.user = null;
    $("user-box").hidden = true;
    show("auth");
  }
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((e) => e.msg).join("；")
      : data.detail;
    throw Object.assign(new Error(detail || "请求失败，请稍后重试"), {
      status: response.status,
    });
  }
  return data;
}

function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").className = "toast" + (error ? " error" : "");
  $("toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(
    () => ($("toast").hidden = true),
    error ? 7000 : 4000,
  );
}

function show(view) {
  ["auth", "list", "workbench"].forEach(
    (name) => ($("view-" + name).hidden = name !== view),
  );
  const inApp = view !== "auth";
  document.body.classList.toggle("is-app", inApp);
  $("sidebar").hidden = !inApp;
  $("breadcrumb").hidden = !inApp;
  $("auth-brand").hidden = inApp;
  $("project-nav").hidden = view !== "workbench";
  $("nav-projects").classList.toggle("active", view === "list");
  if (view !== "workbench") {
    $("crumb-project").textContent = "项目管理";
    $("crumb-divider").hidden = true;
    $("crumb-page").textContent = "";
  }
}

async function run(action, button) {
  if (state.busy) return;
  state.busy = true;
  $("action-feedback").hidden = true;
  const content = button?.innerHTML;
  if (button) {
    button.disabled = true;
    button.textContent = "处理中…";
  }
  if (state.project) syncButtons();
  try {
    await action();
  } catch (e) {
    toast(e.message, true);
    if (state.project && !$("view-workbench").hidden) {
      $("action-feedback").textContent = e.message;
      $("action-feedback").hidden = false;
    } else $("auth-msg").textContent = e.message;
  } finally {
    state.busy = false;
    if (button) {
      button.innerHTML = content;
      button.disabled = false;
    }
    if (state.project && !$("view-workbench").hidden) syncButtons();
  }
}

function modal(title, html) {
  if ($("modal").open) $("modal").close();
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = html;
  $("modal").showModal();
}
$("modal-close").onclick = () => $("modal").close();

function ask(title, message, label = "继续") {
  return new Promise((resolve) => {
    modal(
      title,
      `<p>${esc(message)}</p><div class="row"><button class="btn primary" id="modal-yes">${esc(label)}</button><button class="btn" id="modal-no">取消</button></div>`,
    );
    let answer = false;
    $("modal-yes").onclick = () => {
      answer = true;
      $("modal").close();
    };
    $("modal-no").onclick = () => $("modal").close();
    $("modal").addEventListener("close", () => resolve(answer), { once: true });
  });
}

async function guardLeave() {
  if (
    !state.dirty &&
    !Object.keys(state.decisions).length &&
    !$("source-input").value.trim()
  )
    return true;
  return ask(
    "还有未提交的审核内容",
    "离开会丢失未保存的草稿或变更选择。你可以取消并先保存。",
    "离开",
  );
}

function markDirty() {
  state.dirty = true;
  state.pendingConfirm = null;
  $("draft-editable-note").textContent = "有未保存修改";
  $("wb-draft-badge").textContent = "未保存";
  $("draft-save-status").textContent = "有未保存修改";
}

function updateAuthUI() {
  const register = state.authMode === "register";
  $("auth-title").textContent = register ? "创建工作空间账号" : "登录工作空间";
  $("btn-auth-submit").textContent = register ? "注册并登录" : "登录";
  $("btn-auth-toggle").textContent = register
    ? "已有账号？返回登录"
    : "创建工作空间账号";
  $("auth-password").autocomplete = register
    ? "new-password"
    : "current-password";
  $("auth-msg").textContent = "";
}

$("auth-form").onsubmit = (event) => {
  event.preventDefault();
  run(async () => {
    const r = await api("/api/auth/" + state.authMode, {
      method: "POST",
      body: {
        username: $("auth-username").value.trim(),
        password: $("auth-password").value,
      },
    });
    setToken(r.token);
    state.user = r.user;
    $("auth-password").value = "";
    await afterLogin();
  }, $("btn-auth-submit"));
};
$("btn-auth-toggle").onclick = () => {
  state.authMode = state.authMode === "login" ? "register" : "login";
  updateAuthUI();
};
async function afterLogin() {
  const r = await api("/api/auth/me");
  state.user = r.user;
  $("user-name").textContent = r.user.username;
  $("user-avatar").textContent = r.user.username.slice(0, 1).toUpperCase();
  $("user-box").hidden = false;
  state.project = null;
  state.dirty = false;
  await loadProjects();
  show("list");
}

$("btn-logout").onclick = () =>
  run(async () => {
    if (!(await guardLeave())) return;
    await api("/api/auth/logout", { method: "POST" });
    setToken("");
    state.user = null;
    state.project = null;
    state.dirty = false;
    state.decisions = {};
    $("user-box").hidden = true;
    show("auth");
  }, $("btn-logout"));

async function loadProjects() {
  const { projects } = await api("/api/projects");
  state.projects = projects;
  $("stat-projects").textContent = projects.length;
  $("stat-baselined").textContent = projects.filter(
    (p) => p.current_version_seq,
  ).length;
  $("stat-pending").textContent = projects.filter(
    (p) => !p.current_version_seq,
  ).length;
  $("project-total").textContent = projects.length;
  renderProjects();
}
function renderProjects() {
  const filter = $("project-search").value.trim().toLowerCase();
  const projects = state.projects.filter((p) =>
    p.name.toLowerCase().includes(filter),
  );
  $("project-empty").hidden = state.projects.length > 0;
  $("search-empty").hidden = projects.length > 0 || state.projects.length === 0;
  $("project-list").innerHTML = projects
    .map(
      (p) =>
        `<button class="project-item" data-project="${esc(p.id)}"><span class="project-name"><span class="project-monogram">${esc(p.name.slice(0, 1))}</span><span><strong>${esc(p.name)}</strong><small>${esc(p.id.slice(-8).toUpperCase())}</small></span></span><span><span class="badge ${p.current_version_seq ? "confirmed" : "pending"}">${p.current_version_seq ? "已确认 · V" + p.current_version_seq : "待建立基线"}</span></span><time>${fmtTime(p.updated_at)}</time><span class="project-open">进入项目 →</span></button>`,
    )
    .join("");
}
$("project-search").oninput = renderProjects;

$("project-list").onclick = (event) => {
  const item = event.target.closest("[data-project]");
  if (item) run(() => openProject(item.dataset.project), item);
};
$("btn-new-project").onclick = () => {
  modal(
    "新建项目",
    '<form id="project-form"><label for="new-name">项目名称</label><input id="new-name" maxlength="200" required placeholder="如：品牌官网一期"><div class="row"><button class="btn primary" type="submit" id="create-submit">创建项目</button></div></form>',
  );
  $("project-form").onsubmit = (event) => {
    event.preventDefault();
    run(async () => {
      const p = await api("/api/projects", {
        method: "POST",
        body: { name: $("new-name").value.trim() },
      });
      $("modal").close();
      await openProject(p.id);
    }, $("create-submit"));
  };
};

function hydrateProject(project) {
  state.project = project;
  state.draft = clone(project.draft);
  state.dirty = false;
  state.review = project.review;
  state.decisions = {};
  state.pendingConfirm = null;
  state.pendingApply = null;
  state.requirementMode = project.draft.requirements.length
    ? "draft"
    : project.versions.length
      ? "published"
      : "draft";
  const reviewed = project.review?.decisions?.length > 0;
  if (project.review) project.review.applied ||= reviewed;
  state.selected = new Set(
    project.current_version_id ? [] : project.sources.map((s) => s.id),
  );
  $("source-input").value = "";
  $("source-label").value = "";
}
async function openProject(id, tab = "overview") {
  hydrateProject(await api("/api/projects/" + id));
  show("workbench");
  renderWorkbench();
  selectTab(tab);
}

$("btn-back").onclick = () =>
  run(async () => {
    if (!(await guardLeave())) return;
    state.project = null;
    state.dirty = false;
    state.decisions = {};
    history.replaceState(null, "", location.pathname);
    if (getToken()) {
      await loadProjects();
      show("list");
    } else show("auth");
  }, $("btn-back"));

function syncButtons() {
  const locked = readonly() || state.busy;
  for (const id of [
    "btn-add-source",
    "btn-analyze",
    "btn-save-draft",
    "btn-confirm",
    "btn-compare",
    "btn-apply",
    "btn-material-compare",
    "btn-add-requirement",
    "btn-create-draft",
  ])
    $(id).disabled = locked;
  $("source-editor").disabled = locked;
  $("btn-analyze").disabled ||= !state.selected.size;
  $("btn-compare").disabled ||= !latest() || !state.selected.size;
  $("btn-material-compare").disabled = $("btn-compare").disabled;
  $("btn-confirm").disabled ||=
    !state.draft.requirements.length || state.requirementMode !== "draft";
  $("btn-save-draft").disabled ||=
    !state.dirty || state.requirementMode !== "draft";
  $("btn-create-draft").disabled ||= !latest();
  $("btn-export").disabled = readonly() || !latest();
  $("btn-members").hidden = readonly();
  $("btn-apply").disabled ||=
    state.review?.applied ||
    Object.keys(state.decisions).length !== (state.review?.changes.length || 0);
  const editLocked = locked || state.requirementMode !== "draft";
  $("draft-questions").disabled = editLocked;
  $("candidate-list")
    .querySelectorAll("input,textarea,select,button[data-delete]")
    .forEach((el) => (el.disabled = editLocked));
  $("btn-published-mode").disabled = !latest() || state.busy;
  $("btn-draft-mode").disabled = readonly() || state.busy;
}
function renderWorkbench() {
  $("wb-project-name").textContent = state.project.name;
  $("wb-current-version").textContent = latest()
    ? "当前版本 V" + latest().seq
    : "待建立基线";
  $("wb-current-version").className =
    "badge " + (latest() ? "confirmed" : "pending");
  $("demo-notice").hidden = !readonly();
  $("source-editor").hidden = readonly();
  $("btn-analyze").hidden = readonly();
  $("btn-material-compare").hidden = readonly();
  $("btn-back").textContent = readonly() ? "← 返回登录" : "← 全部项目";
  renderSources();
  renderDraft();
  renderChanges();
  renderVersions();
  renderOverview();
  syncButtons();
}
const tabNames = {
  overview: "项目概览",
  materials: "沟通材料",
  requirements: "需求清单",
  changes: "变更审核",
  versions: "版本历史",
};
function selectTab(tab) {
  if (!tabNames[tab]) tab = "overview";
  state.tab = tab;
  document
    .querySelectorAll("[data-page]")
    .forEach((el) => (el.hidden = el.dataset.page !== tab));
  document.querySelectorAll("[data-tab]").forEach((el) => {
    el.classList.toggle(
      "active",
      el.dataset.tab === tab &&
        (el.classList.contains("nav-item") || el.closest(".project-tabs")),
    );
    if (el.closest(".project-tabs"))
      el.setAttribute(
        "aria-current",
        el.dataset.tab === tab ? "page" : "false",
      );
  });
  $("crumb-project").textContent = state.project.name;
  $("crumb-divider").hidden = false;
  $("crumb-page").textContent = tabNames[tab];
  if (!readonly()) {
    const address = new URL(location.href);
    address.searchParams.set("project", state.project.id);
    address.searchParams.set("tab", tab);
    address.searchParams.delete("training");
    history.replaceState(null, "", address);
  }
  if (tab === "overview") renderOverview();
  if (tab === "requirements") renderDraft();
}
document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-tab]");
  if (target && state.project && !state.busy) selectTab(target.dataset.tab);
});
function pendingChanges() {
  return state.review &&
    !state.review.applied &&
    state.review.baseline_version_id === state.project.current_version_id
    ? state.review.changes.length
    : 0;
}
function renderOverview() {
  const p = state.project,
    current = latest(),
    pending = pendingChanges();
  $("overview-requirements").textContent =
    current?.snapshot.requirements.length || 0;
  $("overview-sources").textContent = p.sources.length;
  $("overview-changes").textContent = pending;
  $("overview-versions").textContent = p.versions.length;
  $("nav-count-materials").textContent = p.sources.length;
  $("nav-count-requirements").textContent =
    state.draft.requirements.length ||
    current?.snapshot.requirements.length ||
    0;
  $("nav-count-changes").textContent = pending || "";
  $("nav-count-versions").textContent = p.versions.length;
  $("overview-status").textContent = pending
    ? "有待审核变更"
    : state.draft.requirements.length
      ? "需求审核中"
      : current
        ? "基线已确认"
        : "待建立基线";
  const tasks = [
    {
      number: "01",
      title: "归档项目沟通",
      text: p.sources.length
        ? `已保存 ${p.sources.length} 份材料，可继续添加新沟通。`
        : "添加客户沟通、会议纪要或邮件，保留需求来源。",
      tab: "materials",
      action: p.sources.length ? "管理材料" : "添加材料",
    },
    {
      number: "02",
      title: state.draft.requirements.length ? "完成需求审核" : "维护需求基线",
      text: state.draft.requirements.length
        ? `当前有 ${state.draft.requirements.length} 条草稿需求待确认。`
        : current
          ? `当前需求基线为 V${current.seq}，修改将形成新版本。`
          : "整理并审核需求条目，确认首个正式版本。",
      tab: "requirements",
      action: state.draft.requirements.length ? "审核草稿" : "查看需求",
    },
    {
      number: "03",
      title: "处理范围变化",
      text: pending
        ? `有 ${pending} 条变更建议等待你的审核。`
        : "收到新沟通后，与已确认的需求范围进行比较。",
      tab: "changes",
      action: pending ? "审核变更" : "查看变更",
    },
  ];
  $("overview-tasks").innerHTML = tasks
    .map(
      (t) =>
        `<div class="task-item"><span class="task-number">${t.number}</span><div class="task-copy"><h3>${t.title}</h3><p>${t.text}</p></div><button class="btn small" data-tab="${t.tab}">${t.action}</button></div>`,
    )
    .join("");
  const activity = [
    ...p.sources.map((s) => ({
      time: s.created_at,
      text: "归档沟通材料 · " + s.label,
    })),
    ...p.versions.map((v) => ({
      time: v.confirmed_at,
      text: `确认需求版本 V${v.seq} · ${v.confirmed_by}`,
    })),
  ]
    .sort((a, b) => new Date(b.time) - new Date(a.time))
    .slice(0, 5);
  $("project-activity").innerHTML = activity.length
    ? activity
        .map(
          (a) =>
            `<div class="activity-item"><p>${esc(a.text)}</p><small>${fmtTime(a.time)}</small></div>`,
        )
        .join("")
    : '<p class="muted fine">暂无项目动态。保存沟通材料后，相关记录将显示在这里。</p>';
  const questions = state.draft.requirements.length
    ? state.draft.questions
    : current?.snapshot.questions || [];
  $("overview-questions").innerHTML = questions.length
    ? questions
        .map(
          (q, i) =>
            `<div class="overview-question"><span class="question-index">${String(i + 1).padStart(2, "0")}</span>${esc(q)}</div>`,
        )
        .join("")
    : '<p class="muted fine">当前没有项目级待澄清事项。</p>';
}

function renderSources() {
  $("source-count").textContent =
    `已选 ${state.selected.size} / ${state.project.sources.length}`;
  $("source-list").innerHTML =
    state.project.sources
      .map(
        (s) =>
          `<div class="source-item"><input type="checkbox" id="select-${esc(s.id)}" data-select="${esc(s.id)}" ${state.selected.has(s.id) ? "checked" : ""} ${readonly() ? "disabled" : ""}><label for="select-${esc(s.id)}">${esc(s.label)} ${readonly() ? '<span class="badge">培训材料</span>' : ""}<small>${sourceTypes[s.source_type] || "材料"} · ${fmtTime(s.created_at)}</small></label><button class="btn small" data-source="${esc(s.id)}">原文</button></div>`,
      )
      .join("") || '<p class="muted fine">暂无沟通材料，保存后将在此归档。</p>';
}
$("source-list").onchange = (event) => {
  const id = event.target.dataset.select;
  if (id) {
    event.target.checked ? state.selected.add(id) : state.selected.delete(id);
    renderSources();
    syncButtons();
  }
};
function showSource(id) {
  const s = state.project.sources.find((source) => source.id === id);
  if (s)
    modal(
      s.label,
      `<p class="muted">${sourceTypes[s.source_type]} · ${fmtTime(s.created_at)}</p><div class="modal-source">${esc(s.text)}</div>`,
    );
}
document.addEventListener("click", (event) => {
  const source = event.target.closest("[data-source]");
  if (source) showSource(source.dataset.source);
});

$("btn-add-source").onclick = () =>
  run(async () => {
    const text = $("source-input").value.trim();
    if (!text) throw new Error("请先输入沟通材料");
    const s = await api(url("/sources"), {
      method: "POST",
      body: {
        text,
        label: $("source-label").value,
        source_type: $("source-type").value,
      },
    });
    state.project.sources.push(s);
    state.selected = new Set([s.id]);
    $("source-input").value = "";
    $("source-label").value = "";
    renderSources();
    toast("已保存并选中这条材料");
  }, $("btn-add-source"));

$("btn-analyze").onclick = () =>
  run(async () => {
    if ($("source-input").value.trim())
      throw new Error("输入框还有未保存材料，请先保存");
    if (
      state.draft.requirements.length &&
      !(await ask(
        "重新整理需求",
        "新的分析将替换当前草稿，请确认已保留需要的内容。",
        "重新整理",
      ))
    )
      return;
    const r = await api(url("/analyze"), {
      method: "POST",
      body: {
        source_ids: [...state.selected],
        expected_updated_at: state.draft.updated_at || null,
      },
    });
    $("analysis-metrics").textContent =
      r.status === "ok"
        ? `已整理 ${r.requirements.length} 条需求，请审核后确认。`
        : "";
    if (r.status !== "ok") throw new Error(r.message);
    state.draft = r.draft;
    state.project.draft = clone(r.draft);
    state.dirty = false;
    state.requirementMode = "draft";
    renderDraft();
    selectTab("requirements");
    toast("需求已整理，请审核后确认");
  }, $("btn-analyze"));

function evidenceHtml(evidence) {
  return `<details><summary>原文依据 · ${evidence.length} 条引用</summary>${evidence.map((e) => `<div class="evidence"><button data-source="${esc(e.source_id)}">${esc(state.project?.sources.find((s) => s.id === e.source_id)?.label || "沟通材料")}</button><br>${esc(e.quote)}</div>`).join("")}</details>`;
}
function renderDraft() {
  const published = state.requirementMode === "published" && latest();
  const reqs = published
    ? latest().snapshot.requirements
    : state.draft.requirements;
  const questions = published
    ? latest().snapshot.questions
    : state.draft.questions;
  $("draft-title").textContent = published ? "正式需求清单" : "需求审核草稿";
  $("draft-empty").hidden = reqs.length > 0;
  $("draft-editable-note").textContent = published
    ? `已确认基线 V${latest().seq} · ${reqs.length} 条需求`
    : state.dirty
      ? "存在未保存修改，请保存后再离开项目。"
      : reqs.length
        ? "草稿已保存，可继续审核并确认版本。"
        : "通过沟通材料整理需求，或手动新增条目。";
  $("wb-draft-badge").textContent = published
    ? "正式基线"
    : state.dirty
      ? "未保存"
      : "审核草稿";
  $("wb-draft-badge").className =
    "badge " + (published ? "confirmed" : "pending");
  $("draft-save-status").textContent = state.dirty
    ? "有未保存修改"
    : "草稿已保存";
  $("btn-published-mode").classList.toggle("active", Boolean(published));
  $("btn-draft-mode").classList.toggle("active", !published);
  $("draft-actions").hidden = Boolean(published) || readonly();
  $("btn-add-requirement").hidden = readonly();
  $("btn-create-draft").hidden = readonly() || !latest();
  $("candidate-list").innerHTML = reqs
    .map((r, i) =>
      published
        ? `<article class="card"><div class="card-title-row"><h3><span class="requirement-id">${esc(r.id)}</span>${esc(r.title)}</h3><span class="tag ${r.intent}">${intents[r.intent]}</span></div><p class="description">${esc(r.description)}</p>${r.stated_acceptance_criteria.map((c) => `<p class="fine">验收条件：${esc(c)}</p>`).join("")}${evidenceHtml(r.evidence)}${r.questions.map((q) => `<p class="question">待澄清：${esc(q)}</p>`).join("")}</article>`
        : `<article class="card"><div class="card-head"><input aria-label="需求标题 ${i + 1}" data-i="${i}" data-field="title" maxlength="300" value="${esc(r.title)}"><select aria-label="需求意图 ${i + 1}" data-i="${i}" data-field="intent">${Object.entries(
            intents,
          )
            .map(
              ([v, label]) =>
                `<option value="${v}" ${r.intent === v ? "selected" : ""}>${label}</option>`,
            )
            .join(
              "",
            )}</select><button class="btn small danger" data-delete="${i}" aria-label="删除需求 ${i + 1}">移除</button></div><textarea aria-label="需求描述 ${i + 1}" data-i="${i}" data-field="description" rows="2" maxlength="10000">${esc(r.description)}</textarea><details><summary>编辑验收条件与待澄清事项</summary><label>验收条件（每行一条）<textarea data-i="${i}" data-field="stated_acceptance_criteria" rows="2">${esc(r.stated_acceptance_criteria.join("\n"))}</textarea></label><label>待澄清事项（每行一项）<textarea data-i="${i}" data-field="questions" rows="2">${esc(r.questions.join("\n"))}</textarea></label></details>${evidenceHtml(r.evidence)}</article>`,
    )
    .join("");
  $("draft-questions").value = questions.join("\n");
  $("draft-questions").hidden = !reqs.length && !state.dirty;
  $("questions-label").hidden = $("draft-questions").hidden;
  syncButtons();
}
$("btn-published-mode").onclick = () => {
  state.requirementMode = "published";
  renderDraft();
};
$("btn-draft-mode").onclick = () => {
  state.requirementMode = "draft";
  renderDraft();
};
function prepareDraft() {
  if (!state.draft.requirements.length && !state.dirty && latest()) {
    state.draft = {
      ...state.draft,
      ...clone(latest().snapshot),
      parent_version_id: state.project.current_version_id,
    };
  }
  state.requirementMode = "draft";
}
$("btn-create-draft").onclick = () =>
  run(async () => {
    if (
      state.draft.requirements.length &&
      !(await ask(
        "替换当前草稿",
        "将从正式基线重新建立草稿，当前未确认的草稿内容会被替换。",
        "创建修订草稿",
      ))
    )
      return;
    state.draft = {
      ...state.draft,
      ...clone(latest().snapshot),
      parent_version_id: state.project.current_version_id,
    };
    state.requirementMode = "draft";
    markDirty();
    renderDraft();
  }, $("btn-create-draft"));
$("btn-add-requirement").onclick = () => {
  if (readonly() || state.busy) return;
  if (!state.project.sources.length) {
    selectTab("materials");
    toast("请先保存沟通材料，便于关联需求依据。");
    return;
  }
  modal(
    "新增需求",
    `<form id="requirement-form"><label for="requirement-title">需求标题</label><input id="requirement-title" required maxlength="300" placeholder="填写明确的交付内容"><label for="requirement-description">需求说明</label><textarea id="requirement-description" maxlength="10000" rows="3" placeholder="描述需求范围与期望结果"></textarea><label for="requirement-intent">需求意图</label><select id="requirement-intent">${Object.entries(
      intents,
    )
      .map(([key, value]) => `<option value="${key}">${value}</option>`)
      .join(
        "",
      )}</select><label for="requirement-source">关联沟通材料</label><select id="requirement-source">${state.project.sources.map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("")}</select><label for="requirement-quote">原文依据</label><textarea id="requirement-quote" required maxlength="50000" rows="3"></textarea><p class="muted fine">保留材料中的原句，便于项目成员核查上下文。</p><div class="row"><button id="requirement-submit" class="btn primary" type="submit">添加到审核草稿</button></div></form>`,
  );
  const fillQuote = () =>
    ($("requirement-quote").value = state.project.sources.find(
      (s) => s.id === $("requirement-source").value,
    ).text);
  fillQuote();
  $("requirement-source").onchange = fillQuote;
  $("requirement-form").onsubmit = (event) => {
    event.preventDefault();
    const source = state.project.sources.find(
        (s) => s.id === $("requirement-source").value,
      ),
      quote = $("requirement-quote").value;
    if (!quote.trim() || !source.text.includes(quote)) {
      toast("原文依据需逐字存在于所选沟通材料中。", true);
      return;
    }
    const title = $("requirement-title").value.trim();
    if (!title) {
      toast("请填写需求标题。", true);
      return;
    }
    prepareDraft();
    state.draft.requirements.push({
      title,
      description: $("requirement-description").value.trim(),
      intent: $("requirement-intent").value,
      stated_acceptance_criteria: [],
      evidence: [{ source_id: source.id, quote }],
      questions: [],
    });
    markDirty();
    $("modal").close();
    selectTab("requirements");
    renderDraft();
    toast("需求已添加到草稿，请审核后确认。");
  };
};

$("candidate-list").oninput = (event) => {
  if (
    state.busy ||
    readonly() ||
    state.requirementMode !== "draft" ||
    event.target.dataset.i == null
  )
    return;
  const { i, field } = event.target.dataset,
    value = event.target.value;
  state.draft.requirements[+i][field] = [
    "questions",
    "stated_acceptance_criteria",
  ].includes(field)
    ? value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean)
    : value;
  markDirty();
  syncButtons();
};
$("candidate-list").onclick = (event) => {
  const remove = event.target.closest("[data-delete]");
  if (remove && !state.busy && !readonly()) {
    state.draft.requirements.splice(+remove.dataset.delete, 1);
    markDirty();
    renderDraft();
  }
};
$("draft-questions").oninput = () => {
  state.draft.questions = $("draft-questions")
    .value.split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  markDirty();
  syncButtons();
};

async function saveDraft() {
  const r = await api(url("/draft"), {
    method: "POST",
    body: {
      requirements: state.draft.requirements,
      questions: state.draft.questions,
      expected_updated_at: state.draft.updated_at || null,
      parent_version_id: state.draft.parent_version_id || null,
    },
  });
  state.draft = r;
  state.project.draft = clone(r);
  state.dirty = false;
  renderDraft();
}
$("btn-save-draft").onclick = () =>
  run(async () => {
    await saveDraft();
    toast("草稿已保存");
  }, $("btn-save-draft"));
$("btn-confirm").onclick = () =>
  run(async () => {
    if (!state.pendingConfirm) {
      await saveDraft();
      if (
        !(await ask(
          "确认需求版本",
          "将把当前审核后的草稿保存为不可覆盖的版本。后续修改会生成新版本。",
          "确认版本",
        ))
      )
        return;
      state.pendingConfirm = {
        request_id: requestId(),
        expected_updated_at: state.draft.updated_at,
      };
    }
    const version = await api(url("/confirm"), {
      method: "POST",
      body: state.pendingConfirm,
    });
    await openProject(state.project.id, "versions");
    toast("已保存 V" + version.seq);
  }, $("btn-confirm"));

$("btn-compare").onclick = () =>
  run(async () => {
    if ($("source-input").value.trim())
      throw new Error("请先保存输入框中的新沟通");
    if (
      Object.keys(state.decisions).length &&
      !(await ask("重新分析变更", "当前未提交的变更选择会被替换。", "重新分析"))
    )
      return;
    const r = await api(url("/compare"), {
      method: "POST",
      body: {
        source_ids: [...state.selected],
        baseline_version_id: state.project.current_version_id,
      },
    });
    if (r.status !== "ok") throw new Error(r.message);
    state.review = r;
    state.decisions = {};
    state.pendingApply = null;
    renderChanges();
    selectTab("changes");
    toast(
      r.changes.length ? "变更候选已生成，请逐条审核" : "未发现需要审核的变更",
    );
  }, $("btn-compare"));

function renderChanges() {
  const review = state.review,
    changes = review?.changes || [];
  const base = state.project.versions.find(
    (v) => v.id === review?.baseline_version_id,
  );
  const stale =
    review && review.baseline_version_id !== state.project.current_version_id;
  const locked = readonly() || review?.applied || stale;
  $("change-empty").hidden = changes.length > 0;
  $("apply-row").hidden = !changes.length || locked;
  $("change-context").textContent = review
    ? `基于 V${base?.seq || "?"} · ${changes.length} 条变更建议${review.applied ? " · 已提交审核" : stale ? " · 基线已过期，请重新分析" : " · 请对每条候选选择采纳或忽略"}`
    : "先确认 V1，再保存并勾选新沟通。";
  $("change-list").innerHTML = changes
    .map((c) => {
      const idx = c.change_index,
        d =
          state.decisions[idx] ||
          review?.decisions?.find((d) => d.change_index === idx) ||
          {};
      const before = (c.related_requirement_ids || [])
        .map((id) => base?.snapshot.requirements.find((r) => r.id === id))
        .filter(Boolean);
      return `<article class="card"><h3><span class="tag ${c.kind}">${kinds[c.kind]}</span> ${esc(c.title || c.proposed_after)}</h3><div class="change-diff"><div class="before"><small>原版本</small>${esc(before.map((r) => `[${intents[r.intent]}] ${r.description}`).join("\n") || "无关联原需求")}</div><div><small>建议修改后</small>${esc(c.proposed_after)}</div></div><p class="muted fine">${esc(c.reason)}</p>${evidenceHtml(c.evidence)}${c.questions.map((q) => `<p class="question">${esc(q)}</p>`).join("")}<div class="decision-row"><button class="btn small ${d.decision === "accept" ? "selected" : ""}" data-decision="accept" data-index="${idx}" ${locked ? "disabled" : ""}>采纳</button><button class="btn small ${d.decision === "ignore" ? "selected" : ""}" data-decision="ignore" data-index="${idx}" ${locked ? "disabled" : ""}>忽略</button><input aria-label="人工修订变更 ${idx + 1}" data-revise="${idx}" maxlength="10000" placeholder="${c.kind === "uncertain" ? "可选：修订待澄清问题" : "可选：人工修订内容"}" value="${esc(d.revised_after || "")}" ${locked || ["unchanged", "removed"].includes(c.kind) ? "disabled" : ""}></div></article>`;
    })
    .join("");
  $("decision-count").textContent =
    `已审核 ${Object.keys(state.decisions).length} / ${changes.length}；全部忽略不会创建新版本。`;
  renderOverview();
  syncButtons();
}
$("change-list").onclick = (event) => {
  const b = event.target.closest("[data-decision]");
  if (!b || readonly() || state.busy) return;
  state.pendingApply = null;
  state.decisions[b.dataset.index] = {
    ...state.decisions[b.dataset.index],
    change_index: +b.dataset.index,
    decision: b.dataset.decision,
  };
  renderChanges();
};
$("change-list").oninput = (event) => {
  if (event.target.dataset.revise == null || readonly() || state.busy) return;
  const idx = +event.target.dataset.revise;
  state.pendingApply = null;
  state.decisions[idx] = {
    change_index: idx,
    decision: "accept",
    revised_after: event.target.value,
  };
  $("decision-count").textContent =
    `已审核 ${Object.keys(state.decisions).length} / ${state.review.changes.length}`;
  const row = event.target.closest(".decision-row");
  row
    .querySelectorAll("[data-decision]")
    .forEach((b) =>
      b.classList.toggle("selected", b.dataset.decision === "accept"),
    );
  syncButtons();
};
$("btn-apply").onclick = () =>
  run(async () => {
    if (state.dirty) throw new Error("请先保存或确认当前草稿，再提交变更审核");
    state.pendingApply ||= {
      request_id: requestId(),
      run_id: state.review.run_id,
      decisions: Object.values(state.decisions),
    };
    const version = await api(url("/apply"), {
      method: "POST",
      body: state.pendingApply,
    });
    await openProject(state.project.id, "versions");
    toast(
      version.created
        ? "已根据审核结果保存 V" + version.seq
        : "审核已保存，需求未变，未创建新版本",
    );
  }, $("btn-apply"));

function versionHtml(version) {
  return `<p class="muted">${fmtTime(version.confirmed_at)} · ${esc(version.confirmed_by)}</p>${version.snapshot.requirements.map((r) => `<article class="card"><h3>${esc(r.id)} · ${esc(r.title)} <span class="tag ${r.intent}">${intents[r.intent]}</span></h3><p class="description">${esc(r.description)}</p>${r.stated_acceptance_criteria.map((x) => `<p class="fine">验收：${esc(x)}</p>`).join("")}${evidenceHtml(r.evidence)}${r.questions.map((q) => `<p class="question">${esc(q)}</p>`).join("")}</article>`).join("")}<h3>项目级待澄清问题</h3>${version.snapshot.questions.map((q) => `<p class="question">${esc(q)}</p>`).join("") || '<p class="muted">无</p>'}`;
}
function diffHtml(version) {
  const parent = state.project.versions.find(
    (v) => v.id === version.parent_version_id,
  );
  if (!parent)
    return '<p class="muted">这是初始版本。</p>' + versionHtml(version);
  const previous = new Map(parent.snapshot.requirements.map((r) => [r.id, r]));
  const current = new Map(version.snapshot.requirements.map((r) => [r.id, r]));
  const ids = new Set([...previous.keys(), ...current.keys()]);
  const rows = [...ids].filter(
    (id) =>
      JSON.stringify(previous.get(id)) !== JSON.stringify(current.get(id)),
  );
  return `<p>V${parent.seq} → V${version.seq} · ${rows.length} 条需求变化</p>${rows.map((id) => `<article class="card"><h3>${esc(id)}</h3><div class="change-diff"><div class="before"><small>修改前</small>${esc(previous.has(id) ? "[" + intents[previous.get(id).intent] + "] " + previous.get(id).description : "不存在")}</div><div><small>修改后</small>${esc(current.has(id) ? "[" + intents[current.get(id).intent] + "] " + current.get(id).description : "已删除")}</div></div></article>`).join("")}<h3>项目级待澄清问题变化</h3><div class="change-diff"><div class="before">${esc(parent.snapshot.questions.join("\n") || "无")}</div><div>${esc(version.snapshot.questions.join("\n") || "无")}</div></div>`;
}
function renderVersions() {
  $("version-empty").hidden = state.project.versions.length > 0;
  $("version-list").innerHTML = [...state.project.versions]
    .reverse()
    .map(
      (v) =>
        `<article class="version-item"><div><span class="badge">V${v.seq}${v.id === state.project.current_version_id ? " · 当前" : ""}</span><p>${v.snapshot.requirements.length} 条需求 · ${fmtTime(v.confirmed_at)} · ${esc(v.confirmed_by)}</p></div><div class="row"><button class="btn small" data-version="${esc(v.id)}" data-action="view">查看详情</button><button class="btn small" data-version="${esc(v.id)}" data-action="diff">前后对比</button><button class="btn small" data-version="${esc(v.id)}" data-action="export" ${readonly() ? "disabled" : ""}>导出</button></div></article>`,
    )
    .join("");
}
$("version-list").onclick = (event) => {
  const b = event.target.closest("[data-version]");
  if (!b) return;
  const v = state.project.versions.find((v) => v.id === b.dataset.version);
  if (b.dataset.action === "export") run(() => downloadExport(v.id), b);
  else
    modal(
      b.dataset.action === "diff" ? "版本前后对比" : "需求版本 V" + v.seq,
      b.dataset.action === "diff" ? diffHtml(v) : versionHtml(v),
    );
};
async function downloadExport(id) {
  const response = await fetch(
    url("/export?version_id=" + encodeURIComponent(id)),
    { headers: { Authorization: "Bearer " + getToken() } },
  );
  if (!response.ok) throw new Error("导出失败，请重新登录或重试");
  const blob = await response.blob(),
    href = URL.createObjectURL(blob),
    link = document.createElement("a");
  link.href = href;
  link.download =
    "需求文档_V" + state.project.versions.find((v) => v.id === id).seq + ".md";
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
$("btn-export").onclick = () =>
  run(() => downloadExport(latest().id), $("btn-export"));
$("btn-members").onclick = () => {
  const owner = state.project.owner_id === state.user?.id;
  modal(
    "项目成员",
    `<p class="muted">成员可查看材料、编辑草稿与确认版本；仅项目所有者可添加成员。</p><p>当前成员：${state.project.members.map((m) => esc(m.username)).join("、") || "仅项目所有者"}</p>${owner ? '<form id="member-form"><label for="member-name">添加已注册用户</label><input id="member-name" required maxlength="50"><div class="row"><button class="btn primary" id="invite-submit" type="submit">添加成员</button></div></form>' : ""}`,
  );
  if (owner)
    $("member-form").onsubmit = (event) => {
      event.preventDefault();
      run(async () => {
        await api(url("/members"), {
          method: "POST",
          body: { username: $("member-name").value.trim() },
        });
        state.project.members = (await api(url("/members"))).members;
        $("modal").close();
        toast("成员已添加");
      }, $("invite-submit"));
    };
};

window.addEventListener("beforeunload", (event) => {
  if (
    state.dirty ||
    Object.keys(state.decisions).length ||
    $("source-input").value.trim()
  ) {
    event.preventDefault();
    event.returnValue = "";
  }
});
$("btn-empty-project").onclick = () => $("btn-new-project").click();
$("btn-material-compare").onclick = () => $("btn-compare").click();
async function returnToProjects() {
  if (!(await guardLeave())) return;
  state.project = null;
  state.dirty = false;
  state.decisions = {};
  history.replaceState(null, "", location.pathname);
  await loadProjects();
  show("list");
}
$("nav-projects").onclick = () => run(returnToProjects, $("nav-projects"));
$("crumb-root").onclick = () => run(returnToProjects, $("crumb-root"));
$("btn-data-policy").onclick = () =>
  modal(
    "数据使用说明",
    "<p>项目资料仅对项目所有者与已添加成员开放。</p><p>使用智能整理时，所选沟通材料会提交至组织配置的 AI 服务用于生成需求建议。请按所在团队的数据管理要求处理客户资料。</p><p>正式需求由项目成员审核确认。需要管理服务接入与数据保存策略时，请联系系统管理员。</p>",
  );
$("btn-help").onclick = () =>
  modal(
    "工作空间使用帮助",
    '<div class="card-list"><article class="card"><h3>1. 保存沟通材料</h3><p class="muted">在项目中归档聊天、会议或邮件，勾选本次需要处理的内容。</p></article><article class="card"><h3>2. 建立需求基线</h3><p class="muted">智能整理或手动新增需求，核查原文依据，保存并确认版本。</p></article><article class="card"><h3>3. 协同审核变更</h3><p class="muted">新沟通与已确认版本比较后，逐条采纳或忽略变更建议，保留版本记录。</p></article></div>',
  );
async function boot() {
  updateAuthUI();
  show("auth");
  const params = new URLSearchParams(location.search);
  if (params.get("training") === "1") {
    try {
      hydrateProject(await api("/api/demo"));
      state.requirementMode = "published";
      show("workbench");
      renderWorkbench();
      selectTab("overview");
    } catch {
      toast("培训空间未开放，请登录工作空间。", true);
    }
    return;
  }
  if (getToken()) {
    try {
      await afterLogin();
      if (params.get("project"))
        await openProject(
          params.get("project"),
          params.get("tab") || "overview",
        );
    } catch (e) {
      toast(e.message, true);
    }
  }
}

boot();
