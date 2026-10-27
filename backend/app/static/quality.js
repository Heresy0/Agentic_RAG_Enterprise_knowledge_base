// Human review UI. All business content is inserted as text.
export function createQualityWorkspace({ $, element, api, state, sameContext, errorNotice, onImprovement = () => {} }) {
  const labels = { pending: "待审核", confirmed: "问题成立", dismissed: "反馈不成立", needs_evidence: "需补证据" };
  const feedbackLabels = { incorrect: "答案错误", incomplete: "遗漏条件或要点", citation: "引用不正确", false_refusal: "不该拒答", outdated: "资料过期", other: "其他问题" };
  const causes = { knowledge_missing: "知识缺失", outdated: "资料过期", parsing: "解析或切分", retrieval: "召回不足", rerank: "重排错误", context: "上下文遗漏", generation: "生成错误或漏答", citation: "引用错误", false_refusal: "误拒答", ambiguous: "问题含糊", access_policy: "权限规则", other: "其他" };
  const blocked = { not_approved: "尚未通过样本审核", feedback_changed: "反馈已变化，需重新审核", export_not_authorized: "用户尚未允许回归导出", corpus_changed: "语料已变化，需重新审核" };
  const selected = new Set();
  let queueEpoch = 0, detailEpoch = 0, cursor = null;
  const canReview = () => !!state.auth?.authenticated && (state.kb ? state.kb.permission === "admin" : state.bases.some(kb => kb.permission === "admin"));
  const current = (revision, epoch) => sameContext(revision) && epoch === detailEpoch && $("quality-dialog").open;
  const button = (text, handler, className = "secondary") => { const node = element("button", text, className); node.type = "button"; node.addEventListener("click", handler); return node; };
  function updateControls() { $("quality-nav").disabled = !canReview(); $("quality-refresh").disabled = !canReview(); $("quality-export").disabled = !canReview() || !selected.size; }
  function reset() {
    queueEpoch += 1; detailEpoch += 1; cursor = null; selected.clear();
    $("quality-list").replaceChildren(); $("quality-versions").replaceChildren(); $("quality-more").hidden = true;
    $("quality-dialog").close(); $("quality-detail").replaceChildren(); $("quality-dialog-status").textContent = "";
    updateControls();
  }
  function options(values, value) {
    const select = element("select");
    for (const [key, label] of Object.entries(values)) { const option = element("option", label); option.value = key; select.append(option); }
    select.value = value; return select;
  }
  function field(text, input) { const label = element("label", undefined, "quality-field"); label.append(element("span", text), input); return label; }
  function textarea(value, max, rows = 3) { const input = element("textarea"); input.value = value || ""; input.maxLength = max; input.rows = rows; return input; }
  function appendHistory(target, rows) {
    for (const row of rows) {
      const item = element("details", undefined, "quality-history-item");
      item.append(element("summary", `第 ${row.revision} 次修订 · ${labels[row.review.review_status]} · ${new Date(row.created_at).toLocaleString()}`),
        element("p", `审核人 ${row.reviewer_user_id}`), element("pre", JSON.stringify(row.review, null, 2)));
      target.append(item);
    }
  }
  function renderDetail(data, revision, epoch) {
    const target = $("quality-detail"); target.replaceChildren();
    const source = element("section", undefined, "quality-source"), editor = element("section", undefined, "quality-editor");
    source.append(element("h3", "当次问题与回答"), element("p", data.source.question), element("p", `检索问题：${data.source.retrieval_question}`),
      element("pre", data.source.response.answer), element("h3", "用户反馈"), element("p", data.source.feedback.comment || "未填写说明"));
    source.append(element("p", `${data.source.feedback.rating === "up" ? "有帮助" : "待改进"} · ${feedbackLabels[data.source.feedback.reason] || "未分类"}`, "answer-meta"));
    source.append(element("p", `审核记录ID：${data.case.id}`, "answer-meta"));
    source.append(element("p", data.source.feedback.allow_dataset_export ? "用户允许内部回归导出" : "用户未允许内部回归导出", "answer-meta"));
    if (data.case.review_status === "confirmed") source.append(button("记录修复并进入验证", () => onImprovement(data.case.id)));
    if (data.case.source_changed) source.append(element("p", "反馈内容已变化，请依据当前反馈重新审核。", "quality-warning"));
    if (data.case.export_blocked_reason) source.append(element("p", blocked[data.case.export_blocked_reason], "answer-meta"));
    for (const item of data.source.snapshot.history || []) source.append(element("h3", "必要历史"), element("p", item.question), element("pre", item.answer));
    const form = element("form", undefined, "quality-form");
    const review = data.review;
    const status = options(labels, review.review_status), cause = options({ "": "请选择根因", ...causes }, review.root_cause || "");
    const sample = options({ draft: "样本草稿", approved: "样本审核通过" }, review.sample_status);
    const answerable = options({ "": "尚未确定", true: "有资料可回答", false: "资料不足，应拒答" }, review.expected_answerable == null ? "" : String(review.expected_answerable));
    const reason = textarea(review.review_reason, 2000), reference = textarea(review.reference_answer, 8000, 4);
    const evaluationQuestion = textarea(review.evaluation_question || data.source.retrieval_question, 1000, 2);
    const facts = textarea((review.required_facts || []).join("\n"), 16032), forbidden = textarea((review.forbidden_claims || []).join("\n"), 16032);
    reason.required = true;
    form.append(element("h3", `人工审核 · 修订 ${data.case.revision}`), field("问题审核结论", status), field("根因", cause), field("样本状态", sample),
      field("审核说明（必填）", reason), field("回归问题（可独立理解）", evaluationQuestion), field("预期可回答性", answerable), field("参考答案 / 拒答依据", reference),
      field("必答事实（每行一项，最多32项）", facts), field("禁止结论（每行一项，最多32项）", forbidden));
    const evidenceInputs = [];
    source.append(element("h3", "当次上下文与样本依据"), element("p", "批准可回答样本时，勾选依据并从对应原文中复制必要的连续摘录。", "answer-meta"));
    for (const evidence of data.source.snapshot.context?.items || []) {
      const section = element("section", undefined, "quality-evidence");
      section.append(element("strong", `[${evidence.citation_id}] ${evidence.document_name} · ${evidence.section_path}`),
        element("p", `来源版本 ${evidence.document_version ?? "未记录"}`, "answer-meta"), element("pre", evidence.content));
      const saved = (review.evidence || []).find(item => item.citation_id === evidence.citation_id);
      const choose = element("input"); choose.type = "checkbox"; choose.checked = !!saved;
      const label = element("label", undefined, "feedback-consent"); label.append(choose, element("span", "用作回归样本依据"));
      const quote = textarea(saved?.quote, 2000); quote.placeholder = "复制此片段中的连续原文，最多2000字符";
      quote.setAttribute("aria-label", `资料 ${evidence.citation_id} 的证据摘录`);
      section.append(label, quote); source.append(section); evidenceInputs.push({ choose, quote, citationId: evidence.citation_id });
    }
    const submit = element("button", "保存审核与新修订", "primary"); submit.type = "submit";
    const statusText = element("p", "", "answer-meta"); statusText.setAttribute("role", "status");
    form.append(element("p", "确认问题可先保存草稿；批准样本需补齐标准。资料不足样本不填写必答事实或支持答案的证据。", "answer-meta"), submit,
      button("重新加载", () => loadCase(data.case.id)), statusText);
    form.addEventListener("submit", async event => {
      event.preventDefault(); submit.disabled = true;
      const lines = node => node.value.split("\n").map(value => value.trim()).filter(Boolean);
      const payload = { expected_revision: data.case.revision, review_status: status.value, sample_status: sample.value, root_cause: cause.value || null,
        review_reason: reason.value, expected_answerable: answerable.value === "" ? null : answerable.value === "true", reference_answer: reference.value.trim() || null,
        evaluation_question: evaluationQuestion.value.trim() || null,
        required_facts: lines(facts), forbidden_claims: lines(forbidden), evidence: evidenceInputs.filter(item => item.choose.checked).map(item => ({ citation_id: item.citationId, quote: item.quote.value })) };
      try {
        const { data: updated } = await api(`/api/quality/cases/${encodeURIComponent(data.case.id)}`, { method: "PUT", body: JSON.stringify(payload) });
        if (!current(revision, epoch)) return;
        renderDetail(updated, revision, epoch); $("quality-dialog-status").textContent = "已保存审核，新修订已记录。";
        await refresh();
      } catch (error) { if (current(revision, epoch)) { statusText.textContent = error.message; errorNotice(error); } }
      finally { submit.disabled = false; }
    });
    const history = element("section", undefined, "quality-history"); history.append(element("h3", "修订历史")); appendHistory(history, data.history);
    let before = data.next_history_before;
    const more = button("更多修订", async () => {
      more.disabled = true;
      try {
        const { data: older } = await api(`/api/quality/cases/${encodeURIComponent(data.case.id)}?history_before=${before}`);
        if (!current(revision, epoch)) return;
        appendHistory(history, older.history); before = older.next_history_before; more.hidden = !before;
      } catch (error) { if (current(revision, epoch)) errorNotice(error); }
      finally { more.disabled = false; }
    });
    more.hidden = !before; editor.append(form, history, more); target.append(source, editor);
  }
  async function display(request) {
    const revision = state.revision, epoch = ++detailEpoch;
    $("quality-detail").replaceChildren(); $("quality-dialog-status").textContent = "正在加载审核资料…";
    if (!$("quality-dialog").open) $("quality-dialog").showModal();
    try { const { data } = await request(); if (current(revision, epoch)) { $("quality-dialog-status").textContent = ""; renderDetail(data, revision, epoch); } }
    catch (error) { if (current(revision, epoch)) { $("quality-detail").replaceChildren(); $("quality-dialog-status").textContent = error.message; errorNotice(error); } }
  }
  const open = runId => display(() => api("/api/quality/cases", { method: "POST", body: JSON.stringify({ run_id: runId }) }));
  const loadCase = caseId => display(() => api(`/api/quality/cases/${encodeURIComponent(caseId)}`));
  async function download(datasetId, kind) {
    const revision = state.revision;
    try {
      const { data } = await api(`/api/quality/datasets/${encodeURIComponent(datasetId)}/${kind}`, { responseType: kind === "samples" ? "text" : "json" });
      if (!sameContext(revision)) return;
      const blob = new Blob([kind === "samples" ? data : JSON.stringify(data, null, 2)], { type: kind === "samples" ? "application/x-ndjson" : "application/json" });
      const url = URL.createObjectURL(blob), anchor = element("a"); anchor.href = url;
      anchor.download = `feedback-regression-${datasetId}.${kind === "samples" ? "jsonl" : "manifest.json"}`;
      document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (sameContext(revision)) errorNotice(error); }
  }
  function renderVersions(items) {
    $("quality-versions").replaceChildren();
    for (const item of items) {
      const card = element("article", undefined, "feedback-card");
      card.append(element("strong", item.name), element("p", `${item.manifest.sample_count} 条 · ${new Date(item.created_at).toLocaleString()} · ${item.current ? "可导出" : "已变化，需新建版本"}`, "answer-meta"));
      if (item.current) card.append(button("下载 JSONL", () => download(item.id, "samples")), button("下载版本清单", () => download(item.id, "manifest")));
      $("quality-versions").append(card);
    }
    if (!items.length) $("quality-versions").append(element("p", "尚无可访问的样本版本。", "answer-meta"));
  }
  async function refresh(more = false) {
    if (!canReview()) { $("quality-list").replaceChildren(element("p", "请选择你管理的知识库。", "empty-row")); return; }
    const revision = state.revision, epoch = ++queueEpoch;
    const params = new URLSearchParams(); if (state.kb) params.set("knowledge_base_id", state.kb.id);
    if ($("quality-status").value) params.set("review_status", $("quality-status").value);
    if (more && cursor) { params.set("before", cursor.before); params.set("before_id", cursor.before_id); }
    else { selected.clear(); $("quality-list").replaceChildren(element("p", "正在加载…", "empty-row")); }
    $("quality-more").disabled = true; updateControls();
    try {
      const { data } = await api(`/api/quality/cases?${params}`);
      if (!sameContext(revision) || epoch !== queueEpoch) return;
      if (!more) $("quality-list").replaceChildren();
      for (const item of data.items) {
        const card = element("article", undefined, "feedback-card");
        card.append(element("strong", item.question), element("p", `${labels[item.review_status]} · ${item.sample_status === "approved" ? "样本已审核" : "样本草稿"}${item.root_cause ? ` · ${causes[item.root_cause]}` : ""}`, "answer-meta"));
        if (item.feedback.comment) card.append(element("p", item.feedback.comment));
        card.append(element("p", `${item.feedback.rating === "up" ? "有帮助" : "待改进"} · ${feedbackLabels[item.feedback.reason] || "未分类"}`, "answer-meta"));
        if (item.export_blocked_reason) card.append(element("p", blocked[item.export_blocked_reason], "answer-meta"));
        card.append(button("查看与审核", () => open(item.run_id)));
        const choose = element("input"); choose.type = "checkbox"; choose.disabled = !item.export_eligible;
        choose.addEventListener("change", () => { if (choose.checked) selected.add(item.id); else selected.delete(item.id); updateControls(); });
        const label = element("label", undefined, "feedback-consent"); label.append(choose, element("span", "加入本次回归版本")); card.append(label);
        $("quality-list").append(card);
      }
      if (!more && !data.items.length) $("quality-list").append(element("p", "当前范围暂无共享反馈。", "empty-row"));
      cursor = data.next_cursor; $("quality-more").hidden = !cursor;
      if (!more) { const { data: versions } = await api("/api/quality/datasets"); if (sameContext(revision) && epoch === queueEpoch) renderVersions(versions.items); }
    } catch (error) { if (sameContext(revision) && epoch === queueEpoch) errorNotice(error); }
    finally { if (epoch === queueEpoch) $("quality-more").disabled = false; }
  }
  $("quality-refresh").addEventListener("click", () => refresh());
  $("quality-status").addEventListener("change", () => refresh());
  $("quality-more").addEventListener("click", () => refresh(true));
  $("quality-dialog").addEventListener("close", () => { detailEpoch += 1; $("quality-detail").replaceChildren(); });
  $("quality-export").addEventListener("click", async () => {
    if (!selected.size) return;
    const revision = state.revision; $("quality-export").disabled = true;
    try {
      await api("/api/quality/datasets", { method: "POST", body: JSON.stringify({ name: $("quality-name").value.trim() || "反馈回归集", case_ids: [...selected] }) });
      if (sameContext(revision)) await refresh();
    } catch (error) { if (sameContext(revision)) errorNotice(error); }
    finally { updateControls(); }
  });
  return { reset, updateControls, refresh, open };
}
