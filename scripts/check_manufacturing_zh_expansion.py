"""Read-only integrity and actual-parser audit of the Chinese expansion.

No application, database, embeddings, provider APIs or quality approval writes.
--parse additionally invokes the unchanged local document splitters.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "sample_docs/manufacturing_zh_expansion_v1"
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(manifest, sources, coverage):
    docs = sources["documents"]
    by_id = {d["id"]: d for d in docs}
    require(len(docs) == len(by_id) == manifest["total_documents"] == 55, "文档数量或编号错误")
    require(len({d["repository_path"] for d in docs}) == 55, "同一文件重复计数")
    require(len({d["sha256"] for d in docs}) == 55, "相同原始文件重复计数")
    require(manifest["status"] == "offline_corpus_candidate_pending_human_review", "候选状态不正确")
    require(manifest["approved_documents"] == 0 and manifest["indexed"] is False, "不能伪造审核或入库状态")
    require(manifest["external_model_calls"] == 0 and manifest["runtime_modified"] is False, "本版为离线零模型资料包")
    require(Counter(d["format"] for d in docs) == Counter(manifest["formats"]), "格式统计不一致")
    require(sum(d["origin"] == "new" for d in docs) == manifest["new_documents"] == 34, "新增文件统计不一致")
    require(sum(d["origin"] == "existing_reference" for d in docs) == manifest["referenced_documents"] == 21, "引用统计不一致")
    require(sum(d["origin"] == "new" and d["source_kind"] == "synthetic_business" for d in docs) == 30, "合成资料数量错误")
    require(sum(d["source_kind"].startswith("public_") for d in docs) == manifest["public_documents"] == 12, "公开资料数量错误")
    require(not any(d["scan"] for d in docs) and manifest["scan_documents"] == 0, "本版不增加扫描件")
    versions = {}
    for d in docs:
        path = (ROOT / d["repository_path"]).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), f"文件路径无效：{d['id']}")
        require(path.suffix == "." + d["format"], f"格式与扩展名不符：{d['id']}")
        require(sha(path) == d["sha256"] and path.stat().st_size == d["bytes"], f"原文件哈希不符：{d['id']}")
        require(d["language"] == "zh-CN", f"不是中文资料：{d['id']}")
        require(d["review_status"] == "pending_human_review" and d["approved"] is False, f"未经人工批准：{d['id']}")
        require(d["indexed"] is False and d["live_verified_this_batch"] is False, f"不能伪造运行状态：{d['id']}")
        if d["origin"] == "new":
            evidence = d["evidence"]
            require(len({u["unit_id"] for u in evidence}) == len(evidence) >= 4, f"证据条目缺失：{d['id']}")
            require(all(u["text"].strip() and u["heading"].strip() for u in evidence), "存在空证据")
            body = "\n".join(u["text"] for u in evidence)
            require(len(re.findall(r"[\u4e00-\u9fff]", body)) >= 80, f"中文正文过少：{d['id']}")
            require(not re.search(r"[A-Za-z]{3,}(?: +[A-Za-z]{3,}){5,}", body), f"出现英文段落：{d['id']}")
            if d["source_kind"] == "synthetic_business":
                require(d.get("factory_approved") is False and d.get("applicability_review_state") == "draft", "合成资料须保留草案状态")
                require(d["product_models"][0].startswith("DEMO-"), "合成产品标记缺失")
                require(all(u["scope"] == "synthetic_rule_or_record" for u in evidence), "不能把合成内容伪装为公开原文")
                if d["document_role"] == "instruction":
                    key = (d["product_models"][0], d["operation_codes"][0])
                    versions.setdefault(key, {})[d["business_revision"]] = d
            else:
                require(d["source_kind"] == "public_summary" and d["source_url"].startswith("https://"), "公开导读来源缺失")
                require(d["rights_status"] == "new_short_summary_original_not_republished", "导读不能伪装为原文")
                require(any(u["scope"] == "author_application_boundary" for u in evidence), "导读须区分应用边界与原文事实")
        else:
            legacy = json.loads((ROOT / d["legacy_manifest"]).read_text(encoding="utf-8-sig"))
            require(any(x["sha256"] == d["sha256"] for x in legacy["documents"]), "历史来源不在其冻结清单中")
    require(len(versions) == 6, "工序版本对数量错误")
    for pair in versions.values():
        require(set(pair) == {"1.0", "2.0"}, "缺少新旧版")
        require(date.fromisoformat(pair["1.0"]["effective_to"]) + timedelta(days=1) == date.fromisoformat(pair["2.0"]["effective_from"]), "新旧版区间重叠或缺口")
    rows = coverage["rows"]
    require(coverage["status"] == "coverage_plan_not_gold_dataset" and coverage["executable"] is False, "覆盖规划不是可执行黄金集")
    require(coverage["legacy_question_count"] == 40 and coverage["target_candidate_count"] == 100 and coverage["approved_new_questions"] == 0, "题集边界错误")
    require(len(rows) == len({r["id"] for r in rows}) == coverage["new_topic_count"] == 60, "覆盖主题数量错误")
    require(Counter(r["category"] for r in rows) == {"guidance": 18, "erpnext": 12, "openmes": 12, "safety": 12, "conversation": 6}, "覆盖分配错误")
    for r in rows:
        require(r["status"] == "evidence_topic_only" and r["human_review"] == "pending" and r["independent_holdout"] is False, "不能伪造金标或盲测")
        source = by_id[r["source_id"]]
        require(r["source_family"] == source["source_family"] and r["runtime_prerequisite"], "分组或前置条件缺失")
        anchor = next(u for u in source["evidence"] if u["unit_id"] == r["unit_id"])
        require(r["evidence_quote"] == anchor["text"], "覆盖主题引用与原文不一致")
    return docs


def parse_documents(docs):
    from backend.app.knowledge.rag import split_file, CHUNKING_VERSION
    from backend.app.documents.chunk_quality import summarize_quality
    rows = []
    for d in docs:
        chunks = split_file(ROOT / d["repository_path"])
        require(chunks and all(c.page_content.strip() for c in chunks), f"解析为空：{d['id']}")
        require(all("section_path" in c.metadata for c in chunks), f"章节元数据缺失：{d['id']}")
        combined = normalized("\n".join(c.page_content for c in chunks))
        require(len(re.findall(r"[\u4e00-\u9fff]", combined)) >= 40, f"解析后中文正文不足：{d['id']}")
        anchor_rows = []
        for u in d.get("evidence", []):
            hits = [i for i, c in enumerate(chunks) if normalized(u["text"]) in normalized(c.page_content)]
            require(hits, f"证据未完整进入分块：{d['id']}/{u['unit_id']}")
            anchor_rows.append(dict(unit_id=u["unit_id"], chunk_indices=hits,
                                    pages=sorted({c.metadata.get("page_start") for i, c in enumerate(chunks) if i in hits and c.metadata.get("page_start")})))
        require(not any("ocr" in c.metadata.get("parser", "").lower() for c in chunks), f"意外需要OCR：{d['id']}")
        rows.append(dict(id=d["id"], chunks=len(chunks), source_sha256=d["sha256"],
                         chunk_types=dict(Counter(c.metadata.get("chunk_type", "unknown") for c in chunks)),
                         quality=summarize_quality(chunks), anchors=anchor_rows,
                         empty_section_chunks=sum(not c.metadata["section_path"] for c in chunks)))
    return dict(chunking_version=CHUNKING_VERSION, documents=len(rows), chunks=sum(r["chunks"] for r in rows),
                anchors=sum(len(r["anchors"]) for r in rows), rows=rows)


def probes(manifest, sources, coverage):
    mutations = {
        "文件哈希损坏": lambda s, c: s["documents"][0].update(sha256="0" * 64),
        "英文语料混入": lambda s, c: s["documents"][0].update(language="en"),
        "伪造人工批准": lambda s, c: s["documents"][0].update(approved=True),
        "证据引用篡改": lambda s, c: c["rows"][0].update(evidence_quote="原文不存在的结论"),
        "伪造独立盲测": lambda s, c: c["rows"][0].update(independent_holdout=True),
    }
    outcomes = {}
    for name, mutate in mutations.items():
        s, c = deepcopy(sources), deepcopy(coverage)
        mutate(s, c)
        try:
            validate(manifest, s, c)
        except ValueError:
            outcomes[name] = "rejected"
        else:
            raise ValueError(f"损坏探针被错误接受：{name}")
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parse", action="store_true")
    parser.add_argument("--probes", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    network_events = []

    def offline_guard(event, values):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            network_events.append(event)
            raise RuntimeError("离线检查禁止网络连接")

    sys.addaudithook(offline_guard)
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files_sha256"].items():
        require(sha(CORPUS / name) == digest, f"清单哈希错误：{name}")
    sources = json.loads((CORPUS / "sources.json").read_text(encoding="utf-8"))
    coverage = json.loads((CORPUS / "coverage.json").read_text(encoding="utf-8"))
    docs = validate(manifest, sources, coverage)
    result = dict(status="passed", scope="offline_corpus_integrity_not_gold_approval_or_model_score",
                  documents=len(docs), topics=60, external_model_calls=0,
                  manifest_sha256=sha(CORPUS / "manifest.json"), checker_sha256=sha(__file__))
    if args.probes:
        result["probes"] = probes(manifest, sources, coverage)
    if args.parse:
        result["actual_parser"] = parse_documents(docs)
    require(not network_events, "检查期间发生网络访问")
    result["network_connect_attempts"] = 0
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in result.items() if k != "actual_parser"} |
                     ({"parsed_chunks": result["actual_parser"]["chunks"], "checked_anchors": result["actual_parser"]["anchors"]} if args.parse else {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
