// Real app.js handlers with an in-memory DOM and mocked HTTP responses.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

class Node {
  constructor(tag = "div") {
    this.tag = tag; this.children = []; this.listeners = {}; this.files = [];
    this.value = ""; this.textContent = ""; this.hidden = false; this.className = "";
    const classes = new Set();
    this.classList = { add: v => classes.add(v), remove: v => classes.delete(v), toggle: v => classes.has(v) ? classes.delete(v) : classes.add(v) };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  get firstChild() { return this.children[0] || this; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute() {}
  scrollIntoView() {}
  reset() {}
  click() { this.clicked = true; return this.listeners.click?.(); }
  remove() { this.removed = true; }
  close() { this.open = false; }
  showModal() { this.open = true; }
  querySelector(selector) {
    const cls = selector.slice(1);
    for (const child of this.children) {
      if (child.className.split(" ").includes(cls)) return child;
      const found = child.querySelector(selector); if (found) return found;
    }
    return null;
  }
  set innerHTML(_) { throw new Error("Unsafe HTML rendering"); }
}

function setup() {
  const nodes = new Map(), requests = [], conversations = [];
  const $ = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  let sequence = 0;
  const answer = { answer: "令牌过期后重新获取。<script>bad()</script>", answerable: true, citations: [] };
  const sandbox = {
    document: { getElementById: $, createElement: tag => new Node(tag), querySelectorAll: () => [], body: new Node("body") },
    window: { confirm: () => true }, Headers, FormData, AbortSignal, Intl, URLSearchParams, Blob,
    URL: { createObjectURL: blob => { sandbox.downloadedBlob = blob; return "blob:test"; }, revokeObjectURL: () => {} },
    clearTimeout, setTimeout,
    fetch: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, method: options.method || "GET", body });
      let payload;
      if (url.endsWith("/conversations") && options.method === "POST") {
        payload = { id: `c${++sequence}`, title: "新对话", turn_count: 0 };
        conversations.unshift(payload);
      } else if (url.endsWith("/conversations")) payload = { items: conversations };
      else if (url.includes("/conversations/")) payload = { turns: [{ question: "令牌过期了？", result: answer }] };
      else if (url === "/api/qa") payload = { ...answer, conversation_id: body.conversation_id };
      else payload = { items: [] };
      return { status: 200, ok: true, headers: new Headers(), json: async () => payload };
    },
  };
  vm.createContext(sandbox);
  const qualitySource = fs.readFileSync(path.join(__dirname, "../backend/app/static/quality.js"), "utf8").replace("export function", "function");
  const verificationSource = fs.readFileSync(path.join(__dirname, "../backend/app/static/verification.js"), "utf8").replace("export function", "function");
  const source = qualitySource + "\n" + verificationSource + "\n" + fs.readFileSync(path.join(__dirname, "../backend/app/static/app.js"), "utf8")
    .replace(/^import .*;\r?\n/gm, "").replace(/init\(\);\s*$/, "");
  vm.runInContext(source, sandbox);
  vm.runInContext('state.auth = { authenticated: true, epoch: 0, name: "Alice", token: async () => "test" }; state.kb = { id: "kb1", permission: "viewer" };', sandbox);
  const run = code => vm.runInContext(code, sandbox);
  const ask = async question => { $("question").value = question; await $("question-form").listeners.submit({ preventDefault() {} }); };
  return { $, requests, run, ask, sandbox };
}

test("first question creates a conversation, followup reuses it, new conversation resets it", async () => {
  const { $, requests, run, ask } = setup();
  await ask("令牌有效期？"); await ask("它过期怎么办？");
  const qa = requests.filter(r => r.url === "/api/qa");
  assert.equal(qa[0].body.conversation_id, "c1");
  assert.equal(qa[1].body.conversation_id, "c1");
  assert.equal(requests.filter(r => r.method === "POST" && r.url.endsWith("/conversations")).length, 1);
  $("new-conversation").listeners.click();
  assert.equal(run("state.conversationId"), null);
  await ask("新主题");
  assert.equal(requests.filter(r => r.url === "/api/qa").at(-1).body.conversation_id, "c2");
});

test("restore renders stored text safely and continues the selected conversation", async () => {
  const { $, run, ask, requests } = setup();
  await run('restoreConversation("existing")');
  assert.equal(run("state.conversationId"), "existing");
  assert.equal($("answer-area").children.at(-1).querySelector(".message-text").textContent, "令牌过期后重新获取。<script>bad()</script>");
  await ask("那如何更新？");
  assert.equal(requests.filter(r => r.url === "/api/qa")[0].body.conversation_id, "existing");
  assert.equal(requests.filter(r => r.method === "POST" && r.url.endsWith("/conversations")).length, 0);
});

test("switching knowledge base clears memory and ignores late answers from the old scope", async () => {
  const { $, run, ask, sandbox } = setup();
  await ask("先建立历史");
  const fetch = sandbox.fetch;
  let finish, started;
  const qaStarted = new Promise(resolve => { started = resolve; });
  sandbox.fetch = async (url, options) => {
    if (url === "/api/qa") { started(); return new Promise(resolve => { finish = resolve; }); }
    return fetch(url, options);
  };
  const pending = ask("旧知识库的追问"); await qaStarted;
  await run('selectBase({ id: "kb2", name: "第二个知识库", permission: "viewer" })');
  assert.equal(run("state.conversationId"), null);
  finish({ status: 200, ok: true, headers: new Headers(), json: async () => ({ answer: "不应显示", citations: [] }) });
  await pending;
  assert.equal(run("state.conversationId"), null);
  assert.equal($("answer-area").children.length, 1);
  assert.equal($("welcome").hidden, false);
});

test("logout clears the current conversation and recent conversation list", async () => {
  const { run, ask } = setup();
  await ask("一个问题");
  run("state.auth.authenticated = false; resetWorkspace()");
  assert.equal(run("state.conversationId"), null);
  assert.equal(run("state.conversations.length"), 0);
});

test("all-accessible mode uses global conversations and null KB, without enabling uploads", async () => {
  const { $, run, requests, ask } = setup();
  run('state.bases = [{ id: "kb1", name: "技术部", permission: "admin" }, { id: "kb2", name: "公共", permission: "viewer" }]');
  await run("selectBase(null)");
  assert.equal($("current-kb").textContent, "全部可访问知识库");
  assert.equal($("question").disabled, false);
  assert.equal($("upload-file").disabled, true);
  assert.equal($("upload-button").disabled, true);
  assert.equal($("kb-list").children.length, 3);
  await ask("哪些资料适用？"); await ask("它的条件呢？");
  const qa = requests.filter(r => r.url === "/api/qa");
  assert.equal(qa[0].body.knowledge_base_id, null);
  assert.equal(qa[0].body.conversation_id, qa[1].body.conversation_id);
  assert.equal(requests.filter(r => r.url === "/api/conversations" && r.method === "POST").length, 1);
  assert.equal(requests.some(r => r.url.includes("/documents")), false);
  await run('selectBase({ id: "kb1", name: "技术部", permission: "admin" })');
  assert.equal(run("state.conversationId"), null);
  assert.equal($("upload-file").disabled, false);
  await ask("仅在技术部查询");
  assert.equal(requests.filter(r => r.url === "/api/qa").at(-1).body.knowledge_base_id, "kb1");
});

test("login defaults to all-accessible mode; no readable bases disables asking", async () => {
  const { $, run, sandbox, requests } = setup();
  const original = sandbox.fetch;
  sandbox.fetch = async (url, options) => url === "/api/knowledge-bases"
    ? { status: 200, ok: true, headers: new Headers(), json: async () => ({ items: [{ id: "kb1", name: "技术部", permission: "admin" }] }) }
    : original(url, options);
  await run("connect()");
  assert.equal(run("state.kb"), null);
  assert.equal($("question").disabled, false);
  assert.equal(requests.some(r => r.url === "/api/conversations"), true);
  run("state.bases = []; state.kb = null; updateControls()");
  assert.equal($("question").disabled, true);
  assert.equal($("ask-button").disabled, true);
});

test("switching from a loading single KB to all-accessible mode does not leave questions disabled", async () => {
  const { $, run } = setup();
  run('state.bases = [{ id: "kb1", name: "技术部", permission: "admin" }]; state.loading = true');
  await run("selectBase(null)");
  assert.equal(run("state.loading"), false);
  assert.equal($("question").disabled, false);
});

test("cross-KB citations render the server KB name safely", () => {
  const { $, run } = setup();
  run('renderAnswer(message("assistant", ""), { answer: "结论", answerable: true, citations: [{ citation_id: "资料1", document_name: "手册", section_path: "条件", knowledge_base_name: "技术部<script>" }] })');
  assert.equal($("answer-area").children.at(-1).querySelector(".citation").textContent, "[资料1] 技术部<script> · 手册 · 条件 ↗");
});

test("citation preview uses authorized endpoint and renders source as text", async () => {
  const { $, run, sandbox } = setup();
  sandbox.fetch = async url => {
    assert.equal(url, "/api/knowledge-bases/kb1/documents/doc1/chunks/hash1");
    return { status: 200, ok: true, headers: new Headers(), json: async () => ({ document_name: "手册", document_version: 2, section_path: "范围", content: "<script>原文</script>" }) };
  };
  run('renderAnswer(message("assistant", ""), { answer: "结论", citations: [{ citation_id: "1", knowledge_base_id: "kb1", document_id: "doc1", chunk_id: "hash1" }] })');
  await $("answer-area").children.at(-1).querySelector(".citation").listeners.click();
  assert.equal($("citation-content").textContent, "<script>原文</script>");
  assert.equal($("citation-dialog").open, true);
  run("resetWorkspace()");
  assert.equal($("citation-content").textContent, "");
  assert.equal($("citation-dialog").open, false);
});

test("late citation response after scope switch is discarded", async () => {
  const { $, run, sandbox } = setup();
  let finish;
  sandbox.fetch = async () => new Promise(resolve => { finish = resolve; });
  const pending = run('previewCitation({ knowledge_base_id: "kb1", document_id: "doc1", chunk_id: "hash1" })');
  await new Promise(resolve => setImmediate(resolve));
  run("resetWorkspace()");
  finish({ status: 200, ok: true, headers: new Headers(), json: async () => ({ content: "旧库内容" }) });
  await pending;
  assert.equal($("citation-content").textContent, "");
});

function jsonResponse(data, status = 200) {
  return { status, ok: status < 400, headers: new Headers(), json: async () => data };
}

test("answer feedback collects a reason and explicit sharing consent", async () => {
  const { $, run, sandbox } = setup();
  let submitted;
  sandbox.fetch = async (url, options = {}) => {
    if (options.method === "PUT") { submitted = JSON.parse(options.body); return jsonResponse({}); }
    return jsonResponse({ feedback: null });
  };
  run('renderAnswer(message("assistant", ""), { answer: "答案", citations: [], run_id: "run1" })');
  const controls = $("answer-area").children.at(-1).querySelector(".answer-feedback");
  await controls.children[1].listeners.click();
  const form = controls.children[0];
  assert.equal(form.className, "feedback-form");
  assert.equal(form.children[3].children[0].checked, false);
  form.children[1].value = "incomplete";
  form.children[2].value = "遗漏了例外";
  form.children[3].children[0].checked = true;
  await form.listeners.submit({ preventDefault() {} });
  assert.deepEqual(submitted, { rating: "down", reason: "incomplete", comment: "遗漏了例外", share_with_admins: true, allow_dataset_export: false });
  assert.equal(controls.children.at(-1).textContent, "已记录：待改进");
});

test("feedback save error retains the form and enables retry", async () => {
  const { $, run, sandbox } = setup();
  sandbox.fetch = async (url, options = {}) => options.method === "PUT" ? jsonResponse({ detail: "暂时无法保存" }, 503) : jsonResponse({ feedback: null });
  run('renderAnswer(message("assistant", ""), { answer: "答案", run_id: "run1" })');
  const controls = $("answer-area").children.at(-1).querySelector(".answer-feedback");
  await controls.children[0].listeners.click();
  const form = controls.children[0];
  await form.listeners.submit({ preventDefault() {} });
  assert.equal(controls.children[0], form);
  assert.equal(form.children.find(node => node.type === "submit").disabled, false);
  assert.match(form.children.at(-1).textContent, /暂时无法保存/);
});

test("restored conversation keeps the snapshot feedback link", async () => {
  const { $, run, sandbox } = setup();
  sandbox.fetch = async () => jsonResponse({ turns: [{ question: "q", run_id: "saved-run", result: { answer: "a", citations: [] } }] });
  await run('restoreConversation("c1")');
  assert.equal($("answer-area").children.at(-1).querySelector(".answer-feedback").children.length, 3);
});

test("snapshot preview renders frozen evidence as text and logout erases it", async () => {
  const { $, run, sandbox } = setup();
  sandbox.fetch = async () => jsonResponse({ request_id: "r1", created_at: "2026-10-27T01:00:00Z", snapshot_status: "captured",
    question: "<script>问题</script>", retrieval_question: "检索问题", response: { answer: "<img onerror=bad()>" },
    snapshot: { context: { items: [{ citation_id: "1", document_name: "制度", section_path: "范围", content: "<script>证据</script>", document_version: 3 }] } } });
  await run('previewSnapshot("r1")');
  assert.equal($("snapshot-dialog").open, true);
  assert.equal($("snapshot-content").children.at(-1).textContent, "<script>证据</script>");
  assert.match($("snapshot-meta").textContent, /上下文已保存/);
  run("resetWorkspace()");
  assert.equal($("snapshot-dialog").open, false);
  assert.equal($("snapshot-content").children.length, 0);
});

test("late snapshot after a scope change cannot refill the dialog", async () => {
  const { $, run, sandbox } = setup();
  let finish;
  sandbox.fetch = async () => new Promise(resolve => { finish = resolve; });
  const pending = run('previewSnapshot("r1")');
  await new Promise(resolve => setImmediate(resolve));
  run("resetWorkspace()");
  finish(jsonResponse({ question: "旧资料" }));
  await pending;
  assert.equal($("snapshot-content").children.length, 0);
  assert.equal($("snapshot-dialog").open, false);
});

test("feedback list passes KB and rating filters and follows its cursor", async () => {
  const { $, run, sandbox } = setup();
  const requested = [];
  $("feedback-view").value = "mine"; $("feedback-rating").value = "down";
  sandbox.fetch = async url => {
    requested.push(url);
    return jsonResponse({ items: [{ id: "f1", run_id: "r1", question: "<script>问题</script>", rating: "down", reason: "incomplete", comment: "说明", answer_preview: "旧回答" }], next_cursor: requested.length === 1 ? { before: "2026-10-27T01:00:00Z", before_id: "f1" } : null });
  };
  await run("refreshFeedback()");
  assert.match(requested[0], /knowledge_base_id=kb1/);
  assert.match(requested[0], /rating=down/);
  assert.equal($("feedback-list").children[0].children[0].textContent, "<script>问题</script>");
  assert.equal($("feedback-more").hidden, false);
  await run("refreshFeedback(true)");
  assert.match(requested[1], /before_id=f1/);
  assert.equal($("feedback-more").hidden, true);
  assert.equal($("feedback-list").children.length, 2);
});

test("a later filter response wins over an earlier feedback request", async () => {
  const { $, run, sandbox } = setup();
  let finish, count = 0;
  sandbox.fetch = async () => ++count === 1 ? new Promise(resolve => { finish = resolve; }) : jsonResponse({ items: [], next_cursor: null });
  const pending = run("refreshFeedback()");
  await new Promise(resolve => setImmediate(resolve));
  await run("refreshFeedback()");
  finish(jsonResponse({ items: [{ question: "旧筛选的私有问题", rating: "up" }], next_cursor: null }));
  await pending;
  assert.equal($("feedback-list").children[0].textContent, "当前范围暂无可访问的反馈。");
});

function qualityData() {
  return { case: { id: "case1", run_id: "run1", revision: 1, review_status: "pending", sample_status: "draft", export_blocked_reason: "not_approved" },
    review: { review_status: "pending", sample_status: "draft", review_reason: "新建审核", required_facts: [], forbidden_claims: [], evidence: [] },
    source: { question: "<script>问题</script>", retrieval_question: "独立问题", response: { answer: "旧答案<script>" },
      feedback: { comment: "遗漏条件", allow_dataset_export: true }, snapshot: { context: { items: [
        { citation_id: "1", document_name: "制度<script>", section_path: "适用范围", document_version: 1, content: "必须核对例外条件<script>" }] } } },
    history: [], next_history_before: null };
}
function admin(run) { run('state.bases = [{id:"kb1", permission:"admin"}]; state.kb = state.bases[0]; updateControls()'); }

test("feedback export consent is separate and is cleared when sharing is withdrawn", async () => {
  const { $, run, sandbox } = setup();
  let submitted;
  sandbox.fetch = async (url, options = {}) => {
    if (options.method === "PUT") { submitted = JSON.parse(options.body); return jsonResponse({}); }
    return jsonResponse({ feedback: { share_with_admins: true, allow_dataset_export: true } });
  };
  run('renderAnswer(message("assistant", ""), {answer:"a",run_id:"run1"})');
  const controls = $("answer-area").children.at(-1).querySelector(".answer-feedback");
  await controls.children[0].click();
  const form = controls.children[0], sharing = form.children[3].children[0], exporting = form.children[4].children[0];
  assert.equal(exporting.checked, true);
  sharing.checked = false; sharing.listeners.change();
  assert.equal(exporting.checked, false); assert.equal(exporting.disabled, true);
  await form.listeners.submit({ preventDefault() {} });
  assert.equal(submitted.allow_dataset_export, false); assert.equal(submitted.share_with_admins, false);
});

test("quality queue requires administrator capability and uses current KB and status filters", async () => {
  const { $, run, sandbox } = setup();
  run("updateControls()"); assert.equal($("quality-nav").disabled, true);
  admin(run); assert.equal($("quality-nav").disabled, false);
  const urls = []; $("quality-status").value = "pending";
  sandbox.fetch = async url => { urls.push(url); return jsonResponse({ items: [], next_cursor: null }); };
  await run("qualityWorkspace.refresh()");
  assert.match(urls[0], /knowledge_base_id=kb1/); assert.match(urls[0], /review_status=pending/);
});

test("review form submits explicit criteria, exact quote and the revision it loaded", async () => {
  const { $, run, sandbox } = setup(); admin(run);
  let submitted;
  sandbox.fetch = async (url, options = {}) => {
    if (options.method === "PUT") { submitted = JSON.parse(options.body); const result = qualityData(); result.case.revision = 2; return jsonResponse(result); }
    if (options.method === "POST") return jsonResponse(qualityData());
    return jsonResponse({ items: [], next_cursor: null });
  };
  await run('qualityWorkspace.open("run1")');
  const form = $("quality-detail").querySelector(".quality-form");
  const field = text => form.children.find(node => node.className === "quality-field" && node.children[0].textContent === text).children[1];
  field("问题审核结论").value = "confirmed"; field("根因").value = "generation"; field("样本状态").value = "approved";
  field("审核说明（必填）").value = "确认遗漏"; field("预期可回答性").value = "true";
  field("参考答案 / 拒答依据").value = "须核对例外条件"; field("必答事实（每行一项，最多32项）").value = "例外条件";
  const evidence = $("quality-detail").querySelector(".quality-evidence");
  evidence.children[3].children[0].checked = true; evidence.children[4].value = "例外条件";
  await form.listeners.submit({ preventDefault() {} });
  assert.equal(submitted.expected_revision, 1); assert.equal(submitted.reference_answer, "须核对例外条件");
  assert.deepEqual(submitted.evidence, [{citation_id:"1",quote:"例外条件"}]);
  assert.equal($("quality-dialog-status").textContent, "已保存审核，新修订已记录。");
});

test("concurrent-review conflict keeps unsaved criteria and allows reload", async () => {
  const { $, run, sandbox } = setup(); admin(run);
  sandbox.fetch = async (url, options = {}) => options.method === "PUT" ? jsonResponse({ detail: "记录已更新，请重新加载" }, 409) : jsonResponse(qualityData());
  await run('qualityWorkspace.open("run1")');
  const form = $("quality-detail").querySelector(".quality-form");
  await form.listeners.submit({ preventDefault() {} });
  assert.equal($("quality-detail").querySelector(".quality-form"), form);
  assert.match(form.children.at(-1).textContent, /重新加载/);
  assert.equal(form.children.find(node => node.type === "submit").disabled, false);
});

test("review data renders as text and late detail after logout is discarded", async () => {
  const { $, run, sandbox } = setup(); admin(run);
  sandbox.fetch = async () => jsonResponse(qualityData());
  await run('qualityWorkspace.open("run1")');
  assert.equal($("quality-detail").querySelector(".quality-source").children[1].textContent, "<script>问题</script>");
  let finish; sandbox.fetch = async () => new Promise(resolve => { finish = resolve; });
  const pending = run('qualityWorkspace.open("run2")'); await new Promise(resolve => setImmediate(resolve));
  run("resetWorkspace()"); finish(jsonResponse(qualityData())); await pending;
  assert.equal($("quality-dialog").open, false); assert.equal($("quality-detail").children.length, 0);
});

test("approved sample selection creates a version and downloads JSONL as text", async () => {
  const { $, run, sandbox } = setup(); admin(run);
  let created;
  const version = { id:"version1", name:"v1", created_at:"2026-10-28T00:00:00Z", current:true, manifest:{sample_count:1} };
  sandbox.fetch = async (url, options = {}) => {
    if (options.method === "POST") { created = JSON.parse(options.body); return jsonResponse(version); }
    if (url.endsWith("/samples")) return { ...jsonResponse({}), text: async () => '{"id":"sample1"}\n' };
    if (url.startsWith("/api/quality/cases")) return jsonResponse({ items: [{...qualityData().case, question:"q", feedback:{}, export_eligible:true}], next_cursor:null });
    return jsonResponse({items:[version]});
  };
  await run("qualityWorkspace.refresh()");
  const choose = $("quality-list").children[0].children.at(-1).children[0]; choose.checked = true; choose.listeners.change();
  assert.equal($("quality-export").disabled, false);
  $("quality-name").value = "已审核v1"; await $("quality-export").click();
  assert.deepEqual(created, {name:"已审核v1",case_ids:["case1"]});
  const download = $("quality-versions").children[0].children.find(node => node.textContent === "下载 JSONL");
  await download.click();
  assert.equal(await sandbox.downloadedBlob.text(), '{"id":"sample1"}\n');
  assert.equal(sandbox.document.body.children.at(-1).download, "feedback-regression-version1.jsonl");
});

test("evaluation submission needs explicit consent and sends numeric budgets", async () => {
  const { $, run, sandbox } = setup(); admin(run);
  let submitted = null;
  sandbox.fetch = async (url, options = {}) => {
    if (options.method === "POST") { submitted = JSON.parse(options.body); return jsonResponse({id:"job1"}); }
    return jsonResponse({items:[]});
  };
  $("evaluation-fixed").value = "fixed"; $("evaluation-feedback").value = "feedback";
  for (const [id,value] of [["evaluation-max-cases","40"],["evaluation-max-calls","200"],["evaluation-max-tokens","1000000"],["evaluation-max-seconds","600"]]) $(id).value=value;
  await $("evaluation-form").listeners.submit({preventDefault(){}}); assert.equal(submitted,null);
  $("evaluation-consent").checked=true;$("evaluation-consent").listeners.change();
  await $("evaluation-form").listeners.submit({preventDefault(){}});
  assert.equal(submitted.allow_model_calls,true);assert.equal(submitted.max_model_calls,200);assert.equal(submitted.max_tokens,1000000);
  assert.equal($("evaluation-consent").checked,false);
});

test("evaluation answer review binds the exact returned response and renders as text", async () => {
  const { $,run,sandbox }=setup();admin(run);let review;
  const record={sample:{id:"FB-case1",role:"feedback",question:"<script>问题</script>",reference_answer:"参考条件"},response:{answer:"<img onerror=bad()>"},context:[],proxy_pass:true,response_binding:"f".repeat(64)};
  const job={id:"job1",status:"succeeded",current:true,completed_cases:1,expected_cases:1,summary:{},records:[record]};
  sandbox.fetch=async(url,options={})=>{
    if(options.method==="POST"){review=JSON.parse(options.body);return jsonResponse(job);}
    if(url==="/api/quality/evaluations/job1")return jsonResponse(job);
    return jsonResponse({items:url==="/api/quality/evaluations"?[job]:[]});
  };
  await run("verificationWorkspace.refresh()");
  await $("verification-jobs").children[0].children.find(node=>node.textContent==="查看结果与复核").click();
  const card=$("verification-detail").querySelector(".feedback-card");
  assert.match(card.children[0].textContent,/<script>/);
  const form=card.querySelector(".quality-form");form.children[0].children[1].value="passed";form.children[1].children[1].value="核对条件与引用";
  await form.listeners.submit({preventDefault(){}});
  assert.equal(review.response_binding,"f".repeat(64));assert.equal(review.verdict,"passed");
});

test("improvement creation, verification and closure carry current revisions", async () => {
  const {$,run,sandbox}=setup();admin(run);const actions=[];
  let row={id:"improvement1",case_id:"case1",revision:1,status:"open",description:"修复遗漏",change_ref:"commit:abc",policy:{max_latency_ratio:1.5,max_token_ratio:1.3}};
  sandbox.fetch=async(url,options={})=>{
    if(options.method==="POST"){
      const body=JSON.parse(options.body);actions.push({url,body});
      if(url.endsWith("/verify"))row={...row,revision:2,status:"verified",verification:{baseline_job_id:"baseline",candidate_job_id:"candidate"}};
      if(url.endsWith("/close"))row={...row,revision:3,status:"closed"};
      return jsonResponse(row);
    }
    return jsonResponse({items:[{id:"baseline",status:"succeeded",current:true,created_at:"2026-10-28T00:00:00Z"},{id:"candidate",status:"succeeded",current:true,created_at:"2026-10-28T01:00:00Z"}]});
  };
  await run('verificationWorkspace.createForCase("case1")');
  let form=$("verification-detail").querySelector(".quality-form");
  form.children[0].children[1].value="修复遗漏";form.children[1].children[1].value="commit:abc";
  await form.listeners.submit({preventDefault(){}});
  let controls=$("verification-detail").children.find(node=>node.className==="quality-form"&&node.tag==="section");
  controls.children[0].children[1].value="baseline";controls.children[1].children[1].value="candidate";controls.children[2].children[1].value="已核对两组结果";
  await controls.children.find(node=>node.textContent==="执行发布检查并标记已验证").click();
  controls=$("verification-detail").children.find(node=>node.className==="quality-form"&&node.tag==="section");
  controls.children[2].children[1].value="依据仍有效，关闭问题";
  await controls.children.find(node=>node.textContent==="重新检查依据并关闭问题").click();
  assert.equal(actions[0].body.case_id,"case1");assert.equal(actions[1].body.expected_revision,1);assert.equal(actions[2].body.expected_revision,2);
  assert.match($("verification-detail").children[0].textContent,/已关闭/);
});

test("late improvement dialog response after logout cannot restore private content", async () => {
  const {$,run,sandbox}=setup();admin(run);let finish;
  sandbox.fetch=async()=>new Promise(resolve=>{finish=resolve;});
  const pending=run('verificationWorkspace.createForCase("case1")');await new Promise(resolve=>setImmediate(resolve));
  run("resetWorkspace()");finish(jsonResponse({items:[]}));await pending;
  assert.equal($("verification-dialog").open,false);assert.equal($("verification-detail").children.length,0);
});
