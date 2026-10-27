// Budgeted evaluation and human verification console. No business HTML is executed.
export function createVerificationWorkspace({ $, element, api, state, sameContext, errorNotice }) {
  const statuses = { queued: "排队中", running: "执行中", succeeded: "执行完成", failed: "执行失败", cancelled: "已取消", open: "待验证", verified: "已验证，关闭时重新检查", closed: "已关闭" };
  const verdicts = { passed: "通过", failed: "存在错误", uncertain: "暂不能确定" };
  let epoch = 0, detailEpoch = 0, polling = null, jobs = [];
  const canManage = () => !!state.auth?.authenticated && (state.kb ? state.kb.permission === "admin" : state.bases.some(kb => kb.permission === "admin"));
  const live = (revision, token) => sameContext(revision) && token === detailEpoch && $("verification-dialog").open;
  const button = (text, action) => { const node = element("button", text, "secondary"); node.type = "button"; node.addEventListener("click", action); return node; };
  function updateControls() { $("verification-nav").disabled = !canManage(); $("evaluation-submit").disabled = !canManage() || !$("evaluation-consent").checked; }
  function reset() {
    epoch += 1; detailEpoch += 1; clearTimeout(polling); jobs = [];
    $("evaluation-consent").checked = false; $("verification-jobs").replaceChildren(); $("improvement-list").replaceChildren();
    $("evaluation-fixed").replaceChildren(); $("evaluation-feedback").replaceChildren();
    $("verification-dialog").close(); $("verification-detail").replaceChildren(); updateControls();
  }
  function select(values, selected = "") {
    const node = element("select"); const empty = element("option", "请选择"); empty.value = ""; node.append(empty);
    for (const item of values) { const option = element("option", item.label); option.value = item.id; node.append(option); }
    node.value = selected; return node;
  }
  function field(label, node) { const result = element("label", undefined, "quality-field"); result.append(element("span", label), node); return result; }
  function text(value, max = 2000) { const node = element("textarea"); node.value = value || ""; node.maxLength = max; node.rows = 3; return node; }
  function number(value, minimum, maximum) { const node = element("input"); node.type = "number"; node.value = String(value); node.min = minimum; node.max = maximum; node.step = ".1"; return node; }
  function dialogStart() { $("verification-detail").replaceChildren(element("p", "正在加载…")); if (!$("verification-dialog").open) $("verification-dialog").showModal(); }
  function jobDetail(data, revision, token) {
    const target = $("verification-detail"); target.replaceChildren(element("h3", `${statuses[data.status]} · ${data.completed_cases}/${data.expected_cases}`),
      element("pre", JSON.stringify(data.summary, null, 2)));
    if (data.error_type) target.append(element("p", `停止原因：${data.error_type}`, "quality-warning"));
    if (!data.current) target.append(element("p", "输入已变化，该任务不能用于当前验证。", "quality-warning"));
    for (const record of data.records || []) {
      const card = element("section", undefined, "feedback-card");
      card.append(element("h3", `${record.sample.role === "fixed" ? "固定集" : "反馈集"} · ${record.sample.question}`),
        element("p", `自动代理：${record.proxy_pass ? "通过" : "未通过"}；人工复核：${verdicts[record.human_review?.verdict] || "尚未复核"}`, "answer-meta"),
        element("h4", "人工参考答案"), element("pre", record.sample.reference_answer), element("h4", "本次实际回答"), element("pre", record.response.answer));
      for (const item of record.context || []) card.append(element("strong", `[${item.citation_id}] ${item.document_name}`), element("pre", item.content));
      if (data.status === "succeeded" && data.current) {
        const verdict = select([{id:"passed",label:"正确且证据充分"},{id:"failed",label:"存在错误"},{id:"uncertain",label:"暂不能确定"}], record.human_review?.verdict || "uncertain");
        const reason = text(record.human_review?.reason), form = element("form", undefined, "quality-form");
        const submit = element("button", "保存答案复核", "primary"); submit.type = "submit";
        form.append(field("复核结论", verdict), field("核对事实、条件及引用的说明", reason), submit);
        form.addEventListener("submit", async event => {
          event.preventDefault(); submit.disabled = true;
          try {
            const { data: updated } = await api(`/api/quality/evaluations/${data.id}/reviews/${encodeURIComponent(record.sample.id)}`, { method:"POST", body:JSON.stringify({ response_binding:record.response_binding, verdict:verdict.value, reason:reason.value }) });
            if (live(revision, token)) jobDetail(updated, revision, token);
          } catch (error) { if (live(revision, token)) errorNotice(error); }
          finally { submit.disabled = false; }
        }); card.append(form);
      }
      target.append(card);
    }
  }
  async function showJob(id) {
    const revision = state.revision, token = ++detailEpoch; dialogStart();
    try { const { data } = await api(`/api/quality/evaluations/${encodeURIComponent(id)}`); if (live(revision, token)) jobDetail(data, revision, token); }
    catch (error) { if (live(revision, token)) { $("verification-detail").replaceChildren(element("p", error.message)); errorNotice(error); } }
  }
  function improvementForm(data, revision, token) {
    const target = $("verification-detail"); target.replaceChildren(element("h3", data.id ? `${statuses[data.status]} · 修订 ${data.revision}` : "建立改进记录"));
    const description = text(data.description, 4000), ref = text(data.change_ref, 128), owner = element("input"); owner.value = data.owner_user_id || "";
    const regression = element("input"); regression.value = data.regression_case_id || "";
    const latency = number(data.policy?.max_latency_ratio || 1.5, 1, 5), tokens = number(data.policy?.max_token_ratio || 1.3, 1, 5);
    const form = element("form", undefined, "quality-form"), save = element("button", "保存改进记录", "primary"); save.type = "submit";
    form.append(field("改进说明", description), field("代码提交 / 文档版本 / 配置变更引用（仅记录）", ref), field("负责人用户ID（留空为本人）", owner),
      field("验证样本审核记录ID（留空沿用原问题；补充资料后可关联新快照）", regression),
      field("P95 最大增长倍数", latency), field("Token / 同币种估算费用最大增长倍数", tokens), save,
      element("p", "保存修改会重新开启问题并使旧验证失效。发布检查只记录结论，不自动部署服务。", "answer-meta"));
    form.addEventListener("submit", async event => {
      event.preventDefault(); save.disabled = true;
      const payload = { case_id:data.case_id, owner_user_id:owner.value || null, description:description.value, change_ref:ref.value,
        regression_case_id:regression.value || null,
        max_latency_ratio:Number(latency.value), max_token_ratio:Number(tokens.value) };
      if (data.id) payload.expected_revision = data.revision;
      try {
        const { data: updated } = await api(data.id ? `/api/quality/improvements/${data.id}` : "/api/quality/improvements", { method:data.id ? "PUT" : "POST", body:JSON.stringify(payload) });
        if (live(revision, token)) { improvementForm(updated, revision, token); if (state.tab === "verification") await refresh(); }
      } catch (error) { if (live(revision, token)) errorNotice(error); }
      finally { save.disabled = false; }
    }); target.append(form);
    if (!data.id) return;
    target.append(button("重新加载记录与历史", () => showImprovement(data.id, true)));
    const choices = jobs.filter(job => job.status === "succeeded" && job.current).map(job => ({ id:job.id, label:`${job.id.slice(0,8)} · ${new Date(job.created_at).toLocaleString()}` }));
    const baseline = select(choices, data.verification?.baseline_job_id), candidate = select(choices, data.verification?.candidate_job_id), note = text("");
    const ack = element("input"); ack.type = "checkbox"; ack.checked = false;
    const ackLabel = element("label", undefined, "feedback-consent"); ackLabel.append(ack, element("span", "已人工复核未知用量或未定价费用；确认该限制并保留说明。"));
    const report = element("pre", ""), controls = element("section", undefined, "quality-form");
    controls.append(field("基线任务", baseline), field("候选任务", candidate), field("验证或关闭说明", note), ackLabel,
      button("查看版本对比", async () => {
        try { const params = new URLSearchParams({baseline_job_id:baseline.value,candidate_job_id:candidate.value,target_case_id:data.regression_case_id || data.case_id,acknowledge_unknown_usage:String(ack.checked)});
          const { data: compared } = await api(`/api/quality/comparison?${params}`); if (live(revision, token)) report.textContent = JSON.stringify(compared,null,2);
        } catch (error) { if (live(revision, token)) errorNotice(error); }
      }), button("执行发布检查并标记已验证", async () => {
        try { const { data: updated } = await api(`/api/quality/improvements/${data.id}/verify`, { method:"POST", body:JSON.stringify({ expected_revision:data.revision, baseline_job_id:baseline.value,candidate_job_id:candidate.value,note:note.value,acknowledge_unknown_usage:ack.checked }) });
          if (live(revision, token)) { improvementForm(updated, revision, token); if (state.tab === "verification") await refresh(); }
        } catch (error) { if (live(revision, token)) errorNotice(error); }
      }), report);
    if (data.status === "verified") controls.append(button("重新检查依据并关闭问题", async () => {
      try { const { data: updated } = await api(`/api/quality/improvements/${data.id}/close`, {method:"POST",body:JSON.stringify({expected_revision:data.revision,note:note.value})});
        if (live(revision, token)) { improvementForm(updated, revision, token); if (state.tab === "verification") await refresh(); }
      } catch (error) { if (live(revision, token)) errorNotice(error); }
    }));
    target.append(controls);
    for (const event of data.history || []) { const details = element("details"); details.append(element("summary", `修订 ${event.revision} · ${new Date(event.created_at).toLocaleString()}`), element("pre", JSON.stringify(event.data,null,2))); target.append(details); }
  }
  async function showImprovement(value, existing = false) {
    const revision = state.revision, token = ++detailEpoch; dialogStart();
    try {
      const { data: latestJobs } = await api("/api/quality/evaluations");
      if (!live(revision, token)) return; jobs = latestJobs.items;
      const data = existing ? (await api(`/api/quality/improvements/${value}`)).data : {case_id:value};
      if (live(revision, token)) improvementForm(data, revision, token);
    } catch (error) { if (live(revision, token)) { $("verification-detail").replaceChildren(element("p", error.message)); errorNotice(error); } }
  }
  async function refresh() {
    clearTimeout(polling); if (!canManage()) return;
    const revision = state.revision, token = ++epoch;
    try {
      const [versions, evaluations, improvements] = await Promise.all([api("/api/quality/datasets"),api("/api/quality/evaluations"),api("/api/quality/improvements")]);
      if (!sameContext(revision) || token !== epoch) return;
      const options = versions.data.items.filter(item => item.current).map(item => ({id:item.id,label:`${item.name} · ${item.manifest.sample_count}条 · ${item.id.slice(0,8)}`}));
      for (const id of ["evaluation-fixed","evaluation-feedback"]) { const old = $(id).value, values = select(options, old); $(id).replaceChildren(...values.children); $(id).value = options.some(item => item.id === old) ? old : ""; }
      jobs = evaluations.data.items; $("verification-jobs").replaceChildren();
      for (const job of jobs) { const card = element("article",undefined,"feedback-card");
        card.append(element("strong",`${statuses[job.status]} · ${job.completed_cases}/${job.expected_cases}`),element("p",`${job.id}${job.current ? "" : " · 输入已变化"}`,"answer-meta"),button("查看结果与复核",()=>showJob(job.id)));
        if (["queued","running"].includes(job.status)) card.append(button("取消后续执行",async()=>{try { await api(`/api/quality/evaluations/${job.id}/cancel`,{method:"POST"}); if (sameContext(revision)) await refresh(); } catch(error){if(sameContext(revision))errorNotice(error);} }));
        $("verification-jobs").append(card);
      }
      $("improvement-list").replaceChildren();
      for (const row of improvements.data.items) { const card = element("article",undefined,"feedback-card"); card.append(element("strong",row.description),element("p",`${statuses[row.status]} · ${row.change_ref}`,"answer-meta"),button("查看与验证",()=>showImprovement(row.id,true))); $("improvement-list").append(card); }
      if (state.tab === "verification" && jobs.some(job=>["queued","running"].includes(job.status))) polling=setTimeout(()=>refresh(),5000);
    } catch(error){if(sameContext(revision)&&token===epoch)errorNotice(error);}
  }
  $("evaluation-consent").addEventListener("change",updateControls);
  $("verification-refresh").addEventListener("click",()=>refresh());
  $("verification-dialog").addEventListener("close",()=>{detailEpoch+=1;$("verification-detail").replaceChildren();});
  $("evaluation-form").addEventListener("submit",async event=>{
    event.preventDefault(); if(!canManage()||!$("evaluation-consent").checked)return;
    const revision=state.revision; $("evaluation-submit").disabled=true;
    try { await api("/api/quality/evaluations",{method:"POST",body:JSON.stringify({fixed_dataset_id:$("evaluation-fixed").value,feedback_dataset_id:$("evaluation-feedback").value,allow_model_calls:true,
      max_cases:Number($("evaluation-max-cases").value),max_model_calls:Number($("evaluation-max-calls").value),max_tokens:Number($("evaluation-max-tokens").value),max_seconds:Number($("evaluation-max-seconds").value)})});
      if(sameContext(revision)){ $("evaluation-consent").checked=false; await refresh(); }
    }catch(error){if(sameContext(revision))errorNotice(error);}finally{updateControls();}
  });
  return { reset, updateControls, refresh, createForCase: id=>showImprovement(id), pause:()=>{clearTimeout(polling);} };
}
