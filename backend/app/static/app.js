import { AuthSession } from "./auth.js";
import { createQualityWorkspace } from "./quality.js";
import { createVerificationWorkspace } from "./verification.js";
import { createOpenMESWorkspace } from "./openmes.js";

const $ = id => document.getElementById(id);
const statuses = { ready: "已就绪", pending: "等待索引", queued: "排队中", running: "索引中", succeeded: "已完成", failed: "失败", cancelled: "已取消" };
const permissions = { viewer: "只读", editor: "可编辑", admin: "管理员" };
const state = { auth: null, config: null, bases: [], kb: null, documents: [], jobs: [], conversations: [], conversationId: null, revision: 0, tab: "qa", asking: false, uploading: false, loading: false, polling: null, deleting: null, feedbackCursor: null, feedbackEpoch: 0, snapshotEpoch: 0 };
state.mode = "knowledge";
state.manufacturing = { orders: [], nextCursor: null, orderId: null, context: null, epoch: 0, listEpoch: 0,
  selectionSupported: false, operationId: null, jobCardId: null, operations: [], jobCards: [], jobCursor: null, cardListEpoch: 0 };
const feedbackReasons = { incorrect: "答案错误", incomplete: "遗漏条件或要点", citation: "引用不正确", false_refusal: "不该拒答", outdated: "资料过期", other: "其他问题" };
const qualityWorkspace = createQualityWorkspace({ $, element, api, state, sameContext, errorNotice, onImprovement: id => verificationWorkspace.createForCase(id) });
const verificationWorkspace = createVerificationWorkspace({ $, element, api, state, sameContext, errorNotice });
const openmesWorkspace = createOpenMESWorkspace({ $, element, api, state, sameContext, errorNotice, invalidate: invalidateManufacturingView,
  onSelection: selectManufacturingBatch, onRefresh: () => loadManufacturingSelection(true, true) });

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
  $("manufacturing-panel").hidden = state.mode !== "manufacturing";
  $("user-work-conditions").hidden = state.mode === "manufacturing";
  $("qa-mode").disabled = !state.auth?.authenticated;
  for (const id of ["manufacturing-order", "manufacturing-refresh", "manufacturing-more"]) $(id).disabled = !canAsk() || state.loading;
  $("manufacturing-selection").hidden = !state.manufacturing.selectionSupported;
  $("manufacturing-operation").disabled = !canAsk() || state.loading || !state.manufacturing.orderId || !state.manufacturing.operations.length;
  $("manufacturing-job-card").disabled = !canAsk() || state.loading || !state.manufacturing.operationId;
  $("manufacturing-job-cards-more").disabled = !canAsk() || state.loading;
  qualityWorkspace.updateControls();
  verificationWorkspace.updateControls();
  $("question").disabled = !canAsk() || state.asking || state.loading;
  for (const id of ["work-model", "work-process", "work-date", "work-order"]) $(id).disabled = !canAsk() || state.asking || state.loading;
  $("ask-button").disabled = !canAsk() || state.asking || state.loading;
  if (state.mode === "manufacturing" && !state.manufacturing.context) $("ask-button").disabled = true;
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
  const response = await fetch(path, { ...requestOptions, headers, signal: AbortSignal.timeout(path.endsWith("/qa") ? 180000 : 120000), credentials: "same-origin" });
  if (epoch !== state.auth.epoch) throw new Error("登录状态已改变，请重试。");
  let data = null;
  if (response.status !== 204) {
    try { data = responseType === "blob" && response.ok ? await response.blob() : responseType === "text" && response.ok ? await response.text() : await response.json(); } catch { throw new Error(`服务返回了无法解析的响应（HTTP ${response.status}），请检查 API 日志。`); }
  }
  if (!response.ok) {
    if (response.status === 401) state.auth.clear();
    const requestId = response.headers.get("X-Request-ID");
    const detail = typeof data?.detail === "string" ? data.detail : Array.isArray(data?.detail) ? data.detail.map(item => item.msg).join("；") : data?.detail?.message || `请求失败（HTTP ${response.status}）`;
    const retry = response.headers.get("Retry-After");
    const error = new Error(`${detail}${retry ? ` 建议 ${retry} 秒后重试。` : ""}${requestId ? `\n请求编号：${requestId}` : ""}`);
    error.status = response.status; error.errorCode = data?.detail?.error_code; throw error;
  }
  return { data, requestId: response.headers.get("X-Request-ID") };
}

function resetWorkspace() {
  state.revision += 1;
  state.asking = false; state.mode = "knowledge"; $("qa-mode").value = "knowledge"; resetManufacturing();
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
function clearConversation({ preserveWorkContext = false } = {}) {
  clearCitationPreview();
  clearDocumentReview();
  state.snapshotEpoch += 1; $("snapshot-dialog").close(); $("snapshot-content").replaceChildren();
  state.conversationId = null;
  $("manufacturing-applicability").replaceChildren(); $("manufacturing-applicability").hidden = true;
  $("conversation-list").value = "";
  const welcome = $("welcome");
  $("answer-area").replaceChildren(welcome);
  welcome.hidden = false;
  $("question").value = "";
  if (!preserveWorkContext) for (const id of ["work-model", "work-process", "work-date", "work-order"]) $(id).value = "";
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
  state.asking = false; resetManufacturing();
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
  try { await Promise.all([refreshDocuments(), refreshConversations(), state.mode === "manufacturing" ? refreshManufacturingOrders() : Promise.resolve()]); if (sameContext(revision)) { if (state.tab === "feedback") await refreshFeedback(); if (state.tab === "quality") await qualityWorkspace.refresh(); if (state.tab === "verification") await verificationWorkspace.refresh(); } }
  finally { if (revision === state.revision) { state.loading = false; updateControls(); } }
}
function conversationPath() { return state.mode === "manufacturing" ? "/api/manufacturing/conversations" : state.kb ? `/api/knowledge-bases/${state.kb.id}/conversations` : "/api/conversations"; }
function conversationUrl(id = "") { return conversationPath() + (id ? `/${encodeURIComponent(id)}` : "") + (state.mode === "manufacturing" && state.kb ? `?knowledge_base_id=${encodeURIComponent(state.kb.id)}` : ""); }

function resetManufacturing() {
  openmesWorkspace.clear(); state.manufacturing.batchStepSupported = false;
  state.manufacturing.batchId = null; state.manufacturing.stepId = null;
  state.manufacturing.listEpoch += 1;
  state.manufacturing.epoch += 1; state.manufacturing.orders = []; state.manufacturing.nextCursor = null;
  state.manufacturing.orderId = null; state.manufacturing.context = null;
  state.manufacturing.selectionSupported = false; state.manufacturing.operationId = null; state.manufacturing.jobCardId = null;
  state.manufacturing.operations = []; state.manufacturing.jobCards = []; state.manufacturing.jobCursor = null;
  state.manufacturing.cardListEpoch += 1; renderManufacturingSelectors();
  const first = element("option", "选择有权访问的工单"); first.value = ""; $("manufacturing-order").replaceChildren(first);
  $("manufacturing-order").value = ""; $("manufacturing-more").hidden = true;
  $("manufacturing-context").replaceChildren(element("p", "选择工单后查看可信来源字段。"));
  $("manufacturing-applicability").replaceChildren(); $("manufacturing-applicability").hidden = true;
}
function invalidateManufacturingView(text) {
  ++state.revision; state.asking = false; state.loading = false;
  resetManufacturing(); clearConversation(); resetFeedback(); state.conversations = []; renderConversations();
  $("manufacturing-context").textContent = text; updateControls();
}
async function refreshManufacturingOrders(more = false) {
  const revision = state.revision, epoch = ++state.manufacturing.listEpoch;
  try {
    const cursor = more && state.manufacturing.nextCursor ? `?cursor=${encodeURIComponent(state.manufacturing.nextCursor)}` : "";
    const { data } = await api(`/api/manufacturing/work-orders${cursor}`);
    if (!sameContext(revision) || epoch !== state.manufacturing.listEpoch || state.mode !== "manufacturing") return;
    state.manufacturing.orders = more ? [...state.manufacturing.orders, ...data.items] : data.items;
    state.manufacturing.nextCursor = data.next_cursor;
    state.manufacturing.selectionSupported = data.selection_supported === true;
    state.manufacturing.batchStepSupported = data.execution_kind === "batch-step";
    const first = element("option", "选择有权访问的工单"); first.value = "";
    $("manufacturing-order").replaceChildren(first);
    for (const order of state.manufacturing.orders) {
      const option = element("option", `${order.work_order_id} · ${order.product_model || "型号未知"} · ${order.operation_code || "工序未知"}`);
      option.value = order.work_order_id; $("manufacturing-order").append(option);
    }
    $("manufacturing-order").value = state.manufacturing.orderId || "";
    $("manufacturing-more").hidden = !data.next_cursor;
    renderManufacturingSelectors(); updateControls();
    return true;
  } catch (error) { if (sameContext(revision) && epoch === state.manufacturing.listEpoch) { invalidateManufacturingView("工单来源或权限无法确认，请重新查询。"); errorNotice(error); } return false; }
}
function renderWorkOrder(context, historical = false) {
  if (historical) openmesWorkspace.clear();
  if (context.work_order_number) openmesWorkspace.renderContext(context, historical);
  const target = $("manufacturing-context"); target.replaceChildren();
  target.append(element("h3", `${historical ? "历史工单快照" : "已核验工单"} · ${context.work_order_id}`));
  for (const [name, label] of [["product_model", "型号"], ["operation_code", "工序"], ["actual_started_at", context.execution ? "工单实际开工（选版依据）" : "实际开工"], ["actual_start_precision", "工单时间精度"], ["route_revision", "路线修订"], ["source_system_id", "来源"], ["source_revision", "源修订"], ["last_verified_at", "最后核验时间"]]) {
    target.append(element("p", `${label}：${context[name] ?? "未知"}`));
  }
  if (context.execution) {
    const execution = context.execution;
    target.append(element("h3", `所选工序记录 · ${execution.operation_id}`));
    for (const [key, label] of [["operation_actual_started_at", "工序实际开工"], ["operation_start_precision", "工序时间精度"], ["operation_status", "工序状态"]]) target.append(element("p", `${label}：${execution[key] ?? "未知"}`));
    target.append(element("h3", execution.job_card_id ? `所选作业卡 · ${execution.job_card_id}` : "尚未选择具体作业卡"));
    if (execution.job_card_id) for (const [key, label] of [["job_card_actual_started_at", "作业卡实际开工"], ["job_card_start_precision", "作业卡时间精度"], ["job_card_status", "作业卡状态"]]) target.append(element("p", `${label}：${execution[key] ?? "未知"}`));
    target.append(element("p", "工单、工序、作业卡的实际开工分别保留；未知值不从其他层级补齐。", "answer-meta"));
  }
  target.append(element("p", historical ? "本卡片为当次快照；下一轮会重新核验来源。" : "字段来自工单系统，不能在此改写；核验后来源仍可能变化。", "answer-meta"));
}
async function selectManufacturingOrder(id) {
  state.manufacturing.batchId = null; state.manufacturing.stepId = null;
  state.manufacturing.orderId = id || null; state.manufacturing.operationId = null; state.manufacturing.jobCardId = null;
  state.manufacturing.operations = []; state.manufacturing.jobCards = []; state.manufacturing.jobCursor = null;
  renderManufacturingSelectors();
  return loadManufacturingSelection(true, false);
}
function manufacturingSelection(value = state.manufacturing) {
  return { ...(value.operationId ? { operation_id: value.operationId } : {}), ...(value.jobCardId ? { job_card_id: value.jobCardId } : {}),
    ...(value.batchId ? { batch_id: value.batchId } : {}), ...(value.stepId ? { step_id: value.stepId } : {}) };
}
async function selectManufacturingBatch(selected) {
  state.manufacturing.batchId = selected.batch_id; state.manufacturing.stepId = selected.step_id;
  return loadManufacturingSelection(false, false);
}
function renderManufacturingSelectors() {
  const value = state.manufacturing;
  $("manufacturing-selection").hidden = !value.selectionSupported;
  const op = element("option", "工单级（未选择工序）"); op.value = ""; $("manufacturing-operation").replaceChildren(op);
  for (const row of value.operations) { const option = element("option", `${row.operation_code} · ${row.operation_id} · ${row.status}`); option.value = row.operation_id; $("manufacturing-operation").append(option); }
  if (value.operationId && !value.operations.some(row => row.operation_id === value.operationId)) { const selected = element("option", `历史选择 · ${value.operationId}`); selected.value = value.operationId; $("manufacturing-operation").append(selected); }
  $("manufacturing-operation").value = value.operationId || "";
  const card = element("option", "未选择作业卡（工序级）"); card.value = ""; $("manufacturing-job-card").replaceChildren(card);
  for (const row of value.jobCards) { const option = element("option", `${row.job_card_id} · ${row.status} · ${row.actual_started_at ?? "实际开工未登记"}`); option.value = row.job_card_id; $("manufacturing-job-card").append(option); }
  if (value.jobCardId && !value.jobCards.some(row => row.job_card_id === value.jobCardId)) { const selected = element("option", `已选作业卡 · ${value.jobCardId}`); selected.value = value.jobCardId; $("manufacturing-job-card").append(selected); }
  $("manufacturing-job-card").value = value.jobCardId || ""; $("manufacturing-job-cards-more").hidden = !value.jobCursor;
}
async function selectManufacturingOperation(id) {
  state.manufacturing.operationId = id || null; state.manufacturing.jobCardId = null;
  state.manufacturing.jobCards = []; state.manufacturing.jobCursor = null; renderManufacturingSelectors();
  return loadManufacturingSelection(false, true);
}
async function selectManufacturingJobCard(id) {
  state.manufacturing.jobCardId = id || null;
  return loadManufacturingSelection(false, false);
}
async function moreManufacturingJobCards() {
  const value = state.manufacturing, revision = state.revision, epoch = ++value.cardListEpoch;
  if (!value.orderId || !value.operationId || !value.jobCursor) return;
  try {
    const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(value.orderId)}/operations/${encodeURIComponent(value.operationId)}/job-cards?cursor=${encodeURIComponent(value.jobCursor)}`);
    if (!sameContext(revision) || epoch !== state.manufacturing.cardListEpoch) return;
    value.jobCards = [...value.jobCards, ...data.items]; value.jobCursor = data.next_cursor; renderManufacturingSelectors(); updateControls();
  } catch (error) { if (sameContext(revision) && epoch === state.manufacturing.cardListEpoch) { invalidateManufacturingView("作业卡来源或权限无法确认，请重新查询。"); errorNotice(error); } }
}
async function loadManufacturingSelection(loadOperations, loadCards) {
  openmesWorkspace.clear();
  const id = state.manufacturing.orderId;
  const revision = ++state.revision, epoch = ++state.manufacturing.epoch;
  state.manufacturing.cardListEpoch += 1;
  state.asking = false; clearConversation(); state.manufacturing.context = null;
  clearCitationPreview(); $("manufacturing-context").replaceChildren(element("p", id ? "正在核验工单…" : "请选择工单。")); updateControls();
  if (!id) return;
  try {
    const value = state.manufacturing, selected = manufacturingSelection(value);
    if ((!value.selectionSupported && selected.operation_id) || (!value.batchStepSupported && selected.batch_id)) throw new Error("当前来源不支持这类执行选择，请重新选择工单。");
    if (value.selectionSupported && loadOperations) {
      const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(id)}/operations`);
      if (!sameContext(revision) || epoch !== state.manufacturing.epoch) return;
      value.operations = data.items;
    }
    if (value.selectionSupported && loadCards && value.operationId) {
      const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(id)}/operations/${encodeURIComponent(value.operationId)}/job-cards`);
      if (!sameContext(revision) || epoch !== state.manufacturing.epoch) return;
      value.jobCards = data.items; value.jobCursor = data.next_cursor;
    }
    const params = new URLSearchParams(selected).toString();
    const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(id)}/context${params ? `?${params}` : ""}`);
    if (!sameContext(revision) || epoch !== state.manufacturing.epoch || state.mode !== "manufacturing") return;
    state.manufacturing.context = data; renderManufacturingSelectors(); renderWorkOrder(data);
    if (state.manufacturing.batchStepSupported) await openmesWorkspace.select(id, selected, data);
  } catch (error) { if (sameContext(revision) && epoch === state.manufacturing.epoch) { invalidateManufacturingView("工单来源或权限无法确认，请重新查询。"); errorNotice(error); } }
  finally { if (sameContext(revision)) updateControls(); }
}
function renderApplicability(value, runId = null) {
  const target = $("manufacturing-applicability"); target.replaceChildren(); target.hidden = !value;
  if (!value) return;
  const labels = { applicable: "适用文件已核验", needs_context: "待补齐或复核条件", no_evidence: "资料不足", conflict: "选版依据冲突" };
  target.append(element("h3", labels[value.state] || value.state));
  if (value.document_code) target.append(element("p", `${value.document_code} · 业务版本 ${value.business_revision}`));
  for (const doc of value.documents || []) target.append(element("p", `${doc.usage === "comparison_only" ? "仅供版本对比 · " : doc.usage === "conflict_evidence" ? "冲突依据 · " : ""}${doc.document_code || doc.document_id} · 业务版本 ${doc.business_revision ?? "未知"} · 技术版本 ${doc.document_version} · 制造审核修订 ${doc.manufacturing_revision}`, "answer-meta"));
  if (value.rule_ids?.length) target.append(element("p", `依据规则：${value.rule_ids.join("、")}`));
  const gaps = { product_model: "产品型号", operation_code: "工序", actual_started_at: "实际开工时间", route_revision: "工艺路线修订", change_notice_refs: "书面通知", manufacturing_review: "制造资料审核", source_anchors: "原文来源", catalog_scope: "目录范围", supersedes_rule_ids: "通知替代关系" };
  if (value.gaps?.length) target.append(element("p", `待处理：${value.gaps.map(name => gaps[name] || name).join("、")}`));
  for (const evidence of value.evidence || []) {
    const button = element("button", `查看选版依据 · ${evidence.document_name || evidence.document_id}`, "citation"); button.type = "button";
    button.addEventListener("click", () => previewCitation(evidence, runId)); target.append(button);
  }
}
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
    const { data } = await api(conversationUrl());
    if (!sameContext(revision)) return;
    state.conversations = data.items; renderConversations();
  } catch (error) { if (sameContext(revision)) errorNotice(error); }
}
function renderAnswer(container, data, requestId) {
  if (state.mode === "manufacturing" && data.work_order_context) { state.manufacturing.context = data.work_order_context; renderWorkOrder(data.work_order_context); renderApplicability(data.applicability, data.run_id); }
  const answer = container.querySelector(".message-text"); answer.classList.remove("loading"); answer.textContent = data.answer;
  if (data.response_type === "clarify") container.append(element("p", `请补充：${(data.missing_fields || []).join("、")}。回复后会继续当前问题。`, "answer-meta"));
  if (!data.answerable && data.refusal_reason) container.append(element("p", data.refusal_reason, "answer-meta"));
  const citations = element("div", undefined, "citation-list");
  for (const citation of data.citations || []) {
    const button = element("button", `[${citation.citation_id}] ${citation.knowledge_base_name ? `${citation.knowledge_base_name} · ` : ""}${citation.document_name} · ${citation.section_path} ↗`, "citation");
    button.type = "button";
    button.disabled = !citation.knowledge_base_id || !citation.document_id || !citation.chunk_id;
    button.addEventListener("click", () => previewCitation(citation, data.run_id));
    citations.append(button);
  }
  for (const citation of data.business_citations || []) {
    const button = element("button", `[${citation.citation_id}] 工单字段 · ${citation.field_name} ↗`, "citation"); button.type = "button";
    button.addEventListener("click", () => previewBusinessCitation(citation, data.run_id)); citations.append(button);
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
    if (data.snapshot.manufacturing) {
      target.append(element("h3", "工单与选版快照"), element("pre", JSON.stringify(data.snapshot.manufacturing, null, 2)));
    }
    if (!data.snapshot.context?.items?.length) target.append(element("p", "本次没有可展示的上下文证据。", "answer-meta"));
    if (data.snapshot.pipeline) {
      const stages = element("details"); stages.append(element("summary", "检索阶段记录（候选ID、名次与分数）"),
        element("pre", JSON.stringify(data.snapshot.pipeline, null, 2))); target.append(stages);
    }
  } catch (error) { if (sameContext(revision) && epoch === state.snapshotEpoch) { $("snapshot-content").replaceChildren(); $("snapshot-meta").textContent = error.message; errorNotice(error); } }
}
let previewRevision = 0;
function clearCitationPreview() {
  previewRevision += 1;
  $("citation-dialog").close();
  $("citation-title").textContent = "引用原文";
  $("citation-meta").textContent = "";
  $("citation-content").textContent = "";
  for (const node of $("citation-dialog").querySelectorAll?.(".citation-source") || []) node.remove();
  if (sourceBlobUrl) URL.revokeObjectURL(sourceBlobUrl);
  sourceBlobUrl = null;
}
async function previewCitation(citation, runId = null) {
  const revision = state.revision, preview = ++previewRevision;
  $("citation-title").textContent = "引用原文";
  $("citation-meta").textContent = "正在加载…";
  $("citation-content").textContent = "";
  if (!$("citation-dialog").open) $("citation-dialog").showModal();
  try {
    if (runId) await api(`/api/qa/runs/${encodeURIComponent(runId)}`);
    const ids = [citation.knowledge_base_id, citation.document_id, citation.chunk_id].map(encodeURIComponent);
    const params = new URLSearchParams();
    if (citation.parent_id) params.set("parent_id", citation.parent_id);
    if (citation.document_version) params.set("expected_version", citation.document_version);
    const suffix = params.size ? `?${params}` : "";
    const { data } = await api(`/api/knowledge-bases/${ids[0]}/documents/${ids[1]}/chunks/${ids[2]}${suffix}`);
    if (!sameContext(revision) || preview !== previewRevision) return;
    $("citation-title").textContent = data.document_name;
    $("citation-meta").textContent = `版本 ${data.document_version} · ${data.section_path}`;
    $("citation-content").textContent = data.content;
    $("citation-meta").textContent += data.page_start ? ` · 物理页 ${data.page_start}–${data.page_end}` : "";
    if (data.quality?.review_required) $("citation-meta").textContent += " · OCR识别结果仍需核对原件";
    const source = element("div"); $("citation-content").after ? $("citation-content").after(source) : $("citation-dialog").append(source);
    source.className = "citation-source";
    sourceLink(source, citation.knowledge_base_id, { id: data.document_id, file_name: data.document_name, mime_type: data.mime_type, version: data.document_version }, data.page_start,
      () => sameContext(revision) && preview === previewRevision && $("citation-dialog").open);
  } catch (error) {
    if (revision === state.revision && preview === previewRevision) {
      if (!state.auth.authenticated) { errorNotice(error); return; }
      if (state.mode === "manufacturing" && [403, 404, 409].includes(error.status)) { invalidateManufacturingView("引用来源无法核验，请重新查询工单。"); errorNotice(error); return; }
      $("citation-meta").textContent = error.message || "原文加载失败，请重试。";
    }
  }
}
async function previewBusinessCitation(citation, runId) {
  clearCitationPreview();
  const revision = state.revision, preview = previewRevision;
  $("citation-title").textContent = "工单字段来源"; $("citation-meta").textContent = "正在核验访问权限…"; $("citation-dialog").showModal();
  try {
    let verified;
    if (runId) {
      const { data } = await api(`/api/qa/runs/${encodeURIComponent(runId)}`);
      verified = data.response?.business_citations?.find(item => item.citation_id === citation.citation_id);
    } else {
      const params = new URLSearchParams({ ...(citation.operation_id ? { operation_id: citation.operation_id } : {}), ...(citation.job_card_id ? { job_card_id: citation.job_card_id } : {}),
        ...(citation.batch_id ? { batch_id: citation.batch_id } : {}), ...(citation.step_id ? { step_id: citation.step_id } : {}) }).toString();
      const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(citation.work_order_id)}/context${params ? `?${params}` : ""}`);
      if (data.source_revision !== citation.source_revision || data.payload_sha256 !== citation.payload_sha256) throw new Error("来源已变化，请重新查询；旧值不再作为当前事实展示。");
      verified = { ...citation, value: data.work_order_number ? openmesWorkspace.fieldValue(data, citation.field_name) : data.execution && Object.prototype.hasOwnProperty.call(data.execution, citation.field_name) ? data.execution[citation.field_name] : data[citation.field_name] };
    }
    if (!sameContext(revision) || preview !== previewRevision) return;
    if (!verified) throw new Error("当前快照中没有这条业务引用。");
    $("citation-title").textContent = `[${verified.citation_id}] ${verified.work_order_id} · ${verified.field_name}`;
    $("citation-meta").textContent = `${verified.source_system_id} / ${verified.source_record_id} · 源修订 ${verified.source_revision} · 最后核验 ${verified.last_verified_at} · ${runId ? "回答当次快照" : "当前来源已核对"}`;
    $("citation-content").textContent = JSON.stringify(verified.value, null, 2);
  } catch (error) { if (sameContext(revision) && preview === previewRevision) { if (state.mode === "manufacturing") { invalidateManufacturingView("业务引用无法核验，请重新查询工单。"); errorNotice(error); } else { $("citation-content").textContent = ""; $("citation-meta").textContent = error.message; } } }
}
async function restoreConversation(id) {
  if (!id) { clearConversation(); updateControls(); return; }
  const revision = ++state.revision;
  const previousId = state.conversationId;
  if (state.mode === "manufacturing") { clearConversation(); state.manufacturing.context = null; $("manufacturing-context").textContent = "正在核对历史快照的访问权限…"; }
  state.loading = true; updateControls(); notice();
  try {
    const { data } = await api(conversationUrl(id));
    if (!sameContext(revision)) return;
    clearConversation(); state.conversationId = id; $("conversation-list").value = id;
    for (const turn of data.turns) {
      message("user", turn.question); renderAnswer(message("assistant", ""), { ...turn.result, run_id: turn.run_id });
    }
    if (state.mode === "manufacturing" && data.binding) {
      state.manufacturing.orderId = data.binding.work_order_id;
      state.manufacturing.operationId = data.binding.operation_id || null; state.manufacturing.jobCardId = data.binding.job_card_id || null;
      state.manufacturing.batchId = data.binding.batch_id || null; state.manufacturing.stepId = data.binding.step_id || null;
      state.manufacturing.operations = []; state.manufacturing.jobCards = []; state.manufacturing.jobCursor = null; renderManufacturingSelectors();
      state.manufacturing.context = data.turns.at(-1)?.result?.work_order_context || { work_order_id: data.binding.work_order_id, source_revision: data.binding.source_revision };
      $("manufacturing-order").value = data.binding.work_order_id;
      renderWorkOrder(state.manufacturing.context, true);
      if (data.binding.invalidated) { state.manufacturing.context = null; notice("这段制造对话已失效，请重新查询工单并开始新对话。", true); }
    }
    if (data.next_before) notice("当前显示最近 100 轮，追问使用最近 6 轮上下文。");
  } catch (error) {
    if (revision === state.revision) { if (state.mode === "manufacturing") invalidateManufacturingView("历史快照无法访问，请重新查询工单。"); else $("conversation-list").value = previousId || ""; errorNotice(error); }
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
const publicationLabels = { draft: "待复核发布", published: "已发布", legacy_published: "历史已发布", withdrawn: "已撤回" };
let documentReviewEpoch = 0, sourceBlobUrl = null;
function clearDocumentReview() {
  documentReviewEpoch += 1;
  $("document-review-dialog").close(); $("document-review-content").replaceChildren();
  if (sourceBlobUrl) URL.revokeObjectURL(sourceBlobUrl);
  sourceBlobUrl = null;
}
async function sourceLink(target, kbId, doc, page, valid) {
  const button = element("button", doc.mime_type === "application/pdf" ? "查看原始PDF" : "下载原件", "secondary");
  target.append(button);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const { data } = await api(`/api/knowledge-bases/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(doc.id)}/source?version=${doc.version}`, { responseType: "blob" });
      if (!valid()) return;
      if (sourceBlobUrl) URL.revokeObjectURL(sourceBlobUrl);
      sourceBlobUrl = URL.createObjectURL(data);
      const link = element("a", "打开原件"); link.href = sourceBlobUrl + (page ? `#page=${page}` : "");
      link.target = "_blank"; link.rel = "noopener";
      if (doc.mime_type !== "application/pdf") link.download = doc.file_name;
      target.append(link);
    } catch (error) { if (valid()) errorNotice(error); }
    finally { button.disabled = false; }
  });
}
async function reviewDocument(doc) {
  clearDocumentReview();
  const revision = state.revision, epoch = documentReviewEpoch, kbId = state.kb.id;
  const valid = () => sameContext(revision) && epoch === documentReviewEpoch && $("document-review-dialog").open;
  const target = $("document-review-content"); $("document-review-dialog").showModal();
  $("document-review-status").textContent = "正在加载…";
  try {
    const { data } = await api(`/api/knowledge-bases/${kbId}/documents/${doc.id}/review`);
    if (!valid()) return;
    const current = data.document;
    $("document-review-status").textContent = `${current.file_name} · v${current.version} · ${publicationLabels[current.publication.state] || "待复核"}`;
    target.append(element("p", current.quality.review_required ? "存在待复核OCR，请逐页核对原件；发布不会清除识别限制。" : "请核对正文、表格、适用范围及版本后发布。"));
    sourceLink(target, kbId, current, null, valid);
    const note = element("textarea"); note.maxLength = 2000; note.placeholder = "填写复核、撤回或退回说明";
    const acknowledged = element("input"); acknowledged.type = "checkbox";
    const label = element("label", "我已核对OCR原件并知悉剩余识别限制"); label.prepend ? label.prepend(acknowledged) : label.append(acknowledged);
    target.append(note); if (current.quality.review_required) target.append(label);
    for (const [value, title] of [["published", "确认发布"], ["withdrawn", "撤回问答使用"], ["draft", "退回待复核"]]) {
      const button = element("button", title, "secondary"); button.disabled = value === "published" && current.status !== "ready";
      button.addEventListener("click", async () => {
        if (!note.value.trim()) { notice("请先填写复核说明。", true); return; }
        button.disabled = true;
        try {
          await api(`/api/knowledge-bases/${kbId}/documents/${doc.id}/publication`, { method: "PUT", body: JSON.stringify({
            expected_version: current.version, expected_revision: current.publication.revision, state: value,
            review_note: note.value, acknowledge_ocr: acknowledged.checked }) });
          if (valid()) { clearDocumentReview(); await refreshDocuments(); notice("文档发布状态已更新。"); }
        } catch (error) { if (valid()) errorNotice(error); }
        finally { button.disabled = false; }
      }); target.append(button);
    }
    let after = null;
    const blocks = element("div"), more = element("button", "加载更多分块", "secondary");
    const append = batch => {
      for (const item of batch.items) {
        const m = item.metadata; blocks.append(element("h3", `分块${item.index + 1} · ${m.section_path || "文档说明"}`),
          element("p", `${m.parser || "原生解析"} · ${m.page_start ? `物理页 ${m.page_start}–${m.page_end}` : "非分页资料"}`, "answer-meta"), element("pre", item.content));
        const audit = element("details"); audit.append(element("summary", "清洗与质量记录"), element("pre", JSON.stringify({ cleaning: m.cleaning, quality: m.quality }, null, 2))); blocks.append(audit);
      }
      after = batch.next_after; more.hidden = after === null;
    };
    more.addEventListener("click", async () => { more.disabled = true; try {
      const { data: batch } = await api(`/api/knowledge-bases/${kbId}/documents/${doc.id}/review?after_index=${after}&expected_version=${doc.version}`);
      if (valid()) append(batch);
    } catch (error) { if (valid()) errorNotice(error); } finally { more.disabled = false; } });
    target.append(blocks, more); append(data);
  } catch (error) { if (valid()) $("document-review-status").textContent = error.message; }
}
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
    status.append(element("p", publicationLabels[doc.publication?.state] || "历史已发布", "answer-meta"));
    if (doc.quality?.review_required) status.append(element("p", "OCR待复核", "answer-meta"));
    const action = element("td");
    if (editable()) {
      const review = element("button", "预览／复核", "secondary"); review.addEventListener("click", () => reviewDocument(doc)); action.append(review);
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
$("new-conversation").addEventListener("click", async () => { if (state.mode === "manufacturing" && state.manufacturing.orderId) await loadManufacturingSelection(true, true); else clearConversation(); updateControls(); notice("已开始新对话，发送问题后自动保存。追问使用最近 6 轮上下文。"); });
$("conversation-list").addEventListener("change", event => restoreConversation(event.target.value));
$("delete-conversation").addEventListener("click", async () => {
  if (!state.conversationId || state.asking || !window.confirm("删除这段对话及其全部历史？此操作无法撤销。")) return;
  const revision = state.revision;
  state.loading = true; updateControls();
  try {
    await api(conversationUrl(state.conversationId), { method: "DELETE" });
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
    notice(`已接收「${data.document.file_name}」，正在等待后台索引。完成后请预览复核并发布。`);
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
  let question = $("question").value.trim();
  if (!question || !canAsk() || state.asking || state.loading) return;
  const conditions = [["work-model", "产品／设备型号"], ["work-process", "工序"], ["work-date", "业务日期"], ["work-order", "工单编号"]]
    .filter(([id]) => $(id).value.trim()).map(([id, label]) => `${label}：${$(id).value.trim()}`);
  const manufacturing = state.mode === "manufacturing";
  if (manufacturing && !state.manufacturing.context) { notice("请先选择并核验工单。", true); return; }
  if (!manufacturing && conditions.length) question += `\n用户补充条件（若与问题冲突请先澄清）：${conditions.join("；")}`;
  if (question.length > 1000) { notice("问题与补充条件合计不能超过1000字，请适当缩短。", true); return; }
  const revision = state.revision;
  state.asking = true; updateControls(); notice();
  message("user", question); $("question").value = "";
  const pending = message("assistant", "正在检索资料并生成答案，请稍候…");
  pending.querySelector(".message-text").classList.add("loading");
  try {
    if (!state.conversationId) {
      const body = manufacturing ? JSON.stringify({ work_order_id: state.manufacturing.orderId, knowledge_base_id: state.kb?.id || null, expected_source_revision: state.manufacturing.context.source_revision, ...manufacturingSelection() }) : undefined;
      const { data } = await api(conversationPath(), { method: "POST", ...(body ? { body } : {}) });
      if (!sameContext(revision)) return;
      state.conversationId = data.id;
      state.conversations.unshift(data); renderConversations();
    }
    const input = { knowledge_base_id: state.kb?.id || null, question, conversation_id: state.conversationId };
    if (manufacturing) { input.work_order_id = state.manufacturing.orderId; input.expected_source_revision = state.manufacturing.context.source_revision; Object.assign(input, manufacturingSelection()); }
    const { data, requestId } = await api(manufacturing ? "/api/manufacturing/qa" : "/api/qa", { method: "POST", body: JSON.stringify(input) });
    if (!sameContext(revision)) return;
    renderAnswer(pending, data, requestId);
    await refreshConversations();
  } catch (error) {
    if (revision === state.revision) {
      if (!state.auth.authenticated) errorNotice(error);
      else {
        if (manufacturing && ![422, 429].includes(error.status)) { invalidateManufacturingView("来源或权限无法继续确认，请重新查询并开始新对话。"); message("assistant", error.message); }
        else { pending.classList.add("error"); const text = pending.querySelector(".message-text"); text.classList.remove("loading"); text.textContent = error.name === "TimeoutError" ? "请求超时，请稍后重新提问。" : error.message; }
      }
    }
  } finally { if (revision === state.revision) { state.asking = false; updateControls(); } }
});
$("qa-mode").addEventListener("change", async event => {
  state.mode = event.target.value === "manufacturing" ? "manufacturing" : "knowledge";
  ++state.revision; state.asking = false; resetManufacturing(); clearConversation(); state.conversations = []; renderConversations(); updateControls();
  await Promise.all([refreshConversations(), state.mode === "manufacturing" ? refreshManufacturingOrders() : Promise.resolve()]);
});
$("manufacturing-order").addEventListener("change", event => selectManufacturingOrder(event.target.value));
$("manufacturing-refresh").addEventListener("click", async () => { const id = state.manufacturing.orderId; if (await refreshManufacturingOrders() && id && state.mode === "manufacturing") await loadManufacturingSelection(true, true); });
$("manufacturing-more").addEventListener("click", () => refreshManufacturingOrders(true));
$("manufacturing-operation").addEventListener("change", event => selectManufacturingOperation(event.target.value));
$("manufacturing-job-card").addEventListener("change", event => selectManufacturingJobCard(event.target.value));
$("manufacturing-job-cards-more").addEventListener("click", () => moreManufacturingJobCards());
for (const id of ["work-model", "work-process", "work-date", "work-order"]) $(id).addEventListener("change", () => {
  if (state.conversationId && !state.asking) {
    const question = $("question").value;
    clearConversation({ preserveWorkContext: true }); $("question").value = question;
    updateControls(); notice("作业条件已变化，已开始新对话。");
  }
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
