"""Loopback-only frontend preview using disposable, in-memory demonstration data.

Run: .venv/Scripts/python.exe scripts/preview_frontend.py --port 8788
This script does not import the business application, use its configuration,
connect to databases, call models, or start workers. Restart to reset demo data.
The OIDC-shaped fixture is only for exercising the unchanged browser login flow.
It is not an authentication service and is never mounted by the application.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

STATIC = Path(__file__).resolve().parents[1] / "backend/app/static"
ASSETS = {"app.js", "auth.js", "quality.js", "verification.js", "openmes.js", "styles.css", "noto-sans-sc-variable.woff2", "FONT-OFL.txt"}
STAMP = "2026-11-16T09:42:16+08:00"
KB = "demo-process"
BASES = [dict(id=KB, name="工艺与作业指导", permission="admin"), dict(id="demo-quality", name="质量与检验规范", permission="viewer")]
ORDERS = [dict(work_order_id="WO-20261116-028", product_model="HC-A17", operation_code="OP-20"), dict(work_order_id="WO-20261116-031", product_model="HC-P01", operation_code="OP-30"), dict(work_order_id="WO-20261115-027", product_model="HC-A17", operation_code="OP-20")]
GUIDE = "1. 完成首件连续检验\n对首件连续检验5件，核对尺寸、外观与装配一致性，并完整记录检验结果。\n\n2. 取得质量复核确认\n首件检验结果须经质量人员复核确认。检验完成不能代替质量确认记录。\n\n3. 确认后再恢复后续作业\n首件检验合格、质量复核确认完成后，方可恢复后续作业。存在异常时，按指导书要求处理。"
DOC_NAMES = ["HC-A17_首件检查作业指导书_Rev.B.pdf", "HC-A17_来料与装配检验规范.pdf", "量具借用与校准管理.docx", "首件检验记录_扫描件.pdf", "HC-A17_首件检查作业指导书_Rev.A.pdf"]
DOCUMENTS = [dict(id=f"demo-doc-{i+1}", file_name=name, status="ready", version=2 if i == 0 else 1, chunk_count=[18, 26, 12, 8, 16][i], updated_at=STAMP, mime_type="application/pdf" if name.endswith("pdf") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document", publication=dict(state="draft" if i == 3 else "withdrawn" if i == 4 else "published", revision=1), quality=dict(review_required=i == 3)) for i, name in enumerate(DOC_NAMES)]
CITATION = dict(citation_id=1, knowledge_base_id=KB, knowledge_base_name="工艺与作业指导", document_id="demo-doc-1", document_name=DOC_NAMES[0], document_version=2, chunk_id="demo-chunk-1", section_path="4.2 首件检验", mime_type="application/pdf", page_start=3, page_end=3)
FEEDBACK = [dict(id="demo-feedback-1", run_id="demo-run-1", question="检验完成后可以恢复后续作业吗？", answer_preview="应在首件检验合格且质量复核确认完成后恢复后续作业。", rating="down", reason="incomplete", comment="原回答没有说明质量确认记录与检验结果的区别，请补充依据。", share_with_admins=True, allow_dataset_export=True, created_at=STAMP), dict(id="demo-feedback-2", run_id="demo-run-2", question="首件应连续检验几件？", answer_preview="Rev.B要求首件连续检验5件。", rating="up", reason=None, comment="找到了对应版本的数量和原文。", share_with_admins=False, allow_dataset_export=False, created_at=STAMP)]
DATASETS = [dict(id="demo-fixed-v1", name="固定基线样本 · 演示", current=True, manifest=dict(sample_count=12), created_at=STAMP), dict(id="demo-feedback-v1", name="反馈回归样本 · 演示", current=True, manifest=dict(sample_count=4), created_at=STAMP)]
CONVERSATIONS: dict[str, dict] = {}
RUNS: dict[str, dict] = {}
REVIEWS: dict[str, dict] = {}


def context(order: str, query: dict, mes: bool = False) -> dict:
    row = next((x for x in ORDERS if x["work_order_id"] == order), ORDERS[0])
    operation = query.get("operation_id", [None])[0]
    card = query.get("job_card_id", [None])[0]
    result = dict(**row, actual_started_at=None if mes else "2026-11-16T08:30:00+08:00", actual_start_precision="unknown" if mes else "datetime", route_revision="R2", source_system_id="OpenMES · 演示" if mes else "ERPNext · 演示", source_revision="demo-revision-1", last_verified_at=STAMP)
    if operation:
        result["operation_code"] = operation.upper()
        result["execution"] = dict(operation_id=operation, operation_actual_started_at=None, operation_start_precision="unknown", operation_status="未登记", job_card_id=card, job_card_actual_started_at="2026-11-16T09:10:00+08:00" if card else None, job_card_start_precision="datetime" if card else "unknown", job_card_status="In Process" if card else None)
    if mes:
        result.update(work_order_number=order, source_record_id="demo-mes-order", status="In Progress")
        if query.get("batch_id", [None])[0]: result["batch"] = dict(batch_id="demo-batch-1", source_record_id="demo-batch-1", lot_number="LOT-1116-A", status="In Progress", actual_started_at="2026-11-16T08:45:00+08:00")
        if query.get("step_id", [None])[0]: result["step"] = dict(step_id="demo-step-1", name="首件检查", status="Pending", actual_started_at=None)
    return result


def answer(question: str, manufacturing: bool, body: dict, mes: bool) -> dict:
    data = dict(answer=GUIDE if manufacturing else "根据《HC-A17 首件检查作业指导书》Rev.B，首件应连续检验5件。检验结果需要完整记录，并经质量人员复核确认后，才能恢复后续作业。[1]\n\n这是前端预览的固定演示回答，没有调用真实模型。", answerable=True, response_type="answer", citations=[CITATION], business_citations=[], timings=dict(total_ms=0), snapshot_status="captured", run_id="demo-run-" + uuid4().hex[:8])
    if manufacturing:
        selection = {k: [v] for k, v in body.items() if k.endswith("_id") and v}
        data.update(work_order_context=context(body.get("work_order_id", ORDERS[0]["work_order_id"]), selection, mes), applicability=dict(state="applicable", document_code="WI-HC-A17-OP-20", business_revision="Rev.B", documents=[], rule_ids=[], evidence=[CITATION]))
    RUNS[data["run_id"]] = dict(question=question, response=data, request_id="preview-only", created_at=STAMP, snapshot_status="captured", retrieval_question=question, snapshot=dict(history=[], context=dict(items=[dict(**CITATION, content=GUIDE)])))
    return data


def review_data(run_id: str) -> dict:
    if run_id not in REVIEWS:
        REVIEWS[run_id] = dict(case=dict(id="demo-case-1", run_id=run_id, revision=1, review_status="pending", sample_status="draft", export_blocked_reason="not_approved"), review=dict(review_status="pending", sample_status="draft", review_reason="演示待审核记录", required_facts=[], forbidden_claims=[], evidence=[]), source=dict(question=FEEDBACK[0]["question"], retrieval_question=FEEDBACK[0]["question"], response=dict(answer=FEEDBACK[0]["answer_preview"]), feedback=FEEDBACK[0], snapshot=dict(history=[], context=dict(items=[dict(**CITATION, content=GUIDE)]))), history=[], next_history_before=None)
    return deepcopy(REVIEWS[run_id])


class PreviewHandler(BaseHTTPRequestHandler):
    def respond(self, value, status=200, mime="application/json; charset=utf-8", headers=None):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for key, val in {"Content-Type": mime, "Content-Length": str(len(raw)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Request-ID": "frontend-preview", **(headers or {})}.items(): self.send_header(key, val)
        self.end_headers()
        self.wfile.write(raw)

    def route(self, method):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        prefix = "/mes" if parsed.path.startswith("/mes/") else "/erp" if parsed.path.startswith("/erp/") else ""
        path = parsed.path[len(prefix):]
        length = int(self.headers.get("Content-Length", "0"))
        if length > 21 * 1024 * 1024: return self.respond(dict(detail="文件超过预览限制。"), 413)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw and "application/json" in self.headers.get("Content-Type", "") else {}
        if path in {"/", "/index.html"} and method == "GET":
            html = (STATIC / "index.html").read_text("utf-8").replace('<body>', '<body><div class="preview-banner">前端预览 · 使用演示数据 · 登录为本地模拟流程 · 不连接ERP/MES，不调用模型</div>')
            for asset in ("app.js", "styles.css"):
                html = html.replace(f'/ui/assets/{asset}"', f'/ui/assets/{asset}?preview={int((STATIC / asset).stat().st_mtime_ns)}"')
            return self.respond(html.encode(), mime="text/html; charset=utf-8")
        if path == "/health/ready": return self.respond(dict(status="preview"))
        if path.startswith("/ui/assets/") and method == "GET":
            name = path.removeprefix("/ui/assets/")
            if name not in ASSETS: return self.respond(dict(detail="资源不存在"), 404)
            asset = (STATIC / name).read_bytes()
            if name == "app.js":
                source = asset.decode("utf-8")
                for module in ("auth.js", "quality.js", "verification.js", "openmes.js"):
                    source = source.replace(f'"./{module}"', f'"./{module}?preview={int((STATIC / module).stat().st_mtime_ns)}"')
                asset = source.encode("utf-8")
            return self.respond(asset, mime=mimetypes.guess_type(name)[0] or "application/octet-stream", headers={"Cache-Control": "public, max-age=3600" if name.endswith(".woff2") else "no-store"})
        if path == "/ui/config": return self.respond(dict(issuer=f"http://127.0.0.1:{self.server.server_port}/preview-oidc", client_id="local-preview-only", max_upload_bytes=20971520, base_path=prefix, workspaces=[dict(label="通用知识", path="/"), dict(label="ERP工作区", path="/erp/"), dict(label="MES工作区", path="/mes/")]))
        if parsed.path == "/preview-oidc/protocol/openid-connect/auth":
            redirect = query.get("redirect_uri", [""])[0]
            allowed = {f"http://127.0.0.1:{self.server.server_port}{p}/" for p in ["", "/erp", "/mes"]}
            if redirect not in allowed: return self.respond(dict(detail="仅限本地预览回调"), 400)
            return self.respond(b"", 302, headers={"Location": redirect + "?" + urlencode(dict(code="local-preview", state=query.get("state", [""])[0]))})
        if parsed.path == "/preview-oidc/protocol/openid-connect/token" and method == "POST":
            enc = lambda data: base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
            token = enc(dict(alg="none", typ="JWT")) + "." + enc(dict(exp=int(time.time()) + 3600, sub="demo-user", name="林工（演示）")) + ".preview"
            return self.respond(dict(access_token=token, token_type="Bearer"))
        if not path.startswith("/api/"): return self.respond(dict(detail="预览页面不存在"), 404)
        if not self.headers.get("Authorization", "").startswith("Bearer "): return self.respond(dict(detail="请先进入演示工作空间"), 401)
        if path == "/api/knowledge-bases": return self.respond(dict(items=BASES))
        if path.endswith("/documents") and method == "GET": return self.respond(dict(items=DOCUMENTS))
        if path.endswith("/indexing-jobs"): return self.respond(dict(items=[]))
        if "/documents/" in path and path.endswith("/review"):
            doc = next((d for d in DOCUMENTS if f"/{d['id']}/" in path), None)
            if not doc: return self.respond(dict(detail="演示文档不存在"), 404)
            return self.respond(dict(document=doc, items=[dict(id="demo-chunk-1", index=0, content=GUIDE, metadata=dict(section_path="4.2 首件检验", parser="pdf_native", page_start=3, page_end=3, quality={}, cleaning={}))], next_after=None))
        if "/documents/" in path and path.endswith("/publication") and method == "PUT":
            doc = next((d for d in DOCUMENTS if f"/{d['id']}/" in path), None)
            if not doc: return self.respond(dict(detail="演示文档不存在"), 404)
            doc["publication"] = dict(state=body["state"], revision=doc["publication"]["revision"] + 1)
            return self.respond(doc)
        if "/chunks/" in path: return self.respond(dict(**CITATION, content=GUIDE, quality=dict(review_required=False)))
        if path.endswith("/source"): return self.respond(("演示原文\n\n" + GUIDE).encode(), mime="text/plain; charset=utf-8")
        if path.endswith("/work-orders"): return self.respond(dict(items=ORDERS, next_cursor=None, **(dict(execution_kind="batch-step") if prefix == "/mes" else dict(selection_supported=True))))
        if path.endswith("/operations"): return self.respond(dict(items=[dict(operation_id=code.lower(), operation_code=code, status="未登记") for code in ["OP-10", "OP-20", "OP-30"]]))
        if path.endswith("/job-cards"): return self.respond(dict(items=[dict(job_card_id="JC-20261116-028-01", status="In Process", actual_started_at="2026-11-16T09:10:00+08:00")], next_cursor=None))
        if path.endswith("/batches"): return self.respond(dict(items=[dict(batch_id="demo-batch-1", batch_number="B-1116-01", lot_number="LOT-1116-A", status="In Progress")]))
        if path.endswith("/steps"): return self.respond(dict(items=[dict(step_id="demo-step-1", step_number=2, name="首件检查", status="Pending")]))
        if path.endswith(("/context", "/execution-context")) and "/work-orders/" in path:
            return self.respond(context(path.split("/work-orders/")[1].split("/")[0], query, prefix == "/mes"))
        if "/guidance-mapping" in path: return self.respond(dict(detail="演示未配置步骤指导书映射，请回到工单级体验查询。"), 404)
        if path.endswith("/conversations"):
            if method == "POST":
                item = dict(id="demo-conversation-" + uuid4().hex[:8], title="新对话", turn_count=0, turns=[], prefix=prefix, mode="manufacturing" if "/manufacturing/" in path else "knowledge", work_order_id=body.get("work_order_id"), selection=body)
                CONVERSATIONS[item["id"]] = item
                return self.respond(item, 201)
            return self.respond(dict(items=[c for c in CONVERSATIONS.values() if c["prefix"] == prefix and c["mode"] == ("manufacturing" if "/manufacturing/" in path else "knowledge")]))
        if "/conversations/" in path:
            identifier = path.rsplit("/", 1)[-1]
            if method == "DELETE": CONVERSATIONS.pop(identifier, None); return self.respond(b"", 204)
            item = deepcopy(CONVERSATIONS.get(identifier))
            if item:
                if item["work_order_id"]: item["binding"] = dict(work_order_id=item["work_order_id"], **{k:v for k,v in item["selection"].items() if k in {"operation_id", "job_card_id", "batch_id", "step_id"}})
                return self.respond(item)
            return self.respond(dict(detail="演示会话不存在"), 404)
        if path.endswith("/qa") and method == "POST":
            result = answer(body["question"], "/manufacturing/" in path, body, prefix == "/mes")
            conversation = CONVERSATIONS.get(body.get("conversation_id"))
            if conversation:
                conversation["turns"].append(dict(question=body["question"], result=result, run_id=result["run_id"])); conversation.update(title=body["question"][:36], turn_count=len(conversation["turns"]))
            return self.respond(result)
        if "/api/qa/runs/" in path:
            identifier = path.split("/runs/")[1].split("/")[0]
            if path.endswith("/feedback"):
                if method == "GET": return self.respond(dict(feedback=next((x for x in FEEDBACK if x["run_id"] == identifier), None)))
                if method == "PUT":
                    record = dict(id="demo-feedback-"+uuid4().hex[:6], run_id=identifier, question=RUNS.get(identifier, {}).get("question", "演示问题"), answer_preview=RUNS.get(identifier, {}).get("response", {}).get("answer", "演示回答"), **body)
                    FEEDBACK[:] = [f for f in FEEDBACK if f["run_id"] != identifier]; FEEDBACK.append(record)
                    return self.respond(dict(feedback=record))
                if method == "DELETE": FEEDBACK[:] = [f for f in FEEDBACK if f["run_id"] != identifier]; return self.respond(b"", 204)
            return self.respond(RUNS.get(identifier) or dict(question=FEEDBACK[0]["question"], retrieval_question=FEEDBACK[0]["question"], response=dict(answer=FEEDBACK[0]["answer_preview"]), request_id="demo-preview", created_at=STAMP, snapshot_status="captured", snapshot=dict(history=[], context=dict(items=[dict(**CITATION, content=GUIDE)]))))
        if path == "/api/qa/feedback": return self.respond(dict(items=[f for f in FEEDBACK if (not query.get("rating") or f["rating"] == query["rating"][0]) and (query.get("view", ["mine"])[0] != "shared" or f["share_with_admins"])], next_cursor=None))
        if path == "/api/quality/cases":
            if method == "POST": return self.respond(review_data(body["run_id"]))
            items = [dict(id="demo-case-1", run_id=FEEDBACK[0]["run_id"], question=FEEDBACK[0]["question"], review_status="pending", sample_status="draft", export_eligible=False, export_blocked_reason="not_approved", feedback=FEEDBACK[0])]
            return self.respond(dict(items=[x for x in items if not query.get("review_status") or x["review_status"] == query["review_status"][0]], next_cursor=None))
        if path == "/api/quality/datasets": return self.respond(dict(items=DATASETS))
        if path.endswith("/manifest"): return self.respond(dict(name="前端演示样本", sample_count=4))
        if path.endswith("/samples"): return self.respond((json.dumps(dict(question=FEEDBACK[0]["question"], answer=GUIDE), ensure_ascii=False)+"\n").encode(), mime="application/x-ndjson")
        if "/api/quality/runs/" in path: return self.respond(review_data(path.split("/runs/")[1].split("/")[0]))
        if "/api/quality/cases/" in path and method == "PUT":
            data = review_data("demo-run-1"); data["review"] = body; data["case"].update(revision=data["case"]["revision"]+1, review_status=body["review_status"], sample_status=body["sample_status"]); REVIEWS["demo-run-1"] = data
            return self.respond(data)
        if path == "/api/quality/evaluations" and method == "GET": return self.respond(dict(items=[dict(id="demo-evaluation-1", status="succeeded", completed_cases=16, expected_cases=16, current=True)]))
        if path == "/api/quality/evaluations/demo-evaluation-1": return self.respond(dict(id="demo-evaluation-1", status="succeeded", completed_cases=16, expected_cases=16, current=True, summary=dict(note="界面演示数据，不是本项目评测成绩", passed=14, total=16), records=[]))
        if path == "/api/quality/improvements": return self.respond(dict(items=[]))
        return self.respond(dict(detail="此操作未配置演示数据。预览不会启动模型、索引或评测任务。"), 409)

    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_PUT(self): self.route("PUT")
    def do_DELETE(self): self.route("DELETE")
    def log_message(self, *args): pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print(f"Local: http://127.0.0.1:{args.port}/erp/ — disposable demo data only", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
