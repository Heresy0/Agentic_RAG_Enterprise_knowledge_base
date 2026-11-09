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

test("manufacturing review selects immutable business fields separately from document quotes", async () => {
  const { $, run, sandbox } = setup(); admin(run);
  const data = qualityData(); data.source.snapshot.context.items = [];
  const evidence = {citation_id:"业务2",field_name:"product_model",value:"<script>型号</script>",source_system_id:"mock-mes-hc",source_record_id:"source-1",source_revision:"r1",payload_sha256:"a".repeat(64),last_verified_at:"2026-11-01T00:00:00Z"};
  data.source.business_evidence_options = [evidence];
  let submitted;
  sandbox.fetch = async (url, options = {}) => {
    if(options.method === "PUT") { submitted=JSON.parse(options.body); return jsonResponse(data); }
    if(options.method === "POST") return jsonResponse(data);
    return jsonResponse({items:[],next_cursor:null});
  };
  await run('qualityWorkspace.open("run1")');
  const row=$("quality-detail").querySelector(".quality-evidence");
  assert.match(row.children[0].children[1].textContent, /<script>型号<\/script>/);
  assert.equal(row.children.filter(node=>node.tag==="textarea").length,0);
  row.children[0].children[0].checked=true;
  await $("quality-detail").querySelector(".quality-form").listeners.submit({preventDefault(){}});
  assert.deepEqual(submitted.evidence,[]);
  assert.deepEqual(submitted.business_evidence,[Object.fromEntries(["citation_id","field_name","value","source_system_id","source_record_id","source_revision","payload_sha256"].map(key=>[key,evidence[key]]))]);
});

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

test("manufacturing conditions are user input and changing them starts a new conversation", async () => {
  const { $, requests, run, ask } = setup();
  $("work-model").value = "机型A"; $("work-process").value = "OP20";
  await ask("首件如何确认？");
  assert.match(requests.filter(r => r.url === "/api/qa").at(-1).body.question, /产品／设备型号：机型A；工序：OP20/);
  $("work-model").value = "机型B"; $("work-model").listeners.change();
  assert.equal(run("state.conversationId"), null);
  assert.equal($("work-model").value, "机型B");
  await ask("检查哪些项目？");
  assert.equal(requests.filter(r => r.url === "/api/qa").at(-1).body.conversation_id, "c2");
  run("resetWorkspace()"); assert.equal($("work-model").value, "");
});

test("document review publishes the exact version and explicit OCR acknowledgement", async () => {
  const { $, run, sandbox } = setup(); const original = sandbox.fetch; let submitted;
  const doc = { id: "doc-review", file_name: "扫描.pdf", version: 2, status: "ready", mime_type: "application/pdf",
    publication: { state: "draft", revision: 4 }, quality: { review_required: true } };
  sandbox.fetch = async (url, options = {}) => {
    if (url.endsWith("/review")) return { ok: true, status: 200, headers: new Headers(), json: async () => ({ document: doc,
      items: [{ content: "<script>不可作为HTML执行</script>", metadata: { parser: "tesseract", page_start: 2, page_end: 2 } }], next_after: null }) };
    if (url.endsWith("/publication")) { submitted = JSON.parse(options.body); return { ok: true, status: 200, headers: new Headers(), json: async () => doc }; }
    return original(url, options);
  };
  run('state.kb.permission = "editor"'); await run('reviewDocument({ id: "doc-review", version: 2 })');
  const all = root => [root, ...root.children.flatMap(all)]; const nodes = all($("document-review-content"));
  assert(nodes.some(n => n.tag === "pre" && n.textContent.includes("<script>")));
  const publish = nodes.find(n => n.textContent === "确认发布");
  await publish.listeners.click(); assert.equal(submitted, undefined);
  nodes.find(n => n.tag === "textarea").value = "已核对原件，保留识别限制";
  nodes.find(n => n.tag === "input").checked = true; await publish.listeners.click();
  assert.deepEqual(submitted, { expected_version: 2, expected_revision: 4, state: "published", review_note: "已核对原件，保留识别限制", acknowledge_ocr: true });
});

test("late document preview after scope switch cannot restore private text", async () => {
  const { $, run, sandbox } = setup(); let finish, start;
  const started = new Promise(resolve => { start = resolve; });
  sandbox.fetch = async () => { start(); return new Promise(resolve => { finish = resolve; }); };
  const pending = run('reviewDocument({ id: "private", version: 1 })');
  await started;
  run('clearConversation(); state.revision += 1');
  finish({ ok: true, status: 200, headers: new Headers(), json: async () => ({ document: { file_name: "私密资料", publication: {}, quality: {} }, items: [] }) });
  await pending; assert.equal($("document-review-dialog").open, false);
  assert.equal($("document-review-content").children.length, 0);
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

function manufacturingSetup() {
  const h = setup(), original = h.sandbox.fetch, records = new Map(), saved = [];
  for (const id of ["WO-017", "WO-018"]) records.set(id, { work_order_id: id, product_model: "HC-A17", operation_code: "OP-20",
    actual_started_at: id === "WO-018" ? "2026-09-28" : "2026-10-26T08:10:00+08:00", actual_start_precision: id === "WO-018" ? "date" : "datetime",
    route_revision: null, source_revision: "r1", source_system_id: "mock-mes-hc", payload_sha256: "fixture-hash-"+id, last_verified_at: "2026-10-31T08:00:00+08:00" });
  const citation = id => ({ citation_id: "业务2", field_name: "product_model", value: "HC-A17", work_order_id: id,
    source_system_id: "mock-mes-hc", source_record_id: "record-"+id, source_revision: "r1", payload_sha256: records.get(id).payload_sha256, last_verified_at: records.get(id).last_verified_at });
  let sequence = 0;
  const response = body => ({ answer: "本轮工单来源事实 <script>只作文本</script>", answerable: true, citations: [],
    business_citations: [citation(body.work_order_id)], run_id: "mfg-run", work_order_context: records.get(body.work_order_id),
    applicability: { state: "applicable", business_revision: "1.0", document_code: "HC-WI-A17-020", documents: [], evidence: [] } });
  h.sandbox.fetch = async (url, options = {}) => {
    if (!url.startsWith("/api/manufacturing") && !url.startsWith("/api/qa/runs/mfg-run")) return original(url, options);
    const body = options.body ? JSON.parse(options.body) : null;
    h.requests.push({ url, method: options.method || "GET", body });
    if (url.startsWith("/api/manufacturing/work-orders/")) return jsonResponse(records.get(decodeURIComponent(url.split("/")[4])));
    if (url.startsWith("/api/manufacturing/work-orders")) return jsonResponse({ items: [...records.values()], next_cursor: null });
    if (url === "/api/manufacturing/qa") return jsonResponse(response(body));
    if (url === "/api/manufacturing/conversations" && options.method === "POST") {
      const item = { id: "mfg-"+(++sequence), title: body.work_order_id, binding: { work_order_id: body.work_order_id, source_revision: "r1" } };
      saved.push(item); return jsonResponse(item);
    }
    if (url.startsWith("/api/manufacturing/conversations/")) return jsonResponse({ id: "history", binding: { work_order_id: "WO-018", source_revision: "r1" },
      turns: [{ question: "之前的问题", result: response({ work_order_id: "WO-018" }), run_id: "mfg-run" }] });
    if (url.startsWith("/api/manufacturing/conversations")) return jsonResponse({ items: saved });
    return jsonResponse({ response: { business_citations: [citation("WO-018")] }, snapshot: {} });
  };
  h.run('state.mode = "manufacturing"; $("qa-mode").value = "manufacturing"; updateControls()');
  return { ...h, records, citation };
}
function treeText(node) { return [node.textContent, ...node.children.map(treeText)].join(" "); }

function executionSetup() {
  const h = manufacturingSetup(), original = h.sandbox.fetch;
  const operations = [{operation_id:"ROW-A",operation_code:"OP-20",status:"Completed"},{operation_id:"ROW-B",operation_code:"OP-30",status:"Pending"}];
  function context(orderId, operationId, cardId) {
    const base = {...h.records.get(orderId),source_revision:`r-${operationId||"wo"}-${cardId||"op"}`,payload_sha256:`hash-${operationId||"wo"}-${cardId||"op"}`};
    if (operationId) base.execution = {operation_id:operationId,operation_status:"Pending",operation_actual_started_at:null,operation_start_precision:null,
      job_card_id:cardId||null,job_card_actual_started_at:cardId==="CARD-A"?"2026-10-27T09:30:00+08:00":null,job_card_start_precision:cardId==="CARD-A"?"datetime":null,job_card_status:cardId?"Open":null};
    return base;
  }
  h.sandbox.fetch = async (url, options={}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    if (url === "/api/manufacturing/work-orders") { h.requests.push({url,method:"GET",body}); return jsonResponse({items:[...h.records.values()],next_cursor:null,selection_supported:true}); }
    if (url.endsWith("/operations")) { h.requests.push({url,method:"GET",body}); return jsonResponse({items:operations}); }
    if (url.includes("/job-cards")) { h.requests.push({url,method:"GET",body}); return jsonResponse({items:[{job_card_id:"CARD-A",status:"Open",actual_started_at:"2026-10-27T09:30:00+08:00"},{job_card_id:"CARD-A2",status:"Open",actual_started_at:null}],next_cursor:null}); }
    if (url.includes("/context")) { h.requests.push({url,method:"GET",body}); const query=new URLSearchParams(url.split("?")[1]||""); return jsonResponse(context(decodeURIComponent(url.split("/")[4]),query.get("operation_id"),query.get("job_card_id"))); }
    if (url === "/api/manufacturing/qa") { h.requests.push({url,method:"POST",body}); return jsonResponse({answer:"当前选择的来源事实",answerable:true,citations:[],business_citations:[],run_id:"execution-run",work_order_context:context(body.work_order_id,body.operation_id,body.job_card_id)}); }
    if (url === "/api/manufacturing/conversations" && options.method === "POST") { const response=await original(url,options); const data=await response.json(); return jsonResponse({...data,binding:{...data.binding,operation_id:body.operation_id,job_card_id:body.job_card_id}}); }
    if (url.startsWith("/api/manufacturing/conversations/selected-history")) { h.requests.push({url,method:"GET",body}); return jsonResponse({id:"selected-history",binding:{work_order_id:"WO-018",source_revision:"r-ROW-A-CARD-A",operation_id:"ROW-A",job_card_id:"CARD-A"},turns:[{question:"之前的问题",result:{answer:"历史答案",answerable:true,citations:[],work_order_context:context("WO-018","ROW-A","CARD-A")}}]}); }
    return original(url,options);
  };
  return {...h,context};
}

test("ERP selection: explicit operation/card flow preserves all three start scopes and sends only selectors", async () => {
  const {$,run,ask,requests}=executionSetup(); await run("refreshManufacturingOrders()"); await run('selectManufacturingOrder("WO-018")');
  await run('selectManufacturingOperation("ROW-A")'); await run('selectManufacturingJobCard("CARD-A")');
  const text=treeText($("manufacturing-context"));
  assert.match(text,/工单实际开工（选版依据）/); assert.match(text,/2026-09-28/); assert.match(text,/工序实际开工：未知/); assert.match(text,/2026-10-27/);
  await ask("所选作业卡实际开工时间是什么？");
  const body=requests.find(row=>row.url==="/api/manufacturing/qa").body;
  assert.equal(body.operation_id,"ROW-A"); assert.equal(body.job_card_id,"CARD-A"); assert(!("actual_started_at" in body));
  assert.equal(requests.find(row=>row.method==="POST"&&row.url==="/api/manufacturing/conversations").body.job_card_id,"CARD-A");
});

test("ERP selection: card or operation changes clear conversation binding and child selection", async () => {
  const {run,ask,requests}=executionSetup(); await run("refreshManufacturingOrders()"); await run('selectManufacturingOrder("WO-018")');
  await run('selectManufacturingOperation("ROW-A")'); await run('selectManufacturingJobCard("CARD-A")'); await ask("型号？");
  await run('selectManufacturingJobCard("CARD-A2")'); assert.equal(run("state.conversationId"),null); await ask("型号？");
  assert.equal(requests.filter(row=>row.method==="POST"&&row.url==="/api/manufacturing/conversations").length,2);
  await run('selectManufacturingOperation("ROW-B")'); assert.equal(run("state.manufacturing.jobCardId"),null); assert.equal(run("state.conversationId"),null);
  assert.equal(run("state.manufacturing.context.execution.operation_id"),"ROW-B");
});

test("ERP selection: late card context cannot replace a newer card selection", async () => {
  const {run,sandbox}=executionSetup(); await run("refreshManufacturingOrders()"); await run('selectManufacturingOrder("WO-018")'); await run('selectManufacturingOperation("ROW-A")');
  const original=sandbox.fetch; let finish,begin; const started=new Promise(resolve=>{begin=resolve;});
  sandbox.fetch=async(url,options)=>{if(url.includes("job_card_id=CARD-A")&&!url.includes("CARD-A2")){begin();return new Promise(resolve=>{finish=()=>original(url,options).then(resolve);});}return original(url,options);};
  const pending=run('selectManufacturingJobCard("CARD-A")'); await started; await run('selectManufacturingJobCard("CARD-A2")'); finish(); await pending;
  assert.equal(run("state.manufacturing.context.execution.job_card_id"),"CARD-A2");
});

test("ERP selection: history and new-conversation refresh retain the explicit selection", async () => {
  const {run,ask,requests}=executionSetup(); await run("refreshManufacturingOrders()"); await run('restoreConversation("selected-history")');
  assert.equal(run("state.manufacturing.operationId"),"ROW-A"); assert.equal(run("state.manufacturing.jobCardId"),"CARD-A");
  await ask("型号？"); assert.equal(requests.find(row=>row.url==="/api/manufacturing/qa").body.job_card_id,"CARD-A");
  await run("loadManufacturingSelection(true,true)"); assert.equal(run("state.manufacturing.context.execution.job_card_id"),"CARD-A"); assert.equal(run("state.conversationId"),null);
});

test("ERP selection: current business citation rechecks the same card and nested source field", async () => {
  const {$,run,context}=executionSetup(); await run("refreshManufacturingOrders()"); await run('selectManufacturingOrder("WO-018")');
  const source=context("WO-018","ROW-A","CARD-A");
  const citation={citation_id:"业务22",field_name:"job_card_actual_started_at",work_order_id:"WO-018",operation_id:"ROW-A",job_card_id:"CARD-A",source_revision:source.source_revision,payload_sha256:source.payload_sha256,source_system_id:"erpnext-hc",source_record_id:"CARD-A"};
  await run(`previewBusinessCitation(${JSON.stringify(citation)})`);
  assert.match($("citation-content").textContent,/2026-10-27/);
});

test("M3 U01: work-order list and read-only source card preserve precision, revision and unknown route", async () => {
  const { $, run } = manufacturingSetup();
  await run("refreshManufacturingOrders()");
  assert.equal($("manufacturing-order").children.length, 3);
  await run('selectManufacturingOrder("WO-018")');
  const text = treeText($("manufacturing-context"));
  assert.match(text, /2026-09-28/); assert.match(text, /date/); assert.match(text, /路线修订：未知/); assert.match(text, /r1/);
  assert.equal($("user-work-conditions").hidden, true); assert.equal($("manufacturing-context").children.some(n => n.tag === "input"), false);
});

test("M3 U02/U03: same order reuses binding, changed order creates a new conversation without client facts", async () => {
  const { $, run, ask, requests } = manufacturingSetup();
  await run('selectManufacturingOrder("WO-018")'); $("work-model").value = "UNTRUSTED-OVERRIDE";
  await ask("型号是什么？"); await ask("那工序呢？");
  let calls = requests.filter(row => row.url === "/api/manufacturing/qa");
  assert.equal(calls[0].body.conversation_id, calls[1].body.conversation_id);
  assert.equal(calls[0].body.work_order_id, "WO-018"); assert.equal(calls[0].body.expected_source_revision, "r1");
  assert(!JSON.stringify(calls[0].body).includes("UNTRUSTED"));
  await run('selectManufacturingOrder("WO-017")'); await ask("首件要求？");
  calls = requests.filter(row => row.url === "/api/manufacturing/qa");
  assert.notEqual(calls[0].body.conversation_id, calls[2].body.conversation_id); assert.equal(calls[2].body.work_order_id, "WO-017");
});

test("M3 U04: source change clears pending answer, source card and conversation binding", async () => {
  const { $, run, ask, sandbox } = manufacturingSetup(); await run('selectManufacturingOrder("WO-018")');
  const original = sandbox.fetch;
  sandbox.fetch = async (url, options) => url === "/api/manufacturing/qa" ? jsonResponse({ detail: { error_code: "mes_context_changed", message: "工单已变化，请重新查询。" } }, 409) : original(url, options);
  await ask("型号是什么？");
  assert.equal(run("state.conversationId"), null); assert.equal(run("state.manufacturing.context"), null);
  assert(!treeText($("manufacturing-context")).includes("2026-09-28")); assert.match(treeText($("answer-area")), /重新查询/);
});

test("M3 U05: changing to ordinary mode clears the manufacturing binding and never calls MES for ordinary QA", async () => {
  const { $, run, ask, requests } = manufacturingSetup(); await run('selectManufacturingOrder("WO-018")'); await ask("型号？");
  const before = requests.filter(row => row.url.startsWith("/api/manufacturing/work-orders")).length;
  await $("qa-mode").listeners.change({ target: { value: "knowledge" } });
  assert.equal(run("state.conversationId"), null); assert.equal(run("state.manufacturing.context"), null);
  await ask("普通制度问题");
  assert.equal(requests.filter(row => row.url.startsWith("/api/manufacturing/work-orders")).length, before);
  assert.equal(requests.at(-1).url.includes("/api/manufacturing"), false);
});

test("M3 U06: late manufacturing answer after order switch cannot refill old facts or unlock a newer request", async () => {
  const { $, run, ask, sandbox } = manufacturingSetup(); await run('selectManufacturingOrder("WO-018")');
  const original = sandbox.fetch; let finish, begin;
  const started = new Promise(resolve => { begin = resolve; });
  sandbox.fetch = async (url, options) => { if (url === "/api/manufacturing/qa") { begin(); return new Promise(resolve => { finish = resolve; }); } return original(url, options); };
  const pending = ask("旧工单问题"); await started; await run('selectManufacturingOrder("WO-017")');
  finish(jsonResponse({ answer: "OLD-PRIVATE-ANSWER", work_order_context: { work_order_id: "WO-018" } })); await pending;
  assert.equal(run("state.manufacturing.context.work_order_id"), "WO-017"); assert(!treeText($("answer-area")).includes("OLD-PRIVATE"));
});

test("M3 U06: late source context after logout cannot restore the old card", async () => {
  const { $, run, sandbox } = manufacturingSetup(); let finish, begin;
  const started = new Promise(resolve => { begin = resolve; });
  sandbox.fetch = async () => { begin(); return new Promise(resolve => { finish = resolve; }); };
  const pending = run('selectManufacturingOrder("WO-018")'); await started;
  run('state.auth.authenticated = false; resetWorkspace()'); finish(jsonResponse({ work_order_id: "PRIVATE-OLD-ORDER" })); await pending;
  assert.equal(run("state.manufacturing.context"), null); assert(!treeText($("manufacturing-context")).includes("PRIVATE-OLD"));
});

test("M3 U07: business reference is reauthorized and shows field, source revision and time without a PDF page", async () => {
  const { $, run, citation } = manufacturingSetup();
  run('globalThis.testCitation = '+JSON.stringify(citation("WO-018")));
  await run('previewBusinessCitation(testCitation, "mfg-run")');
  assert.match($("citation-title").textContent, /product_model/); assert.match($("citation-meta").textContent, /r1/);
  assert.match($("citation-meta").textContent, /2026-10-31/); assert.equal($("citation-content").textContent, '"HC-A17"');
  assert(!$("citation-meta").textContent.includes("物理页"));
});

test("M3 U08: denied history clears previous order and late business preview after scope change stays closed", async () => {
  const { $, run, sandbox, citation } = manufacturingSetup(); await run('selectManufacturingOrder("WO-018")');
  sandbox.fetch = async () => jsonResponse({ detail: { message: "当前不可访问" } }, 404);
  await run('restoreConversation("denied")'); assert.equal(run("state.manufacturing.context"), null);
  assert(!treeText($("manufacturing-context")).includes("2026-09-28"));
  run('globalThis.testCitation = '+JSON.stringify(citation("WO-018"))); let finish, begin;
  const started = new Promise(resolve => { begin = resolve; });
  sandbox.fetch = async () => { begin(); return new Promise(resolve => { finish = resolve; }); };
  const pending = run('previewBusinessCitation(testCitation, "mfg-run")'); await started;
  run('state.revision += 1; clearConversation()'); finish(jsonResponse({ response: { business_citations: [{ ...citation("WO-018"), value: "LATE-PRIVATE-VALUE" }] } })); await pending;
  assert.equal($("citation-dialog").open, false); assert.equal($("citation-content").textContent, "");
});
