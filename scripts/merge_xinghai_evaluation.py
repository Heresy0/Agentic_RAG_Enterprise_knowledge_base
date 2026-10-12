"""Maintain one main 80-case pack; preserve original cases and historical reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.corpus_expansion_data import AS_OF, GROUPS
MAIN = ROOT / "evals/xinghai_v3"
BASE_CORPUS = ROOT / "sample_docs/xinghai_base"
EXPANSION_CORPUS = ROOT / "sample_docs/xinghai_expansion"
COMBINED_CORPUS = ROOT / "output/knowledge-corpus-unified"
REVISION = "2026-10-13-unified-v1"
MARKER = "## 澄川新增单轮题"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def combined_manifest(base_corpus=BASE_CORPUS, expansion_corpus=EXPANSION_CORPUS):
    manifests = [read_json(root / "manifest.json") for root in (base_corpus, expansion_corpus)]
    rows = [row for manifest in manifests for row in manifest["documents"]]
    if len(rows) != 50 or len({Path(row["file"]).name for row in rows}) != 50:
        raise ValueError("Expected 50 unique source documents")
    return dict(company="星海科技有限公司", fictional=True, edition="xinghai-unified-50-v1",
                total_documents=50, counts=dict(Counter(row["format"] for row in rows)),
                group_counts=dict(Counter(row["group"] for row in rows)), documents=rows)


def prepare_corpus(base_corpus=BASE_CORPUS, expansion_corpus=EXPANSION_CORPUS, output=COMBINED_CORPUS):
    roots = [Path(base_corpus).resolve(), Path(expansion_corpus).resolve()]
    output = Path(output).resolve()
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in roots):
        raise ValueError("Combined output must be separate from source corpora")
    manifest = combined_manifest(*roots)
    copies = []
    for root in roots:
        for row in read_json(root / "manifest.json")["documents"]:
            source = (root / row["file"]).resolve()
            target = (output / row["file"]).resolve()
            if not source.is_relative_to(root / "documents") or not target.is_relative_to(output / "documents"):
                raise ValueError("Source document path outside documents")
            if hashlib.sha256(source.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("Source document hash changed")
            copies.append((source, target))
    expected = {target for _, target in copies}
    existing = {p.resolve() for p in (output / "documents").rglob("*") if p.is_file()}
    if existing - expected:
        raise ValueError("Unexpected files in combined corpus")
    for source, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
    path = output / "manifest.json"
    content = json_text(manifest)
    if not path.is_file() or path.read_text(encoding="utf-8") != content:
        path.write_bytes(content.encode("utf-8"))
    return manifest


def append_cases_to_book(text, rows, answers=False):
    before, separator, tail = text.partition("## 二、多轮对话")
    before = before.partition(MARKER)[0].rstrip().replace("单轮问答（60题）", "单轮问答（80题）")
    extra = ["", MARKER, "", "以下20题按2026-10-13及题目明确日期判断；原题仍保持原业务日期和评分要求。", ""]
    for row in rows:
        extra += [f"### {row['id']} · {row['category']} · {row['difficulty']}", "", row["question"], ""]
        if answers:
            extra += ["参考答案：" + row["reference_answer"], "",
                      "核心事实：" + "；".join(row["required_facts"]), ""]
            extra += [f"来源：{item['document']}；证据：{item['text']}" for item in row["expected_evidence"]]
            extra += ["", "人工检查：" + "；".join(row["semantic_checks"]), ""]
        else:
            extra += [f"范围：{row['expected_scope']}。", ""]
    return before + "\n" + "\n".join(extra) + "\n" + separator + tail


def merged_artifacts(output=MAIN, base_corpus=BASE_CORPUS, expansion_corpus=EXPANSION_CORPUS):
    from scripts.build_knowledge_expansion import make_cases
    from backend.app.documents.text_splitter import split_markdown
    from backend.app.evaluation.metrics import evidence_metrics

    output = Path(output)
    original = [json.loads(line) for line in (output / "single_turn.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    base = [row for row in original if row["id"].startswith("XH-")]
    if len(base) != 60 or sum(row["answerable"] for row in base) != 53:
        raise ValueError("Original 60-case baseline must remain intact")
    if any(not row["id"].startswith(("XH-", "CC-")) for row in original):
        raise ValueError("Unrecognized cases: merge explicitly rather than dropping them")
    extra = make_cases()
    unexpected = {row["id"] for row in original if row["id"].startswith("CC-")} - {row["id"] for row in extra}
    if unexpected:
        raise ValueError("Unrecognized expansion cases must not be dropped")
    for row in extra:
        row["expected_scope"] = row["source_groups"][0] if len(row["source_groups"]) == 1 else "all_accessible"
        row["citation_requirement"] = "all_required_sources" if len(row["required_source_documents"]) > 1 else "one_supporting_source" if row["answerable"] else "no_unsupported_citation"
    rows = [*base, *extra]
    if len({row["id"] for row in rows}) != 80:
        raise ValueError("Duplicate case IDs")
    manifest = combined_manifest(base_corpus, expansion_corpus)
    coverage = read_json(output / "coverage.json")
    checks = [row for row in coverage["evidence_checks"] if row["id"].startswith("XH-")]
    by_name = {Path(row["file"]).name: row for row in read_json(expansion_corpus / "manifest.json")["documents"]}
    parsed = {}
    for row in extra:
        for evidence in row["expected_evidence"]:
            name = evidence["document"]
            if name not in parsed:
                parsed[name] = [dict(document_name=name, content=chunk.page_content)
                                for chunk in split_markdown(expansion_corpus / by_name[name]["file"])]
            if not evidence_metrics([evidence], parsed[name])["all_evidence_present"]:
                raise ValueError("Expansion anchor missing from parsed source")
            checks.append(dict(id=row["id"], document=name,
                               method="current_project_splitter_chunk_with_current_file_hash", found=True))
    coverage.update(as_of=AS_OF, as_of_by_suite={"XH": "2026-10-04", "CC": AS_OF},
                    annotation_version=REVISION, corpus_edition=manifest["edition"],
                    single_turn_cases=80, answerable=71, unanswerable=9, covered_documents=50,
                    covered_formats=manifest["counts"], single_turn_scopes=dict(Counter(r["expected_scope"] for r in rows)),
                    categories=dict(Counter(r["category"] for r in rows)), evidence_checks=checks,
                    corpus_document_hashes={Path(r["file"]).name: r["sha256"] for r in manifest["documents"]},
                    base_cases=60, expansion_cases=20, live_evaluation_executed=False,
                    external_model_calls=0, database_writes=0)
    artifacts = {output / "single_turn.jsonl": jsonl(rows), output / "coverage.json": json_text(coverage)}
    for group in GROUPS:
        artifacts[output / "by_scope" / f"{group}.jsonl"] = jsonl([r for r in rows if r["source_groups"] == [group]])
    artifacts[output / "cross_scope.jsonl"] = jsonl([r for r in rows if len(r["source_groups"]) > 1])
    artifacts[output / "unanswerable.jsonl"] = jsonl([r for r in rows if not r["answerable"]])
    for book, answers in [("测试题册.md", False), ("答案与评分.md", True)]:
        artifacts[output / book] = append_cases_to_book((output / book).read_text(encoding="utf-8"), extra, answers)
    guide = (output / "README.md").read_text(encoding="utf-8")
    guide = guide.partition("\n## 统一扩容入口")[0]
    guide = guide.replace("语料：部门版 v3 的30份虚构业务文档", "语料：部门版v3的30份及澄川扩容20份虚构业务文档")
    guide = guide.replace("基准日期：2026-10-04。", "业务日期：原XH题为2026-10-04，CC题为2026-10-13；明确日期以题目为准。", 1)
    guide = guide.replace("60道单轮题：53道可回答、7道资料不足；覆盖全部30份文档。", "80道单轮题：71道可回答、9道资料不足；覆盖50份文档（原30份＋澄川20份）。")
    guide = guide.replace("合计80个案例", "合计100个案例").replace("单轮60、对话24轮", "单轮80、对话24轮").replace("共96个检查步骤", "共116个检查步骤").replace("不等于96次模型调用", "不等于116次模型调用")
    guide = guide.replace("60道单轮共300分", "80道单轮共400分").replace("420分", "520分")
    guide = guide.replace("当前数据库保留了旧版3份资料，共33份；题集基于新30份。", "历史构建时数据库保留旧版资料；统一题集目前覆盖50份指定语料，数据库可能另有未列入本题集的旧资料。")
    guide += "\n## 统一扩容入口\n\n默认入口仍为本目录：原60题与新增CC-001至CC-020合并为80题；8组多轮和12项权限用例保持原样。\n原XH题按2026-10-04及其题目日期，CC题按2026-10-13及其题目日期；合并不修改原答案或评分标准。\n旧报告的60题分数不直接与80题总分比较；按题号子集比较，报告哈希随题集合并变化。\n\n统一解析语料在`output/knowledge-corpus-unified`，仅为原文的本地副本，不重新入库或生成向量。\n运行`python scripts/merge_xinghai_evaluation.py --prepare-corpus`准备50份原文；原30份位于版本管理中的`sample_docs/xinghai_base`，新增20份位于`sample_docs/xinghai_expansion`，克隆后即可准备。\n`evaluate_system.py`默认读取本80题及统一语料目录；仅测新增题可加`--case-ids CC-001 CC-002`等题号。\n重新生成原题集或扩容包会同步这80题；本次合并只验证结构与来源，不代表实测问答通过。\n"
    artifacts[output / "README.md"] = guide
    return artifacts


def merge(output=MAIN, base_corpus=BASE_CORPUS, expansion_corpus=EXPANSION_CORPUS):
    artifacts = merged_artifacts(output, base_corpus, expansion_corpus)
    for path, content in artifacts.items():
        if not path.is_file() or path.read_bytes() != content.encode("utf-8"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
    return artifacts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-corpus", action="store_true")
    args = parser.parse_args()
    if args.prepare_corpus:
        prepare_corpus()
    merge()
    print(json.dumps(dict(single_turn_cases=80, answerable=71, unanswerable=9, documents=50,
                          model_calls=0, database_writes=0)))
