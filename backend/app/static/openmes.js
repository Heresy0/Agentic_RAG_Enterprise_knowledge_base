// Choices bind the shared manufacturing QA context; history keeps its frozen fields.
export function createOpenMESWorkspace({ $, element, api, errorNotice, sameContext, state, invalidate, onSelection, onRefresh }) {
  let epoch = 0;
  const current = (version, revision) => version === epoch && sameContext(revision) && state.mode === "manufacturing";
  function clear() {
    epoch += 1;
    $("openmes-execution").hidden = true;
    $("openmes-batch").replaceChildren(); $("openmes-step").replaceChildren(); $("openmes-context").replaceChildren();
    $("openmes-mapping").hidden = true; $("openmes-mapping").replaceChildren();
  }
  function choices(id, rows, key, label, title, selected) {
    const first = element("option", title); first.value = ""; $(id).replaceChildren(first);
    for (const row of rows) { const option = element("option", label(row)); option.value = row[key]; $(id).append(option); }
    $(id).value = selected || "";
  }
  function renderContext(context, historical = false) {
    $("openmes-execution").hidden = false;
    const target = $("openmes-context"); target.replaceChildren(element("h3", context.work_order_number));
    for (const [entity, row, identity] of [["工单", context, "source_record_id"], ["批次", context.batch, "batch_id"], ["步骤", context.step, "step_id"]]) {
      if (!row) continue;
      target.append(element("h4", `${entity} · 来源记录 ${row[identity]}`));
      if (row.name) target.append(element("p", row.name));
      if (row.lot_number) target.append(element("p", `批号：${row.lot_number}`));
      target.append(element("p", `登记状态：${row.status}`), element("p", `实际开工：${row.actual_started_at ?? "未知"}`));
    }
    target.append(element("p", "工单级实际开工未由本来源提供；批次与步骤时间不能代填，也不能证明检验或质量签署已完成。", "answer-meta"));
    target.append(element("p", `${historical ? "回答当次快照；重新核验会开始新对话" : "本次已核验来源"}：${context.source_system_id} · ${context.last_verified_at || "—"}`, "answer-meta"));
    if (historical) {
      $("openmes-mapping").hidden = true; $("openmes-mapping").replaceChildren();
      const batch = context.batch, step = context.step;
      choices("openmes-batch", batch ? [batch] : [], "batch_id", row => `历史批次 ${row.batch_id}`, "工单级", batch?.batch_id);
      choices("openmes-step", step ? [step] : [], "step_id", row => `历史步骤 ${row.step_id}`, "批次级", step?.step_id);
      $("openmes-batch").disabled = $("openmes-step").disabled = true;
    }
  }
  async function select(order, selected, context) {
    clear(); if (!order) return;
    const version = ++epoch, revision = state.revision;
    $("openmes-execution").hidden = false; $("openmes-context").textContent = "正在核验批次与步骤选择…";
    $("openmes-batch").disabled = $("openmes-step").disabled = true;
    try {
      const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(order)}/batches`);
      if (!current(version, revision)) return;
      if (selected.batch_id && !data.items.some(row => row.batch_id === selected.batch_id)) throw new Error("所选批次不可访问。");
      choices("openmes-batch", data.items, "batch_id", row => `批次 ${row.batch_number} · ${row.lot_number || row.batch_id} · ${row.status}`, "工单级（未选择批次）", selected.batch_id);
      let steps = [];
      if (selected.batch_id) {
        const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(order)}/batches/${encodeURIComponent(selected.batch_id)}/steps`);
        if (!current(version, revision)) return;
        steps = data.items;
        if (selected.step_id && !steps.some(row => row.step_id === selected.step_id)) throw new Error("所选步骤不可访问。");
      }
      choices("openmes-step", steps, "step_id", row => `${row.step_number}. ${row.name} · ${row.status}`, "批次级（未选择步骤）", selected.step_id);
      renderContext(context);
      $("openmes-batch").disabled = false; $("openmes-step").disabled = !selected.batch_id;
      if (selected.step_id && state.kb?.permission === "admin") renderMapping(order, selected, context, version, revision);
    } catch (error) { if (version === epoch && revision === state.revision) { invalidate("执行来源或权限无法核验，请重新查询工单。"); errorNotice(error); } }
  }
  function renderMapping(order, selected, context, version, revision) {
    const target = $("openmes-mapping"), kb = state.kb.id;
    target.hidden = false; target.replaceChildren(element("h4", "步骤指导书审核映射"), element("p", "为当前工单、批次和步骤明确指定指导书版本。保存草稿后逐项复核，批准后用于作业指导；不会修改 MES 记录。", "answer-meta"));
    const base = `/api/manufacturing/work-orders/${encodeURIComponent(order)}/batches/${encodeURIComponent(selected.batch_id)}/steps/${encodeURIComponent(selected.step_id)}/guidance-mapping`;
    const suffix = `?knowledge_base_id=${encodeURIComponent(kb)}`;
    const failed = error => {
      if (version !== epoch || revision !== state.revision) return;
      target.replaceChildren(element("p", "审核映射无法核验，请重新加载工单后查看。", "answer-meta"));
      if (error.status === 401 || error.status === 409) invalidate("来源或登录状态已变化，请重新查询工单。");
      errorNotice(error);
    };
    const load = element("button", "加载审核映射", "secondary"); load.type = "button"; target.append(load);
    load.addEventListener("click", async () => {
      load.disabled = true;
      try {
        const { data } = await api(base + suffix);
        if (!current(version, revision)) return;
        const { data: candidates } = await api(base + "/candidates" + suffix);
        if (!current(version, revision)) return;
        if (candidates.source_revision !== context.source_revision) { const error = new Error("来源已变化，请先重新核验工单。"); error.status = 409; throw error; }
        const form = element("div"), choice = element("select"), reason = element("textarea"), status = element("p", `当前修订 ${data.revision} · ${data.mapping?.state || "未配置"}`, "answer-meta");
        reason.maxLength = 2000; reason.value = data.mapping?.review_note || "";
        choice.append(element("option", "选择已发布并复核的指导书及工艺编码"));
        choice.children[0].value = "";
        candidates.items.forEach((row, index) => {
          const option = element("option", `${row.document_code} · 业务版本 ${row.business_revision} · ${row.operation_code} · 文档版本 ${row.document_version}`); option.value = String(index); choice.append(option);
          if (row.document_id === data.mapping?.data.document_id && row.operation_code === data.mapping?.data.operation_code) choice.value = String(index);
        });
        const label = element("label", "指导书与工艺编码"); label.append(choice);
        const note = element("label", "审核依据与说明"); note.append(reason); form.append(status, label, note);
        const buttons = [];
        for (const [stateName, title] of [["draft", "保存草稿"], ["reviewed", "复核并启用"], ["withdrawn", "撤回映射"]]) {
          const button = element("button", title, "secondary"); button.type = "button";
          button.disabled = stateName === "reviewed" && data.mapping?.state !== "draft" || stateName === "withdrawn" && !data.mapping;
          button.addEventListener("click", async () => {
            const row = choice.value === "" ? null : candidates.items[Number(choice.value)];
            if (!reason.value.trim() || stateName !== "withdrawn" && !row) { status.textContent = "请选择指导书并填写审核依据。"; return; }
            buttons.forEach(item => { item.disabled = true; });
            try {
              await api(base + suffix, { method: "PUT", body: JSON.stringify({ expected_revision: data.revision, state: stateName, review_note: reason.value.trim(),
                ...(stateName === "withdrawn" ? {} : { document_id: row.document_id, operation_code: row.operation_code, expected_source_revision: context.source_revision }) }) });
              if (!current(version, revision)) return;
              await onRefresh();
            } catch (error) { failed(error); }
          });
          buttons.push(button); form.append(button);
        }
        for (const row of data.history) form.append(element("p", `修订 ${row.revision} · ${row.state} · ${row.data.document_code} ${row.data.business_revision} · 审核者 ${row.actor_user_id} · ${row.created_at} · ${row.review_note}`, "answer-meta"));
        target.replaceChildren(target.children[0], target.children[1], load, form);
      } catch (error) { failed(error); }
    });
  }
  function fieldValue(context, field) {
    if (Object.prototype.hasOwnProperty.call(context, field)) return context[field];
    const fields = { lot_number: "lot_number", name: "name", status: "status", actual_started_at: "actual_started_at", start_precision: "actual_start_precision", updated_at: "source_updated_at" };
    for (const prefix of ["batch", "step"]) {
      if (!field.startsWith(prefix + "_")) continue;
      const suffix = field.slice(prefix.length + 1), key = ["id", "number"].includes(suffix) ? prefix + "_" + suffix : fields[suffix];
      return context[prefix]?.[key];
    }
    return context[field];
  }
  $("openmes-batch").addEventListener("change", () => onSelection({ batch_id: $("openmes-batch").value || null, step_id: null }));
  $("openmes-step").addEventListener("change", () => onSelection({ batch_id: state.manufacturing.batchId, step_id: $("openmes-step").value || null }));
  $("openmes-refresh").addEventListener("click", () => onRefresh());
  return { select, clear, renderContext, fieldValue };
}
