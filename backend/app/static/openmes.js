// Source fields only: no model requests, writes, or implicit job-card conversion.
export function createOpenMESWorkspace({ $, element, api, errorNotice, sameContext, state, invalidate }) {
  let order = null, batch = null, step = null, epoch = 0;
  const current = (version, revision) => version === epoch && sameContext(revision) && state.mode === "manufacturing";
  function clear() {
    epoch += 1; order = batch = step = null;
    $("openmes-execution").hidden = true;
    $("openmes-batch").replaceChildren(); $("openmes-step").replaceChildren(); $("openmes-context").replaceChildren();
  }
  function choices(id, rows, key, label, title) {
    const first = element("option", title); first.value = "";
    $(id).replaceChildren(first);
    for (const row of rows) { const option = element("option", label(row)); option.value = row[key]; $(id).append(option); }
  }
  function render(data) {
    const target = $("openmes-context"); target.replaceChildren();
    target.append(element("h3", data.record.work_order_number));
    for (const [entity, row, id] of [["工单", data.record.work_order, "source_record_id"], ["批次", data.record.batch, "batch_id"], ["步骤", data.record.step, "step_id"]]) {
      if (!row) continue;
      target.append(element("h4", `${entity} · 来源记录 ${row[id]}`));
      if (row.name) target.append(element("p", row.name));
      if (row.lot_number) target.append(element("p", `批号：${row.lot_number}`));
      target.append(element("p", `登记状态：${row.status}`), element("p", `实际开工：${row.actual_started_at ?? "未登记／未知"}`));
    }
    target.append(element("p", "各层级实际开工分别读取。开工记录不能证明检验、签署或质量放行已经完成。", "answer-meta"));
    target.append(element("p", `来源：${data.record.work_order.source_system_id} · 核验时间：${data.last_verified_at}`, "answer-meta"));
    const details = element("details"), summary = element("summary", "查看字段来源与修订"); details.append(summary);
    for (const c of data.business_citations) details.append(element("p", `${c.source_entity} ${c.source_record_id} · ${c.field_name} = ${c.value ?? "未知"} · ${c.source_revision}`, "answer-meta"));
    target.append(details);
  }
  async function load() {
    const version = ++epoch, revision = state.revision;
    $("openmes-context").textContent = "正在核验执行记录…";
    $("openmes-batch").disabled = $("openmes-step").disabled = true;
    try {
      const params = new URLSearchParams({ ...(batch ? { batch_id: batch } : {}), ...(step ? { step_id: step } : {}) });
      const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(order)}/execution-context?${params}`);
      if (current(version, revision)) render(data);
    } catch (error) { if (current(version, revision)) { invalidate("执行来源或权限无法核验，请重新查询工单。"); errorNotice(error); } }
    finally { if (current(version, revision)) { $("openmes-batch").disabled = false; $("openmes-step").disabled = !batch; } }
  }
  async function select(workOrder) {
    clear(); order = workOrder;
    if (!order) return;
    $("openmes-execution").hidden = false;
    const version = ++epoch, revision = state.revision;
    $("openmes-context").textContent = "正在读取批次…";
    $("openmes-batch").disabled = $("openmes-step").disabled = true;
    try {
      const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(order)}/batches`);
      if (!current(version, revision)) return;
      choices("openmes-batch", data.items, "batch_id", row => `批次 ${row.batch_number} · ${row.lot_number || row.batch_id} · ${row.status}`, "工单级（未选择批次）");
      choices("openmes-step", [], "step_id", row => row.name, "未选择步骤");
      await load();
    } catch (error) { if (current(version, revision)) { invalidate("批次来源或权限无法核验，请重新查询工单。"); errorNotice(error); } }
  }
  $("openmes-batch").addEventListener("change", async () => {
    batch = $("openmes-batch").value || null; step = null;
    const version = ++epoch, revision = state.revision;
    choices("openmes-step", [], "step_id", row => row.name, "未选择步骤");
    $("openmes-context").replaceChildren(); $("openmes-step").disabled = true;
    if (batch) {
      try {
        const { data } = await api(`/api/manufacturing/work-orders/${encodeURIComponent(order)}/batches/${encodeURIComponent(batch)}/steps`);
        if (!current(version, revision)) return;
        choices("openmes-step", data.items, "step_id", row => `${row.step_number}. ${row.name} · ${row.status}`, "批次级（未选择步骤）");
      } catch (error) { if (current(version, revision)) { invalidate("步骤来源或权限无法核验，请重新查询工单。"); errorNotice(error); } return; }
    }
    if (current(version, revision)) await load();
  });
  $("openmes-step").addEventListener("change", () => { step = $("openmes-step").value || null; void load(); });
  $("openmes-refresh").addEventListener("click", () => { void load(); });
  return { select, clear };
}
