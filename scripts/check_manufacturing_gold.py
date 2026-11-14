"""Validate the offline gold candidate; never execute QA, HTTP or model calls."""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals/manufacturing_gold_v1"
COUNTS = {"guidance": 12, "erpnext": 8, "openmes": 8, "safety": 8, "conversation": 4}


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def render_review(cases, sources):
    lines = ["# 40题黄金候选人工审核", "", "40题均待人工审核，已批准0题。本文件为cases.jsonl的可读视图；没有新问答结果或模型评分。", "",
             "先核对原文/源实体，再核对标准答案、条件例外、禁止结论和HTTP行为。归档快照不能作为当前线上授权；含假设的多步场景须在后续隔离环境复核。人工结论、审核者和时间应另存审核记录或新版本，保留本候选原件。", "",
             "| ID | 分类 | 问题 | 状态 |", "| --- | --- | --- | --- |"]
    lines.extend(f"| [{c['id']}](#{c['id'].lower()}) | {c['category']} | {c['question']} | 待人工审核 |" for c in cases)
    for c in cases:
        x = c["expected"]
        lines += ["", "## " + c["id"], "", "**问题：** " + c["question"], "", "**选择：** `" + json.dumps(c["input"]["selection"], ensure_ascii=False) + "`", "",
                  "**前置条件：** " + "；".join(c["input"]["preconditions"]), "", "**标准答案草案：** " + x["reference_answer"], "",
                  f"**预期行为：** {x['behavior']} / HTTP {x['http_status']}；待人工确认。", "", "**必答点：**", ""]
        lines += ["- " + f["text"] for f in x["required_facts"]]
        lines += ["", "**禁止结论：** " + "；".join(x["forbidden_claims"]), "", "**来源证据：**", ""]
        for e in c["evidence"]:
            s = sources[e["source_id"]]
            if e["kind"] == "text":
                lines += [f"- {e['id']} / {e['source_id']} / {e['unit_id']}：", "", "```text", e["quote"], "```", ""]
            elif e["kind"] == "full_text":
                lines += [f"- {e['id']} / {e['source_id']}：对照完整文件全部{len(e['unit_ids'])}个条目；{e['reason']}"]
            else:
                lines += [f"- {e['id']} / {e['source_id']} / `{e['field_path']}` = `{json.dumps(e['value'], ensure_ascii=False)}`；归档时间 {s['captured_at']}，本轮未实时复读。"]
        if c["input"]["conversation_steps"]:
            lines += ["", "**多步协议：**", "", "```json", json.dumps(c["input"]["conversation_steps"], ensure_ascii=False, indent=2), "```"]
        lines += ["", "**待审核原因：** " + "；".join(c["review"]["reasons"]), ""]
    lines += ["## 来源索引", "", "业务证据是白名单归档投影；原始私有产物只保留哈希和定位，不携带模型答案、凭据、租户或用户UUID。MES执行源的work_order层展开为投影根，batch/step仍分别保留；映射属于平台审核元数据。", ""]
    for sid, s in sources.items():
        if "repository_path" in s:
            lines.append(f"- {sid}：[原文]({Path('../../'+s['repository_path']).as_posix()})；SHA-256 `{s['original_sha256']}`。")
        else:
            lines.append(f"- {sid}：归档投影SHA-256 `{s['projection_sha256']}`；来源产物SHA-256 `{s['capture_origin']['artifact_sha256']}`。")
    return "\n".join(lines) + "\n"


def check():
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files_sha256"].items():
        path = (ROOT / name).resolve()
        assert path.is_relative_to(ROOT) and path.is_file(), name
        assert digest(path.read_bytes()) == expected, f"frozen artifact changed: {name}"
    cases = [json.loads(line) for line in (DATA / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    source_bundle = json.loads((DATA / "sources.json").read_text(encoding="utf-8"))
    sources = source_bundle["sources"]
    assert len(cases) == 40 and Counter(c["category"] for c in cases) == COUNTS
    assert manifest["status"] == "candidate_pending_human_review" and manifest["approved_cases"] == 0
    assert not manifest["executable"] and not source_bundle["current_live_verification"]
    assert len({c["id"] for c in cases}) == len({c["question"] for c in cases}) == 40
    for sid, s in sources.items():
        if s["kind"] in {"document", "platform_specification"}:
            p = (ROOT / s["repository_path"]).resolve()
            assert p.is_relative_to(ROOT), sid
            blob = p.read_bytes()
            if s["checksum_mode"] == "lf_normalized_text":
                assert digest(blob.replace(b"\r\n", b"\n")) == s["content_sha256"], sid
            else:
                assert s["checksum_mode"] == "raw_bytes" and digest(blob) == s["original_sha256"], sid
            if p.suffix == ".docx":
                from docx import Document
                doc = Document(p)
                lines = [x.text for x in doc.paragraphs if x.text.strip()]
                lines += [" | ".join(c.text for c in row.cells) for t in doc.tables for row in t.rows]
            else:
                lines = [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
            assert lines == [u["text"] for u in s["units"]], sid
            assert len({u["id"] for u in s["units"]}) == len(lines)
        else:
            assert not s["live_verified_this_batch"] and s["captured_at"], sid
            assert digest(canonical(s["data"])) == s["projection_sha256"], sid
            assert s["capture_origin"]["artifact_sha256"] and s["capture_origin"]["locator"]
    for index, c in enumerate(cases, 1):
        assert c["id"] == f"MFG-GOLD-{index:03}"
        assert c["review"]["status"] == "pending_human_review" and c["review"]["human_reviewer"] is None
        assert c["review"]["reviewed_at"] is None and c["review"]["assistant_source_check"] == "non_independent"
        assert c["provenance"]["split"] == "regression_candidate" and not c["provenance"]["independent_holdout"]
        assert c["provenance"]["leakage_groups"] and c["input"]["preconditions"]
        x = c["expected"]
        assert x["reference_answer"] and x["required_facts"] and x["forbidden_claims"]
        assert x["behavior"] in {"answer", "clarify", "refuse", "error"}
        assert x["answerable"] == (x["behavior"] == "answer")
        assert (x["http_status"] >= 400) == (x["behavior"] == "error")
        ids = {e["id"] for e in c["evidence"]}
        assert len(ids) == len(c["evidence"]) and ids
        for f in x["required_facts"]:
            assert f["text"] and f["evidence_ids"] and set(f["evidence_ids"]) <= ids
        for e in c["evidence"]:
            s = sources[e["source_id"]]
            if e["kind"] == "text":
                assert next(u["text"] for u in s["units"] if u["id"] == e["unit_id"]) == e["quote"]
            elif e["kind"] == "full_text":
                assert e["unit_ids"] == [u["id"] for u in s["units"]]
            else:
                assert e["kind"] == "field"
                v = s["data"]
                for part in e["field_path"].split("."):
                    v = v[part]
                assert v == e["value"] and type(v) is type(e["value"])
        context = c["input"]["context_source_id"]
        if context:
            d = sources[context]["data"]; selection = c["input"]["selection"]
            assert selection["work_order_id"] == d["work_order_id"]
            for key in ["operation_id", "job_card_id"]:
                if key in selection:
                    assert selection[key] == d["execution"][key]
            for parent, child in [("batch", "batch_id"), ("step", "step_id")]:
                if child in selection:
                    assert selection[child] == d[parent][child] and d[parent]["work_order_id"] == d["work_order_id"]
        if c["category"] == "conversation":
            assert len(c["input"]["conversation_steps"]) >= 2
        for step in c["input"]["conversation_steps"]:
            if "context" in step:
                assert step["context"] in sources
    assert (DATA / "REVIEW.md").read_text(encoding="utf-8") == render_review(cases, sources)
    return {"status": "verified_candidate", "cases": 40, "categories": COUNTS, "sources": len(sources),
            "pending_human_review": 40, "approved_cases": 0, "live_verified": False, "executed_cases": 0, "model_calls": 0}


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False))
