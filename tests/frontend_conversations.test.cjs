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
    document: { getElementById: $, createElement: tag => new Node(tag), querySelectorAll: () => [] },
    window: { confirm: () => true }, Headers, FormData, AbortSignal, Intl, URLSearchParams,
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
  const source = fs.readFileSync(path.join(__dirname, "../backend/app/static/app.js"), "utf8")
    .replace(/^import .*;\r?\n/, "").replace(/init\(\);\s*$/, "");
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
  assert.deepEqual(submitted, { rating: "down", reason: "incomplete", comment: "遗漏了例外", share_with_admins: true });
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
  assert.equal(form.children[4].disabled, false);
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
