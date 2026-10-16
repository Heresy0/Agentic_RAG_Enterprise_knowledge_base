"""Generate the 20-document expansion without model calls or database writes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.corpus_expansion_data import AS_OF, CASES, DOCUMENTS, GROUPS

CORPUS = ROOT / "sample_docs" / "xinghai_expansion"
MAIN_DATASET = ROOT / 'evals' / 'archive' / 'xinghai_v3'
EDITION = "xinghai-cc-expansion-v1"

RECORD_HANDLING = {
    "制度流程": "办理人员先核对项目编号、事项类型和业务发生日期，再选择适用版本。申请、批准、执行和复核分别记录，不用一个环节的完成代替其他环节。申请材料缺少范围或者时间时，退回补齐而不自行补造事实；批准者不应扩大文件未规定的适用对象。修订时保留旧版及生效边界，按发生日期解释新旧关系。",
    "历史版本": "复核人员先核对业务发生日期与归档适用期，再查当时申请和执行记录。旧文件用于解释历史依据，不承接新的审批事项；新版修订也不自动抹掉当时合法形成的记录。缺少历史凭证时应说明无法确认，不凭当前规则猜测当时已批准。查询历史标准与查询实际执行结果是两种问题，后者仍需对应任务证据。",
    "项目记录": "项目经理归档原始记录与附件，区分计划、内部确认、客户接受和最终签署。记录中的日期按各自事项解释，不能把计划节点当作实际完成，或者把一个局部通过扩大为整个交付通过。缺少原始附件时先补齐证据，不回填未发生的结果。后续修订保留原记录及修订原因，避免覆盖曾发现的问题和原来的待办状态。",
    "故障复盘": "复盘归档时核对事件编号、环境、受影响对象和时间口径。发现、处置、恢复确认与客户验收分别记录，不把临时缓解说成所有风险已消除。根因依据本事件的运行证据，不能仅因症状相似就复用另一事件的结论。后续检查未完成时保留待办，未观测到的数据不补造；复盘中的一次耗时不能被推广为所有事件的恢复承诺。",
    "跨部门资料": "交接人员核对任务编号与资料归口，按各自职责提交和复核证据。可见的入口资料只能说明办理路径，不能代替内部规范或者已签署的操作结果。跨部门问题需要分别核查相关来源，缺少一方资料时说明边界，不把另一方的确认扩大为双方已经完成。部门间协作使用受控记录关联，不在公共说明中复制内部明细或访问凭据。",
}
GROUP_HANDLING = {
    "公司公共": "公共说明只公开项目办理规则与入口，不保存员工个人评审结果、客户敏感明细或操作凭据。任务涉及部门内部规范时，应由实际获准人员查阅对应知识库。公共文件的可见范围不能替代系统授权，文中提到另一个部门或者资料名称也不意味着读者已拥有该资料。疑问由原办理部门核查，不从公共入口推导内部操作参数。",
    "人力资源部": "人力资源部按员工任务和支持日期核对登记，访问仅限本部门成员及显式授权人员。部门内的评审与登记不自动授予技术资源权限，也不说明薪酬奖金已发生变化。需要交接时只提供办理所需的最小事实，不把完整人员记录复制到公共资料。任务时间、申请时间和复核时间分别留存，不能因为复核较晚而错误选择新版标准。",
    "技术部": "技术部依据本项目对象与批次核对业务结果，访问仅限本部门成员及显式授权人员。与平台研发部协作时区分业务验收和运行恢复，不凭对方的通知代替自己负责的验证。对客户的确认保留明确范围，未签署的计划不描述成客户承诺。涉及数据或账号权限时另查正式批准记录，本项目文档不会自动产生新的数据访问授权。",
    "平台研发部": "平台研发部仅由获准人员核查本环境的运行证据，访问限本部门成员及显式授权人员。脱敏沙箱数据不能说明生产运行结果，技术部或客户的业务验收也不能代替配置复核。事件号、配置版本和操作对象分别保留，不凭类似名称混用日志。正文不得保存真实密码、访问令牌或客户机密，资料未观测到的范围仍保持未知。",
}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def filename(doc):
    return f"{doc['number']:02d}_{doc['title']}.md"


def render_document(doc):
    status = "历史归档" if doc["end"] else "现行"
    metadata = [f"# {doc['title']}", "", "星海科技有限公司", "",
                f"归口部门：{doc['group']}；入库范围：{doc['group']}。", "",
                f"文档编号：{doc['code']}；版本：{doc['version']}；生效日期：{doc['start']}；状态：{status}。"]
    if doc["end"]:
        metadata += [f"失效日期：{doc['end']}；适用期间：{doc['start']}至{doc['end']}。"]
    metadata += ["适用范围：澄川项目CC-2026-07的脱敏沙箱与配套支持；不替代其他项目及公司通用制度。", ""]
    for number, (heading, text) in enumerate(doc["sections"], 1):
        metadata += [f"## {number} {heading}", "", text, ""]
    metadata += ["## 5 记录复核与交接", "", RECORD_HANDLING[doc["kind"]], "",
                 "## 6 资料归口与访问边界", "", GROUP_HANDLING[doc["group"]], ""]
    return "\n".join(metadata)


def make_cases():
    by_number = {doc["number"]: doc for doc in DOCUMENTS}
    result = []
    for identifier, question, answer, facts, anchors, category in CASES:
        groups = list(dict.fromkeys(by_number[number]["group"] for number, _ in anchors))
        documents = list(dict.fromkeys(filename(by_number[number]) for number, _ in anchors))
        row = dict(id=identifier, question=question, reference_answer=answer, answerable=bool(anchors),
                   required_facts=facts, expected_evidence=[dict(document=filename(by_number[number]), text=text)
                                                          for number, text in anchors],
                   source_groups=groups, expected_scope=groups[0] if len(groups) == 1 else "全部可访问",
                   required_source_documents=documents, category=category, difficulty="medium",
                   as_of=AS_OF, annotation_version=EDITION, profile="全范围测试用户",
                   manual_review_required=True,
                   semantic_checks=["核对时间、版本、对象及否定范围；关键词齐全不能代替语义审核。"],
                   source_file_paths=[f"documents/{GROUPS[by_number[n]['group']]}/{filename(by_number[n])}"
                                      for n in dict.fromkeys(number for number, _ in anchors)])
        if not anchors:
            row["semantic_checks"] = ["拒答且不编造数值或完成日期；引用约定期限不能证明实际完成。"]
        result.append(row)
    return result


def validate_spec(texts, cases):
    if len(texts) != 20 or len(cases) != 20:
        raise ValueError("Expansion must contain 20 documents and 20 cases")
    if Counter(d["kind"] for d in DOCUMENTS) != {"制度流程": 6, "项目记录": 4, "故障复盘": 4,
                                                "历史版本": 4, "跨部门资料": 2}:
        raise ValueError("Unexpected document composition")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate case ID")
    by_name = {filename(doc): texts[doc["number"]] for doc in DOCUMENTS}
    covered = set()
    for case in cases:
        if not all(fact in case["reference_answer"] for fact in case["required_facts"]):
            raise ValueError(f"Reference answer missing facts: {case['id']}")
        for evidence in case["expected_evidence"]:
            if evidence["text"] not in by_name[evidence["document"]]:
                raise ValueError(f"Missing source anchor: {case['id']}")
            covered.add(evidence["document"])
    if covered != set(by_name):
        raise ValueError("Some documents have no evaluation evidence")
    for code in {d["code"] for d in DOCUMENTS if d["end"]}:
        pair = sorted((d for d in DOCUMENTS if d["code"] == code), key=lambda d: d["start"])
        if len(pair) != 2 or not pair[0]["end"] < pair[1]["start"]:
            raise ValueError("Historical/current validity periods overlap")


GUIDE = r"""# 星海知识库小规模扩容包

新增20份Markdown文档：6份流程、8份项目/故障记录、4份历史版本、2份跨部门资料。
基准日期为2026-10-13；文档、企业业务事实与题集均为人工设计的合成回归资料，不是实际企业数据或盲测基准。
延续星海公司，新增澄川项目CC-2026-07；规则仅适用于该项目，原有30份文档和原题集保持原样。

| 知识库 | 文档数 | 目录 |
| --- | ---: | --- |
| 公司公共 | 5 | documents/01_公司公共 |
| 人力资源部 | 4 | documents/02_人力资源部 |
| 技术部 | 6 | documents/03_技术部 |
| 平台研发部 | 5 | documents/04_平台研发部 |

只上传documents中对应部门的业务文档；不要上传本README、manifest、入库清单、生成器数据或evals中的题目和参考答案。
公共入口不复写HR评审节点和平台故障细节；跨部门资料不会自动改变权限。使用现有知识库和真实获准账号，逐库入库。

## 生成与检查

生成只使用本地Python，不请求模型，不获取密钥，不连接数据库。固定事实源位于scripts/corpus_expansion_data.py。
再次生成时内容一致则无变化；若目标已有不同内容，默认停止，审核后才使用--overwrite替换生成文件。
--check只核对生成物，不写文件。它检查证据在原文中存在，实际解析检查须执行下面的parsing评估。

```powershell
.venv\Scripts\python.exe scripts/build_knowledge_expansion.py
.venv\Scripts\python.exe scripts/build_knowledge_expansion.py --check
.venv\Scripts\python.exe scripts/merge_xinghai_evaluation.py --prepare-corpus
.venv\Scripts\python.exe scripts/evaluate_system.py --mode offline --layers parsing
```

## 入库与评估

入库会调用Embedding，下面为单库示例；替换UUID，并为四个目录分别使用正确知识库ID和已获准用户ID。不要对包根目录递归入库。
批量入库CLI是管理工具，直接连接数据库，操作人须事先核实这些ID的租户归属和授权；浏览器上传仍由API校验权限。
此前已有按文件哈希、切分版本、OCR修订和模型判断的幂等索引；仅文件字节不变不保证跨版本始终跳过。

```powershell
.venv\Scripts\python.exe scripts/index_documents.py --directory sample_docs/xinghai_expansion/documents/01_公司公共 --tenant-id <租户UUID> --knowledge-base-id <公共库UUID> --user-id <获准用户UUID>
```

新增20题已合并至evals/xinghai_v3，主单轮题集共80题（71道可回答、9道资料不足）；CC-001至CC-020覆盖本包全部20份资料。
by_scope适合逐库执行；跨库题需要全部可访问模式且具备所有来源库权限，不能对单个固定库运行混合题集。
使用evals/system_config.example.json的格式准备本地配置，四个知识库名称分组不变；令牌只放环境变量。
线上评估先取少量案例；查询Embedding、重排和答案生成会计费。本次包生成及离线验证不代表已入库或实际问答通过。

```powershell
# 只测检索/重排/上下文，不执行答案生成；仍会调用Embedding和重排。
.venv\Scripts\python.exe scripts/evaluate_system.py --mode live --config evals/system_config.local.json --layers retrieval,rerank,context --case-ids CC-001 CC-002 CC-005 CC-011 CC-016 --allow-model-calls
# 确认检索后再显式执行完整80题，费用高于只测新增20题。
.venv\Scripts\python.exe scripts/evaluate_system.py --mode live --config evals/system_config.local.json --layers retrieval,rerank,context,answer --allow-model-calls
```

固定了项目事实再生成文档与题集，属于同源合成回归。测试计划/实际、历史/现行、相似根因及跨库证据，不能以文档数量推导生产容量。
本包没有新增多轮或自动权限场景，主目录保留原8组多轮及12项权限用例。单库内部信息不能因在公共入口被提及而泄漏。
coverage.json只记录文件哈希、规模及证据覆盖，不包含虚构的模型准确率或Token账单。
生成器使用相同模板的记录交接部分，因此性能规模与业务语义难度应分别报告，不能视为20份独立真实企业样本。
"""


def build_artifacts(corpus):
    corpus = Path(corpus).resolve()
    texts = {doc["number"]: render_document(doc) for doc in DOCUMENTS}
    cases = make_cases()
    validate_spec(texts, cases)
    artifacts, entries = {}, []
    for doc in DOCUMENTS:
        relative = f"documents/{GROUPS[doc['group']]}/{filename(doc)}"
        text = texts[doc["number"]]
        artifacts[corpus / relative] = text
        entries.append(dict(file=relative, group=doc["group"], format="md", kind=doc["kind"],
                            document_code=doc["code"], business_version=doc["version"],
                            effective_from=doc["start"], effective_to=doc["end"], characters=len(text),
                            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(), features=[]))
    manifest = dict(company="星海科技有限公司", fictional=True, edition=EDITION, as_of=AS_OF,
                    total_documents=len(entries), counts={"md": len(entries)},
                    group_counts=dict(Counter(row["group"] for row in entries)), documents=entries)
    artifacts[corpus / "manifest.json"] = dumps(manifest)
    artifacts[corpus / "README.md"] = GUIDE
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=["file", "group", "kind", "sha256"], lineterminator="\n")
    writer.writeheader()
    writer.writerows({key: row[key] for key in writer.fieldnames} for row in entries)
    artifacts[corpus / "入库清单.csv"] = output.getvalue()
    coverage = dict(edition=EDITION, as_of=AS_OF, total_documents=20, single_turn_cases=20,
                    answerable_cases=18, unanswerable_cases=2,
                    covered_documents=sorted({ev["document"] for row in cases for ev in row["expected_evidence"]}),
                    categories=dict(Counter(row["category"] for row in cases)),
                    total_characters=sum(row["characters"] for row in entries),
                    character_range=[min(row["characters"] for row in entries), max(row["characters"] for row in entries)],
                    corpus_document_hashes={Path(row["file"]).name: row["sha256"] for row in entries},
                    model_calls=0, token_usage=None, quality_evaluation="not_run",
                    limitation="Synthetic source/anchor coverage only; not retrieval or semantic accuracy.")
    return artifacts, coverage


def write_artifacts(artifacts, *, check=False, overwrite=False):
    # Validate the entire set before writing: no partial replacement on conflict.
    changed = [path for path, value in artifacts.items()
               if not path.is_file() or path.read_bytes() != value.encode("utf-8")]
    if check and changed:
        raise ValueError("Generated files missing or changed: " + ", ".join(str(path) for path in changed))
    if not overwrite and not check:
        conflicts = [path for path in changed if path.exists()]
        if conflicts:
            raise ValueError("Existing content differs; review before --overwrite: " + ", ".join(map(str, conflicts)))
    for path in changed if not check else ():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(artifacts[path].encode("utf-8"))
    return len(changed)


def validate_inventory(corpus):
    root = (corpus / "documents").resolve()
    expected = {root / GROUPS[doc["group"]] / filename(doc) for doc in DOCUMENTS}
    existing = {path.resolve() for path in root.rglob("*") if path.is_file()}
    if existing - expected:
        raise ValueError("Unexpected files in documents; move them outside the ingestion directory")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    validate_inventory(args.corpus_dir)
    artifacts, coverage = build_artifacts(args.corpus_dir)
    changed = write_artifacts(artifacts, check=args.check, overwrite=args.overwrite)
    if args.corpus_dir.resolve() == CORPUS.resolve() and (MAIN_DATASET / "single_turn.jsonl").is_file():
        from scripts.merge_xinghai_evaluation import merged_artifacts
        main_artifacts = merged_artifacts()
        write_artifacts(main_artifacts, check=args.check, overwrite=args.overwrite)
    print(json.dumps(dict(status="verified" if args.check else "generated", changed_files=changed,
                          documents=20, cases=20, characters=coverage["total_characters"],
                          character_range=coverage["character_range"], model_calls=0), ensure_ascii=False))


if __name__ == "__main__":
    main()
