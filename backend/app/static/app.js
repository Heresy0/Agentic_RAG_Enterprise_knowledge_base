import { AuthSession } from "./auth.js";
import { createQualityWorkspace } from "./quality.js";
import { createVerificationWorkspace } from "./verification.js";

const $ = id => document.getElementById(id);
const statuses = { ready: "已就绪", pending: "等待索引", queued: "排队中", running: "索引中", succeeded: "已完成", failed: "失败", cancelled: "已取消" };
const permissions = { viewer: "只读", editor: "可编辑", admin: "管理员" };
const state = { auth: null, config: null, bases: [], kb: null, documents: [], jobs: [], conversations: [], conversationId: null, revision: 0, tab: "qa", asking: false, uploading: false, loading: false, polling: null, deleting: null, feedbackCursor: null, feedbackEpoch: 0, snapshotEpoch: 0 };
const feedbackReasons = { incorrect: "答案错误", incomplete: "遗漏条件或要点", citation: "引用不正确", false_refusal: "不该拒答", outdated: "资料过期", other: "其他问题" };
const qualityWorkspace = createQualityWorkspace({ $, element, api, state, sameContext, errorNotice, onImprovement: id => verificationWorkspace.createForCase(id) });
const verificationWorkspace = createVerificationWorkspace({ $, element, api, state, sameContext, errorNotice });

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text; // Never render server/user content as HTML.
  if (className) node.className = className;
  return node;
}
function notice(text = "", error = false) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
  $("notice").classList.toggle("error", error);
}
function editable() { return ["editor", "admin"].includes(state.kb?.permission); }
function canAsk() { return !!state.auth?.authenticated && (!!state.kb || state.bases.length > 0); }
function sameContext(revision) { return revision === state.revision && state.auth.authenticated; }
function errorNotice(error) {
  if (!state.auth?.authenticated) resetWorkspace();
  notice(error.message || "操作失败，请稍后重试。", true);
}
function updateControls() {
  qualityWorkspace.updateControls();
  verificationWorkspace.updateControls();
  $("question").disabled = !canAsk() || state.asking || state.loading;
  $("ask-button").disabled = !canAsk() || state.asking || state.loading;
  $("ask-button").firstChild.textContent = state.asking ? "生成中… " : "发送问题 ";
  for (const id of ["new-conversation", "conversation-list"]) $(id).disabled = !canAsk() || state.asking || state.loading;
  $("delete-conversation").disabled = !state.conversationId || state.asking || state.loading;
  $("upload-file").disabled = !editable() || state.uploading || state.loading;
  $("upload-button").disabled = !editable() || !$("upload-file").files.length || state.uploading || state.loading;
  $("upload-button").textContent = state.uploading ? "正在上传…" : "上传并索引";
  $("refresh-button").disabled = !state.kb || state.loading;
  $("session-button").textContent = state.auth?.authenticated ? `${state.auth.name} · 退出` : "登录工作空间 ↗";
  $("feedback-shared-option").disabled = state.kb ? state.kb.permission !== "admin" : !state.bases.some(kb => kb.permission === "admin");
  if ($("feedback-shared-option").disabled && $("feedback-view").value === "shared") $("feedback-view").value = "mine";
  $("feedback-refresh").disabled = !canAsk();
}

async function api(path, options = {}) {
  const { responseType, ...requestOptions } = options;
  const epoch = state.auth.epoch;
  const token = await state.auth.token();
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...requestOptions, headers, signal: AbortSignal.timeout(120000), credentials: "same-origin" });
  if (epoch !== state.auth.epoch) throw new Error("登录状态已改变，请重试。");
  let data = null;
  if (response.status !== 204) {
    try { data = responseType === "text" && response.ok ? await response.text() : await response.json(); } catch { throw new Error(`服务返回了无法解析的响应（HTTP ${response.status}），请检查 API 日志。`); }
  }
  if (!response.ok) {
    if (response.status === 401) state.auth.clear();
    const requestId = response.headers.get("X-Request-ID");
    const detail = typeof data?.detail === "string" ? data.detail : Array.isArray(data?.detail) ? data.detail.map(item => item.msg).join("；") : `请求失败（HTTP ${response.status}）`;
    const retry = response.headers.get("Retry-After");
    throw new Error(`${detail}${retry ? ` 建议 ${retry} 秒后重试。` : ""}${requestId ? `\n请求编号：${requestId}` : ""}`);
  }
  return { data, requestId: response.headers.get("X-Request-ID") };
}

function resetWorkspace() {
  state.revision += 1;
  resetFeedback();
  $("feedback-view").value = "mine"; $("feedback-rating").value = "";
  clearTimeout(state.polling);
  state.bases = []; state.kb = null; state.documents = []; state.jobs = [];
  state.conversations = []; renderConversations();
  state.loading = false;
  $("kb-list").replaceChildren(element("p", "登录后查看可访问的知识库", "sidebar-empty"));
  $("kb-count").textContent = "—";
  $("current-kb").textContent = "尚未选择";
  $("document-kb").textContent = "知识库文档";
  $("permission").textContent = "请先登录";
  $("upload-form").reset();
  $("selected-file").textContent = "尚未选择文件";
  clearConversation(); renderDocuments(); renderJobs(); updateControls();
}
function clearConversation() {
  clearCitationPreview();
  state.snapshotEpoch += 1; $("snapshot-dialog").close(); $("snapshot-content").replaceChildren();
  state.conversationId = null;
  $("conversation-list").value = "";
  const welcome = $("welcome");
  $("answer-area").replaceChildren(welcome);
  welcome.hidden = false;
  $("question").value = "";
}
function renderBases() {
  $("kb-count").textContent = state.bases.length;
  $("kb-list").replaceChildren();
  if (!state.bases.length) $("kb-list").append(element("p", "当前账号没有可访问的知识库，请联系管理员授权。", "sidebar-empty"));
  if (state.bases.length) {
    const all = element("button", "全部可访问知识库", `kb-button${state.kb === null ? " selected" : ""}`);
    all.setAttribute("aria-pressed", String(state.kb === null));
    all.addEventListener("click", () => selectBase(null));
    $("kb-list").append(all);
  }
  for (const kb of state.bases) {
    const button = element("button", undefined, `kb-button${kb.id === state.kb?.id ? " selected" : ""}`);
    button.append(element("span", "", "kb-marker"), element("span", kb.name));
    button.setAttribute("aria-pressed", String(kb.id === state.kb?.id));
    button.addEventListener("click", () => selectBase(kb));
    $("kb-list").append(button);
  }
}
async function connect() {
  const revision = ++state.revision;
  state.loading = true; updateControls(); notice("正在加载你的知识库…");
  try {
    const { data } = await api("/api/knowledge-bases");
    if (!sameContext(revision)) return;
    state.bases = data.items;
    $("login-dialog").close();
    notice(); renderBases();
    if (state.bases.length) await selectBase(null);
    else notice("当前账号尚未获得知识库访问权限，请联系管理员。");
  } catch (error) { if (revision === state.revision) errorNotice(error); }
  finally { state.loading = false; updateControls(); }
}
async function selectBase(kb) {
  const revision = ++state.revision;
  state.loading = true;
  clearTimeout(state.polling);
  state.kb = kb; state.documents = []; state.jobs = [];
  resetFeedback();
  state.conversations = []; renderConversations();
  $("current-kb").textContent = kb?.name || "全部可访问知识库";
  $("document-kb").textContent = kb?.name || "请选择文档所属知识库";
  $("permission").textContent = kb ? (permissions[kb.permission] || kb.permission) : "按当前账号权限检索";
  $("upload-form").reset();
  $("selected-file").textContent = !kb ? "上传或管理文档，请先选择左侧的具体知识库。" : (editable() ? "尚未选择文件" : "你拥有只读权限，可查看文档和提问。");
  clearConversation(); renderBases(); renderDocuments(); renderJobs(); notice(); updateControls();
  try { await Promise.all([refreshDocuments(), refreshConversations()]); if (sameContext(revision)) { if (state.tab === "feedback") await refreshFeedback(); if (state.tab === "quality") await qualityWorkspace.refresh(); if (state.tab === "verification") await verificationWorkspace.refresh(); } }
  finally { if (revision === state.revision) { state.loading = false; updateControls(); } }
}
function conversationPath() { return state.kb ? `/api/knowledge-bases/${state.kb.id}/conversations` : "/api/conversations"; }
function renderConversations() {
  const select = $("conversation-list");
  const first = element("option", "新对话"); first.value = "";
  select.replaceChildren(first);
  for (const item of state.conversations) {
    const option = element("option", item.title); option.value = item.id; select.append(option);
  }
  select.value = state.conversationId || "";
}
async function refreshConversations() {
  if (!canAsk()) return;
  const revision = state.revision;
  try {
    const { data } = await api(conversationPath());
    if (!sameContext(revision)) return;
    state.conversations = data.items; renderConversations();
  } catch (error) { if (sameContext(revision)) errorNotice(error); }
}
function renderAnswer(container, data, requestId) {
  const answer = container.querySelector(".message-text"); answer.classList.remove("loading"); answer.textContent = data.answer;
  if (!data.answerable && data.refusal_reason) container.append(element("p", data.refusal_reason, "answer-meta"));
  const citations = element("div", undefined, "citation-list");
  for (const citation of data.citations || []) {
    const button = element("button", `[${citation.citation_id}] ${citation.knowledge_base_name ? `${citation.knowledge_base_name} · ` : ""}${citation.document_name} · ${citation.section_path} ↗`, "citation");
    button.type = "button";
    button.disabled = !citation.knowledge_base_id || !citation.document_id || !citation.chunk_id;
    button.addEventListener("click", () => previewCitation(citation));
    citations.append(button);
  }
  container.append(citations);
  const seconds = data.timings ? `${(data.timings.total_ms / 1000).toFixed(1)} 秒 · ` : "";
  container.append(element("div", `${seconds}${data.citations?.length || 0} 个来源${requestId ? ` · 请求编号 ${requestId}` : ""}`, "answer-meta"));
  if (data.run_id) addFeedbackControls(container, data.run_id);
  else if (data.snapshot_status === "unavailable") container.append(element("p", "本次回答快照暂不可用，无法关联反馈。", "answer-meta"));
}

function resetFeedback() {
  qualityWorkspace.reset();
  verificationWorkspace.reset();
  state.feedbackEpoch += 1; state.snapshotEpoch += 1; state.feedbackCursor = null;
  $("feedback-list").replaceChildren(element("p", "选择范围后查看反馈。", "empty-row"));
  $("feedback-more").hidden = true;
  $("snapshot-dialog").close(); $("snapshot-content").replaceChildren(); $("snapshot-meta").textContent = "";
}
function addFeedbackControls(container, runId, existing = null) {
  const controls = existing || element("div", undefined, "answer-feedback");
  controls.replaceChildren();
  for (const [rating, label] of [["up", "赞 · 有帮助"], ["down", "踩 · 待改进"]]) {
    const button = element("button", label, "secondary"); button.type = "button";
    button.addEventListener("click", () => editFeedback(container, controls, runId, rating)); controls.append(button);
  }
  const preview = element("button", "查看问答快照", "secondary"); preview.type = "button";
  preview.addEventListener("click", () => previewSnapshot(runId)); controls.append(preview);
  if (!existing) container.append(controls);
}
async function editFeedback(container, controls, runId, rating) {
  const revision = state.revision;
  const epoch = controls.feedbackEpoch = (controls.feedbackEpoch || 0) + 1;
  controls.replaceChildren(element("span", "正在加载评价…", "answer-meta"));
  try {
    const { data } = await api(`/api/qa/runs/${encodeURIComponent(runId)}`);
    if (!sameContext(revision) || controls.feedbackEpoch !== epoch) return;
    const form = element("form", undefined, "feedback-form");
    form.append(element("strong", rating === "up" ? "这次回答有帮助" : "这次回答需要改进"));
    const reason = element("select"); reason.setAttribute("aria-label", "问题原因");
    for (const [value, label] of Object.entries(feedbackReasons)) { const option = element("option", label); option.value = value; reason.append(option); }
    reason.value = data.feedback?.reason || "incorrect"; reason.hidden = rating !== "down";
    const comment = element("textarea"); comment.rows = 2; comment.maxLength = 2000;
    comment.placeholder = "可补充问题或改进建议（选填）"; comment.setAttribute("aria-label", "反馈说明"); comment.value = data.feedback?.comment || "";
    const label = element("label", undefined, "feedback-consent");
    const shared = element("input"); shared.type = "checkbox"; shared.checked = data.feedback?.share_with_admins || false;
    label.append(shared, element("span", "提交给相关知识库管理员，允许查看本次问题、答案、必要历史和证据。未勾选时仅自己可见。"));
    const exportLabel = element("label", undefined, "feedback-consent");
    const allowExport = element("input"); allowExport.type = "checkbox"; allowExport.checked = data.feedback?.allow_dataset_export || false; allowExport.disabled = !shared.checked;
    shared.addEventListener("change", () => { allowExport.disabled = !shared.checked; if (!shared.checked) allowExport.checked = false; });
    exportLabel.append(allowExport, element("span", "另行允许审核后的问题、参考答案和必要证据导出，用于本租户内部回归评测。"));
    const submit = element("button", "提交评价", "primary"); submit.type = "submit";
    const cancel = element("button", "取消", "secondary"); cancel.type = "button";
    const restore = () => addFeedbackControls(container, runId, controls);
    cancel.addEventListener("click", restore);
    const status = element("p", "", "answer-meta"); status.setAttribute("role", "status");
    form.append(reason, comment, label, exportLabel, submit, cancel, status);
    form.addEventListener("submit", async event => {
      event.preventDefault(); submit.disabled = true; cancel.disabled = true;
      try {
        await api(`/api/qa/runs/${encodeURIComponent(runId)}/feedback`, { method: "PUT", body: JSON.stringify({ rating, reason: rating === "down" ? reason.value : null, comment: comment.value, share_with_admins: shared.checked, allow_dataset_export: shared.checked && allowExport.checked }) });
        if (!sameContext(revision)) return;
        restore(); controls.append(element("span", rating === "up" ? "已记录：有帮助" : "已记录：待改进", "answer-meta"));
      } catch (error) { if (sameContext(revision)) { status.textContent = error.message; errorNotice(error); } }
      finally { submit.disabled = false; cancel.disabled = false; }
    });
    controls.replaceChildren(form);
  } catch (error) {
    if (sameContext(revision) && controls.feedbackEpoch === epoch) { controls.replaceChildren(element("span", error.message, "answer-meta")); const retry = element("button", "重试", "secondary"); retry.type = "button"; retry.addEventListener("click", () => editFeedback(container, controls, runId, rating)); controls.append(retry); errorNotice(error); }
  }
}
async function refreshFeedback(more = false) {
  if (!canAsk()) return;
  const revision = state.revision, epoch = ++state.feedbackEpoch;
  const params = new URLSearchParams({ view: $("feedback-view").value || "mine" });
  if (state.kb) params.set("knowledge_base_id", state.kb.id);
  if ($("feedback-rating").value) params.set("rating", $("feedback-rating").value);
  if (more && state.feedbackCursor) { params.set("before", state.feedbackCursor.before); params.set("before_id", state.feedbackCursor.before_id); }
  else { state.feedbackCursor = null; $("feedback-list").replaceChildren(element("p", "正在加载…", "empty-row")); }
  $("feedback-more").disabled = true;
  try {
    const { data } = await api(`/api/qa/feedback?${params}`);
    if (!sameContext(revision) || epoch !== state.feedbackEpoch) return;
    if (!more) $("feedback-list").replaceChildren();
    for (const item of data.items) {
      const card = element("article", undefined, "feedback-card");
      card.append(element("strong", item.question), element("p", `${item.rating === "up" ? "有帮助" : "待改进"}${item.reason ? ` · ${feedbackReasons[item.reason]}` : ""} · ${item.share_with_admins ? "已提交给管理员" : "仅自己可见"}`, "answer-meta"));
      if (item.comment) card.append(element("p", item.comment));
      card.append(element("p", item.answer_preview, "feedback-excerpt"));
      const preview = element("button", "查看快照", "secondary"); preview.addEventListener("click", () => previewSnapshot(item.run_id)); card.append(preview);
      if (params.get("view") === "shared") { const review = element("button", "审核与标注", "secondary"); review.addEventListener("click", () => qualityWorkspace.open(item.run_id)); card.append(review); }
      if (params.get("view") === "mine") {
        const remove = element("button", "撤回评价", "secondary");
        remove.addEventListener("click", async () => {
          remove.disabled = true;
          try { await api(`/api/qa/runs/${encodeURIComponent(item.run_id)}/feedback`, { method: "DELETE" }); if (sameContext(revision)) await refreshFeedback(); }
          catch (error) { if (sameContext(revision)) errorNotice(error); }
          finally { remove.disabled = false; }
        }); card.append(remove);
      }
      $("feedback-list").append(card);
    }
    if (!more && !data.items.length) $("feedback-list").append(element("p", "当前范围暂无可访问的反馈。", "empty-row"));
    state.feedbackCursor = data.next_cursor; $("feedback-more").hidden = !data.next_cursor;
  } catch (error) { if (sameContext(revision) && epoch === state.feedbackEpoch) { if (!more) $("feedback-list").replaceChildren(element("p", "反馈加载失败，请刷新重试。", "empty-row")); errorNotice(error); } }
  finally { if (epoch === state.feedbackEpoch) $("feedback-more").disabled = false; }
}
async function previewSnapshot(runId) {
  const revision = state.revision, epoch = ++state.snapshotEpoch;
  $("snapshot-content").replaceChildren(); $("snapshot-meta").textContent = "正在加载…";
  if (!$("snapshot-dialog").open) $("snapshot-dialog").showModal();
  try {
    const { data } = await api(`/api/qa/runs/${encodeURIComponent(runId)}`);
    if (!sameContext(revision) || epoch !== state.snapshotEpoch || !$("snapshot-dialog").open) return;
    $("snapshot-meta").textContent = `请求 ${data.request_id} · ${new Date(data.created_at).toLocaleString()} · ${data.snapshot_status === "captured" ? "上下文已保存" : "证据快照不完整或未采集"}`;
    const target = $("snapshot-content");
    target.append(element("h3", "原问题"), element("p", data.question), element("h3", "检索问题"), element("p", data.retrieval_question), element("h3", "当次回答"), element("pre", data.response.answer));
    for (const history of data.snapshot.history || []) target.append(element("h3", "必要历史"), element("p", history.question), element("pre", history.answer));
    for (const evidence of data.snapshot.context?.items || []) {
      target.append(element("h3", `[${evidence.citation_id}] ${evidence.document_name} · ${evidence.section_path}`), element("p", `文档版本 ${evidence.document_version ?? "未记录"} · 索引 ${evidence.vector_generation ?? "未记录"}`, "answer-meta"), element("pre", evidence.content));
    }
    if (!data.snapshot.context?.items?.length) target.append(element("p", "本次没有可展示的上下文证据。", "answer-meta"));
  } catch (error) { if (sameContext(revision) && epoch === state.snapshotEpoch) { $("snapshot-content").replaceChildren(); $("snapshot-meta").textContent = error.message; errorNotice(error); } }
}
let previewRevision = 0;
function clearCitationPreview() {
  previewRevision += 1;
  $("citation-dialog").close();
  $("citation-title").textContent = "引用原文";
  $("citation-meta").textContent = "";
  $("citation-content").textContent = "";
}
async function previewCitation(citation) {
  const revision = state.revision, preview = ++previewRevision;
  $("citation-title").textContent = "引用原文";
  $("citation-meta").textContent = "正在加载…";
  $("citation-content").textContent = "";
  if (!$("citation-dialog").open) $("citation-dialog").showModal();
  try {
    const ids = [citation.knowledge_base_id, citation.document_id, citation.chunk_id].map(encodeURIComponent);
    const parent = citation.parent_id ? `?parent_id=${encodeURIComponent(citation.parent_id)}` : "";
    const { data } = await api(`/api/knowledge-bases/${ids[0]}/documents/${ids[1]}/chunks/${ids[2]}${parent}`);
    if (!sameContext(revision) || preview !== previewRevision) return;
    $("citation-title").textContent = data.document_name;
    $("citation-meta").textContent = `版本 ${data.document_version} · ${data.section_path}`;
    $("citation-content").textContent = data.content;
  } catch (error) {
    if (revision === state.revision && preview === previewRevision) {
      if (!state.auth.authenticated) { errorNotice(error); return; }
      $("citation-meta").textContent = error.message || "原文加载失败，请重试。";
    }
  }
}
async function restoreConversation(id) {
  if (!id) { clearConversation(); updateControls(); return; }
  const revision = ++state.revision;
  const previousId = state.conversationId;
  state.loading = true; updateControls(); notice();
  try {
    const { data } = await api(`${conversationPath()}/${id}`);
    if (!sameContext(revision)) return;
    clearConversation(); state.conversationId = id; $("conversation-list").value = id;
    for (const turn of data.turns) {
      message("user", turn.question); renderAnswer(message("assistant", ""), { ...turn.result, run_id: turn.run_id });
    }
    if (data.next_before) notice("当前显示最近 100 轮，追问使用最近 6 轮上下文。");
  } catch (error) {
    if (revision === state.revision) { $("conversation-list").value = previousId || ""; errorNotice(error); }
  } finally { if (revision === state.revision) { state.loading = false; updateControls(); } }
}
async function refreshDocuments(automatic = false) {
  if (!state.kb) return;
  const revision = state.revision;
  const path = `/api/knowledge-bases/${state.kb.id}`;
  if (!automatic) { state.loading = true; updateControls(); }
  try {
    const hadActiveJobs = state.jobs.some(job => ["queued", "running"].includes(job.status));
    const results = await Promise.all([api(`${path}/documents`), editable() ? api(`${path}/indexing-jobs?limit=20`) : Promise.resolve({ data: { items: [] } })]);
    if (!sameContext(revision)) return;
    state.documents = results[0].data.items; state.jobs = results[1].data.items;
    const hasActiveJobs = state.jobs.some(job => ["queued", "running"].includes(job.status));
    if (hadActiveJobs && !hasActiveJobs) {
      // The worker can finish between the two requests; read documents once more.
      const latest = await api(`${path}/documents`);
      if (!sameContext(revision)) return;
      state.documents = latest.data.items;
    }
    renderDocuments(); renderJobs();
    clearTimeout(state.polling);
    if (hasActiveJobs) {
      state.polling = setTimeout(() => refreshDocuments(true), 5000);
    }
  } catch (error) { if (revision === state.revision) errorNotice(error); }
  finally { if (revision === state.revision) { state.loading = false; updateControls(); } }
}
function statusNode(status) { return element("span", statuses[status] || status, `status ${status}`); }
function formatDate(date) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(date)); }
function renderDocuments() {
  const body = $("documents-body"); body.replaceChildren();
  $("document-count").textContent = state.documents.length;
  if (!state.documents.length) {
    const row = element("tr"), cell = element("td", state.kb ? "这里还没有文档。上传第一份资料，开始积累团队知识。" : "登录并选择知识库后查看文档", "empty-row");
    cell.colSpan = 5; row.append(cell); body.append(row); return;
  }
  for (const doc of state.documents) {
    const row = element("tr");
    const name = element("td"); name.append(element("span", "▤  "), element("span", doc.file_name));
    const status = element("td"); status.append(statusNode(doc.status));
    const action = element("td");
    if (editable()) {
      const button = element("button", "删除", "delete-button");
      button.addEventListener("click", () => {
        state.deleting = { document: doc, kbId: state.kb.id, revision: state.revision };
        $("delete-description").textContent = doc.file_name;
        $("delete-dialog").showModal();
      });
      action.append(button);
    } else action.textContent = "只读";
    row.append(name, status, element("td", `v${doc.version} / ${doc.chunk_count} 块`), element("td", formatDate(doc.updated_at)), action);
    body.append(row);
  }
}
function renderJobs() {
  $("jobs-section").hidden = !editable(); $("jobs-list").replaceChildren();
  if (editable() && !state.jobs.length) $("jobs-list").append(element("p", "暂无索引任务", "sidebar-empty"));
  for (const job of state.jobs) {
    const container = element("div", undefined, "job"), info = element("div", undefined, "job-info");
    const doc = state.documents.find(item => item.id === job.document_id);
    info.append(element("div", doc?.file_name || `文档 ${job.document_id}`), element("p", `目标版本 v${job.target_version} · 尝试 ${job.attempt_count}/${job.max_attempts} · ${formatDate(job.updated_at)}`));
    if (job.last_error) info.append(element("p", job.last_error));
    const actions = element("div", undefined, "job-actions"); actions.append(statusNode(job.status));
    if (job.status === "failed") {
      const retry = element("button", "重试", "secondary");
      retry.addEventListener("click", async () => {
        const revision = state.revision;
        retry.disabled = true;
        try {
          await api(`/api/knowledge-bases/${state.kb.id}/indexing-jobs/${job.id}/retry`, { method: "POST" });
          if (sameContext(revision)) { notice("索引任务已重新加入队列。"); await refreshDocuments(); }
        } catch (error) { if (revision === state.revision) errorNotice(error); }
        finally { retry.disabled = false; }
      });
      actions.append(retry);
    }
    container.append(info, actions); $("jobs-list").append(container);
  }
}
function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll("[data-tab]").forEach(button => { button.classList.toggle("active", button.dataset.tab === tab); button.setAttribute("aria-current", button.dataset.tab === tab ? "page" : "false"); });
  $("qa-panel").hidden = tab !== "qa"; $("documents-panel").hidden = tab !== "documents";
  $("feedback-panel").hidden = tab !== "feedback";
  $("quality-panel").hidden = tab !== "quality";
  $("verification-panel").hidden = tab !== "verification";
  $("page-name").textContent = { qa: "知识问答", documents: "文档管理", feedback: "问答反馈", quality: "质量工作台", verification:"评测与改进" }[tab];
  $("heading").textContent = { qa: "把团队知识，变成可靠答案。", documents: "让团队的经验，有序沉淀。", feedback: "让每一次反馈，都有据可查。", quality: "把发现的问题，变成改进的依据。", verification:"让改进经过验证，再关闭问题。" }[tab];
  $("subheading").textContent = { qa: "从内部文档中查找答案，每一个结论都有据可循。", documents: "管理知识库资料，追踪索引进度，让新知识随时可用。", feedback: "关联当次答案与证据，为持续改进留下可靠线索。", quality: "审核反馈、核对标准，逐步积累可追溯的回归样本。", verification:"对比固定集与反馈集，核对答案、耗时和用量，保留验证依据。" }[tab];
  if (tab === "feedback") refreshFeedback();
  if (tab === "quality") qualityWorkspace.refresh();
  if (tab === "verification") verificationWorkspace.refresh(); else verificationWorkspace.pause();
}
function message(role, text) {
  $("welcome").hidden = true;
  const container = element("article", undefined, `message ${role}`);
  container.append(element("div", role === "user" ? "你" : "✧ 知序 · 知识助手", "message-label"), element("div", text, "message-text"));
  $("answer-area").append(container);
  container.scrollIntoView({ block: "nearest" });
  return container;
}

async function checkHealth() {
  try {
    const response = await fetch("/health/ready", { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw new Error();
    $("health").classList.add("online"); $("health").lastChild.textContent = "服务在线";
  } catch { $("health").classList.remove("online"); $("health").lastChild.textContent = "服务暂不可用"; }
}

document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => setTab(button.dataset.tab)));
document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => {
  if (!canAsk()) { $("login-dialog").showModal(); return; }
  if (!state.asking) { $("question").value = button.dataset.question; $("question").focus(); }
}));
$("session-button").addEventListener("click", () => {
  if (state.auth?.authenticated) { state.auth.logout(); resetWorkspace(); notice("已退出当前工作空间。"); }
  else $("login-dialog").showModal();
});
$("oidc-login").addEventListener("click", async () => {
  if (!state.auth) { notice("登录配置尚未加载，请刷新页面。", true); return; }
  try { await state.auth.login(); } catch (error) { $("login-help").textContent = error.message; }
});
$("token-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.auth) return;
  try { state.auth.useToken($("token").value); $("token").value = ""; resetWorkspace(); $("login-dialog").close(); await connect(); }
  catch (error) { $("login-help").textContent = error.message; }
});
$("refresh-button").addEventListener("click", () => refreshDocuments());
$("feedback-refresh").addEventListener("click", () => refreshFeedback());
$("feedback-view").addEventListener("change", () => refreshFeedback());
$("feedback-rating").addEventListener("change", () => refreshFeedback());
$("feedback-more").addEventListener("click", () => refreshFeedback(true));
$("new-conversation").addEventListener("click", () => { clearConversation(); updateControls(); notice("已开始新对话，发送问题后自动保存。追问使用最近 6 轮上下文。"); });
$("conversation-list").addEventListener("change", event => restoreConversation(event.target.value));
$("delete-conversation").addEventListener("click", async () => {
  if (!state.conversationId || state.asking || !window.confirm("删除这段对话及其全部历史？此操作无法撤销。")) return;
  const revision = state.revision;
  state.loading = true; updateControls();
  try {
    await api(`${conversationPath()}/${state.conversationId}`, { method: "DELETE" });
    if (sameContext(revision)) { clearConversation(); await refreshConversations(); notice("对话记录已删除。"); }
  } catch (error) { if (revision === state.revision) errorNotice(error); }
  finally { if (revision === state.revision) { state.loading = false; updateControls(); } }
});
$("upload-file").addEventListener("change", () => { $("selected-file").textContent = $("upload-file").files[0]?.name || "尚未选择文件"; updateControls(); });
$("upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  const file = $("upload-file").files[0];
  if (!file || !editable() || state.uploading) return;
  if (!/\.(pdf|docx|txt|md)$/i.test(file.name)) { notice("请选择 PDF、DOCX、TXT 或 Markdown 文件。", true); return; }
  if (file.size > state.config.max_upload_bytes) { notice("文件超过单次上传大小限制，请选择较小的文件。", true); return; }
  const revision = state.revision;
  state.uploading = true; updateControls();
  const form = new FormData(); form.append("file", file);
  try {
    const { data } = await api(`/api/knowledge-bases/${state.kb.id}/documents`, { method: "POST", body: form });
    if (!sameContext(revision)) return;
    notice(`已接收「${data.document.file_name}」，正在等待后台索引。完成后可用于问答。`);
    $("upload-form").reset(); $("selected-file").textContent = "尚未选择文件";
    await refreshDocuments();
  } catch (error) { if (revision === state.revision) errorNotice(error); }
  finally { state.uploading = false; updateControls(); }
});
$("delete-cancel").addEventListener("click", () => $("delete-dialog").close());
$("delete-confirm").addEventListener("click", async () => {
  const target = state.deleting;
  if (!target || !sameContext(target.revision)) { $("delete-dialog").close(); return; }
  $("delete-confirm").disabled = true;
  try {
    await api(`/api/knowledge-bases/${target.kbId}/documents/${target.document.id}`, { method: "DELETE" });
    $("delete-dialog").close();
    if (sameContext(target.revision)) { notice("文档已删除，后续问答将不再检索该文档。历史回答仍保留原引用。"); await refreshDocuments(); }
  } catch (error) { $("delete-dialog").close(); if (target.revision === state.revision) errorNotice(error); }
  finally { $("delete-confirm").disabled = false; state.deleting = null; }
});
$("question-form").addEventListener("submit", async event => {
  event.preventDefault();
  const question = $("question").value.trim();
  if (!question || !canAsk() || state.asking || state.loading) return;
  const revision = state.revision;
  state.asking = true; updateControls(); notice();
  message("user", question); $("question").value = "";
  const pending = message("assistant", "正在检索资料并生成答案，请稍候…");
  pending.querySelector(".message-text").classList.add("loading");
  try {
    if (!state.conversationId) {
      const { data } = await api(conversationPath(), { method: "POST" });
      if (!sameContext(revision)) return;
      state.conversationId = data.id;
      state.conversations.unshift(data); renderConversations();
    }
    const { data, requestId } = await api("/api/qa", { method: "POST", body: JSON.stringify({ knowledge_base_id: state.kb?.id || null, question, conversation_id: state.conversationId }) });
    if (!sameContext(revision)) return;
    renderAnswer(pending, data, requestId);
    await refreshConversations();
  } catch (error) {
    if (revision === state.revision) {
      if (!state.auth.authenticated) errorNotice(error);
      else { pending.classList.add("error"); const text = pending.querySelector(".message-text"); text.classList.remove("loading"); text.textContent = error.name === "TimeoutError" ? "请求超时，请稍后重新提问。" : error.message; }
    }
  } finally { state.asking = false; updateControls(); }
});
$("question").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("question-form").requestSubmit(); }
});

async function init() {
  try {
    const response = await fetch("/ui/config");
    if (!response.ok) throw new Error("无法加载登录配置，请检查 API 服务。");
    state.config = await response.json(); state.auth = new AuthSession(state.config);
    $("upload-description").textContent = `支持 PDF、DOCX、TXT、Markdown · 单文件最大 ${Math.round(state.config.max_upload_bytes / 1048576)} MB`;
    if (await state.auth.callback()) await connect();
    else notice("登录企业账号后，即可在有权限的知识库中提问。", false);
  } catch (error) { notice(error.message, true); }
  updateControls(); await checkHealth();
  setInterval(checkHealth, 60000);
}
init();
