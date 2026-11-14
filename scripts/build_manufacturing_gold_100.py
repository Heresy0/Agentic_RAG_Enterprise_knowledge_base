"""Author the offline 100-case candidate from frozen local sources only.

No QA, model, embeddings, HTTP, runtime configuration or approval writes.
Reviewer identity is always assistant, never an invented human sign-off.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.manufacturing_gold_100_data import additions, LEGACY_NOTES

DATA = ROOT / "evals/manufacturing_gold_100_v1"
LEGACY = ROOT / "evals/manufacturing_gold_v1"
CORPUS = ROOT / "sample_docs/manufacturing_zh_expansion_v1"
COUNTS = {"guidance":30,"erpnext":20,"openmes":20,"safety":20,"conversation":10}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_json(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")


def read_sources():
    from pypdf import PdfReader
    sources = deepcopy(json.loads((LEGACY/"sources.json").read_text(encoding="utf-8"))["sources"])
    inventory = json.loads((CORPUS/"sources.json").read_text(encoding="utf-8"))["documents"]
    parser_report = json.loads((ROOT/"docs/reports/manufacturing-zh-expansion-20261112-r1.json").read_text(encoding="utf-8"))
    locators = {x["id"]:{u["unit_id"]:u["pages"] for u in x["anchors"]} for x in parser_report["actual_parser"]["rows"]}
    for r in inventory:
        if r["origin"] == "new" and r["source_kind"] == "synthetic_business":
            sources[r["id"]] = dict(kind="document", repository_path=r["repository_path"],
                original_sha256=r["sha256"], checksum_mode="raw_bytes", source_origin="synthetic_business",
                source_family=r["source_family"], language="zh-CN", format=r["format"],
                unit_coverage="selected_sections_not_full_document", extraction="frozen_corpus_sections_checked_against_original",
                live_verified_this_batch=False, corpus_review_state="draft_not_platform_approved",
                units=[dict(id=u["unit_id"], text=u["text"], heading=u["heading"], physical_pages=locators[r["id"]][u["unit_id"]]) for u in r["evidence"]])
        if r["id"] in {"ZH-PUB-01","ZH-PUB-03","ZH-PUB-04"}:
            path = ROOT/r["repository_path"]
            if r["format"] == "pdf":
                pages=[p.extract_text().strip() for p in PdfReader(path).pages]
                if r["id"]=="ZH-PUB-01":
                    second=pages[1][pages[1].index("第三条"):].split("——")[0].strip()
                    third=pages[2][:pages[2].index("第四条")].strip()
                    excerpts=[(2,second),(3,third)]
                else:
                    sixth=pages[5][pages[5].index("（十二）"):pages[5].index("（十三）")].strip()
                    seventh=pages[6][pages[6].index("（十四）"):pages[6].index("（十五）")].strip()
                    excerpts=[(6,sixth),(7,seventh)]
                units=[dict(id=f"PAGE-{i:03}",text=text,physical_pages=[i]) for i,text in excerpts]
                extraction = "pypdf selected complete clauses with physical pages; no semantic rewriting"
            else:
                lines = [x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
                markers=("（三）明确追溯方式","（四）统一技术要求","（七）保障追溯数据安全")
                units = [dict(id=f"LINE-{i:03}",text=line,physical_pages=[]) for i,line in enumerate(lines,1) if any(k in line for k in markers)]
                extraction = "selected original nonblank lines retaining original line numbers"
            sources[r["id"]] = dict(kind="document",repository_path=r["repository_path"],original_sha256=r["sha256"],
                checksum_mode="raw_bytes",source_origin="frozen_chinese_public_reference",source_family=r["source_family"],
                language="zh-CN",format=r["format"],extraction=extraction,unit_coverage="selected_sections_not_full_document",
                source_url=r["source_url"],rights_status=r["rights_status"],
                validity_status="archived_edition_not_current_legal_or_factory_certification",live_verified_this_batch=False,units=units)
    return sources


def evidence_factory(sources):
    def evidence(sid, uid):
        u = next(u for u in sources[sid]["units"] if u["id"] == uid)
        return dict(source_id=sid,kind="text",unit_id=uid,quote=u["text"])

    def spec(sid, needle):
        matches = [u for u in sources[sid]["units"] if needle in u["text"]]
        if len(matches) != 1:
            raise ValueError(f"spec anchor ambiguous or absent: {sid}/{needle}/{len(matches)}")
        return evidence(sid,matches[0]["id"])
    return evidence,spec


def make_case(category, question, answer, facts, forbidden, evidence, review_note, *, mode="offline_document_candidate", selection=None, preconditions=None, steps=None):
    evidence = [dict(e,id=f"E{i:02}") for i,e in enumerate(evidence,1)]
    return dict(category=category,question=question,test_mode=mode,
        input=dict(role="described_by_preconditions",selection=selection or {},context_source_id=None,
                   preconditions=preconditions or ["只阅读指定版本的本地中文原文，不请求当前业务API，不把合成台账或历史原文当作实时执行证据。"],
                   conversation_steps=steps or [],selection_identifiers="offline_aliases_require_isolated_resolution" if selection else "not_an_api_request"),
        expected=dict(behavior="error" if mode=="integration_protocol_candidate" else "answer",
                      answerable=mode!="integration_protocol_candidate",http_status=409 if mode=="integration_protocol_candidate" else None,
                      reference_answer=answer,required_facts=[dict(id=f"F{i:02}",text=text,evidence_ids=[evidence[j]["id"] for j in indices]) for i,(text,indices) in enumerate(facts,1)],
                      optional_facts=[],forbidden_claims=forbidden,
                      grading="按问题逐项核对语义、对象、条件与引用；允许等价表达。未问的说明不得升级为必答点，原文或字段不支持的执行结论不得给分。"),
        evidence=evidence,_review_note=review_note,
        provenance=dict(split="regression_candidate",independent_holdout=False,
                        leakage_groups=[],related_legacy_cases=[],answer_origin="assistant_authored_from_local_evidence_not_run_outputs"))


def public_cases(make,sources,evidence):
    def line(needle):
        matches=[u for u in sources["ZH-PUB-03"]["units"] if needle in u["text"]]
        if len(matches)!=1: raise ValueError("public anchor not unique: "+needle)
        return evidence("ZH-PUB-03",matches[0]["id"])
    return [
        make("guidance","按本库归档的制造业质量管理数字化实施指南第十四项，质量数据管理覆盖哪些环节，职责与权限如何处理？",
             "应管理质量数据的采集、管理、处理、分析、应用全过程，明确各环节职责和权限，并强化跨部门及部门内的数据管理机制；这不表示某企业已经落实。",
             [("覆盖采集、管理、处理、分析、应用全过程",[0]),("明确各环节职责权限，强化跨部门及部门内机制",[0])],
             ["只保存数据就等于全过程管理", "指南发布证明演示企业已经完成建设"], [evidence("ZH-PUB-04","PAGE-007")],
             "物理第7页第十四项原文完整，保留全过程与职责权限；问的是归档文本，不作当前法规适用或企业达标认定。"),
        make("guidance","按归档指南第十二项，物料数字化追溯覆盖哪些环节，一物一码是否对所有企业所有物料无条件要求？",
             "覆盖采购、生产、仓储、物流、交付及售后服务全过程。原文限定为有条件的企业对关键物料实施一物一码，不能扩张为所有企业、所有物料的无条件要求。",
             [("采购、生产、仓储、物流、交付、售后服务全过程",[0]),("有条件的企业、关键物料两个限定均保留",[0])],
             ["所有企业所有物料必须一物一码", "一物一码足以证明已完成质量放行"], [evidence("ZH-PUB-04","PAGE-006")],
             "物理第6页第十二项同时核对流程六环节和有条件、关键物料两项限定，避免把条件建议改成普遍要求。"),
        make("guidance","仅按本库2023年第75号令归档文本第三条，主要负责人和质量安全总监、质量安全员的责任关系是什么？",
             "归档文本规定主要负责人对本单位产品质量安全工作全面负责；质量安全总监、质量安全员按岗位职责协助主要负责人做好管理工作。这里只解释归档文本，不认证当前法律或特定企业适用性。",
             [("主要负责人对本单位产品质量安全工作全面负责",[0]),("总监与安全员按岗位职责协助主要负责人",[0,1])],
             ["设置总监后主要负责人不再负责", "把归档条款认定为对任意企业当前适用的法律结论"],
             [evidence("ZH-PUB-01","PAGE-002"),evidence("ZH-PUB-01","PAGE-003")],
             "第三条跨物理第2至3页，完整主语必须联读；不把下一条的职责或当前效力混入本题。"),
        make("guidance","按本库归档的2024年追溯实施意见，企业是否只能使用一种指定编码，选择追溯方式还需满足什么？",
             "不是只能用某一种指定编码。生产销售单位可自主选择能够实现质量安全追溯并与追溯平台对接的任一种追溯方式，包括原文列举的已有追溯码或系统。",
             [("可自主选择任一种符合条件的追溯方式",[0]),("同时满足实现追溯、与平台对接",[0])],
             ["只能使用一种固定品牌编码", "任意编码无需追溯信息和平台对接"], [line("（三）明确追溯方式")],
             "保留任一种已有方式与实现追溯、平台对接的联合条件；不把可选择误解成无条件自由编码。"),
        make("guidance","按归档2024年实施意见，对可以不使用信息化追溯的定制产品，可以怎样追溯、由谁落实，记录至少保留多久？",
             "对题设这类定制产品，生产方和使用方通过记录台账或签订合同等方式实现质量安全追溯，有关记录或台账至少保留3年；不能扩展为所有产品均可免除信息化追溯。",
             [("限定可以不使用信息化追溯的定制产品",[0]),("生产方和使用方通过台账或合同等方式落实",[0]),("记录或台账至少保留3年",[0])],
             ["所有产品均可免信息化追溯", "至少3年等于恰好3年后必须删除"], [line("（四）统一技术要求")],
             "按整段保留对象、双方主体、方式和至少3年的下限；不是对当前任意产品保存期限的法律建议。"),
        make("guidance","按归档2024年实施意见，参与追溯的相关方对数据和安全防护有哪些要求，仅能查询到数据是否足够？",
             "相关方应防护追溯平台与追溯数据，确保数据真实、完整、准确、安全，并做到可查证和不可篡改。仅能查询到数据不足以满足这些要求。",
             [("平台与数据都需安全防护",[0]),("真实、完整、准确、安全",[0]),("可查证且不可篡改，不能只满足能查询",[0])],
             ["数据可查即代表真实且未篡改", "公开追溯意味着所有数据对所有人公开"], [line("（七）保障追溯数据安全")],
             "同段逐项检查真实性、完整性、准确性、安全、可查证与不可篡改，不把可查询替代质量与安全。"),
    ]


def render_review(cases,sources,reviews):
    notes={r["case_id"]:r for r in reviews}
    lines=["# 制造业100题候选逐题复核", "", "100题已由助手逐题完成非独立复核，独立人工批准0题，工厂业务批准0题。本文件没有模型回答或执行成绩。", "",
           "001至040继承旧候选，036仅在新版本把未问的细节改为可选。041至094来自三个合成资料包，095至100使用已有中文原件。54道新增离线资料题没有HTTP状态断言，6道新增协议题尚需隔离夹具与真实ID解析。", "",
           "| 编号 | 类别 | 问题 | 模式 |", "| --- | --- | --- | --- |"]
    lines += [f"| [{c['id']}](#{c['id'].lower()}) | {c['category']} | {c['question']} | {c['test_mode']} |" for c in cases]
    for c in cases:
        x,review=c["expected"],notes[c["id"]]
        lines += ["", "## "+c["id"],"", "**问题：** "+c["question"],"", "**测试模式：** "+c["test_mode"],"",
                  "**选择：** `"+json.dumps(c["input"]["selection"],ensure_ascii=False)+"`","",
                  "**前置条件：** "+"；".join(c["input"]["preconditions"]),"", "**标准答案：** "+x["reference_answer"],"",
                  "**预期行为：** "+x["behavior"]+"；HTTP "+("不适用，离线资料题" if x["http_status"] is None else str(x["http_status"])),"", "**必答点：**",""]
        lines += ["- "+f["id"]+"："+f["text"]+"；证据 "+"、".join(f["evidence_ids"]) for f in x["required_facts"]]
        if x.get("optional_facts"):
            lines += ["", "**可选说明 不计为必答：**",""]+["- "+f["text"] for f in x["optional_facts"]]
        lines += ["", "**禁止结论：** "+"；".join(x["forbidden_claims"]),"", "**来源证据：**",""]
        for e in c["evidence"]:
            source=sources[e["source_id"]]
            if e["kind"]=="text":
                unit=next(u for u in source["units"] if u["id"]==e["unit_id"])
                pages=unit.get("physical_pages",[])
                location="；物理页 "+",".join(map(str,pages)) if pages else ""
                lines += [f"- {e['id']} / {e['source_id']} / {e['unit_id']}{location}","", "```text",e["quote"],"```",""]
            elif e["kind"]=="field":
                lines += [f"- {e['id']} / {e['source_id']} / `{e['field_path']}` = `{json.dumps(e['value'],ensure_ascii=False)}`；历史投影，本轮未实时复读。"]
            else:
                lines += [f"- {e['id']} / {e['source_id']}：全部{len(e['unit_ids'])}个条目；{e['reason']}"]
        if c["input"]["conversation_steps"]:
            lines += ["", "**多步协议 本轮不执行：**", "", "```json",json.dumps(c["input"]["conversation_steps"],ensure_ascii=False,indent=2),"```"]
        lines += ["", "**助手逐题复核：** "+review["rationale"],"", "**审核身份：** assistant / non_independent；仅接受为离线候选，未批准平台样本、来源或工厂业务。", ""]
    lines += ["## 来源索引", ""]
    for sid,s in sources.items():
        if "repository_path" in s:
            lines.append(f"- {sid}：[原件](../../{s['repository_path']})；{s.get('source_origin',s['kind'])}。")
        else:
            lines.append(f"- {sid}：历史白名单投影；SHA-256 `{s['projection_sha256']}`；当前实时核验为false。")
    return "\n".join(lines)+"\n"


def main():
    if (DATA/"manifest.json").exists(): raise SystemExit("保留已生成版本；需另建新版，不覆盖。")
    DATA.mkdir(parents=True,exist_ok=True)
    sources=read_sources()
    evidence,spec=evidence_factory(sources)
    cases=[json.loads(line) for line in (LEGACY/"cases.jsonl").read_text(encoding="utf-8").splitlines()]
    original=deepcopy(cases)
    for c in cases:
        c.pop("review")
        c["test_mode"]="legacy_manufacturing_candidate"
        c["expected"].setdefault("optional_facts",[])
    c=cases[35]
    optional=c["expected"]["required_facts"].pop()
    c["expected"]["optional_facts"]=[dict(optional,id="O01",reason="题目只问执行状态能否推出检验签署完成，不把未问数量设为必答。")]
    new=additions(make_case,evidence,spec,lambda make:public_cases(make,sources,evidence))
    reviews=[]
    stamp=datetime.now(timezone.utc).isoformat()
    for i,c in enumerate(cases+new,1):
        c["id"]=f"MFG-GOLD-{i:03}"
        note=c.pop("_review_note") if i>40 else LEGACY_NOTES[i-1]
        c["review_id"]=c["id"]
        if i>40:
            source_groups={sources[e["source_id"]].get("source_family") for e in c["evidence"]}
            c["provenance"]["leakage_groups"]=["document-family:"+x for x in sorted(source_groups-{None})]
            if i<=94:
                r=(i-41)//18
                from scripts.manufacturing_zh_expansion_data import PRODUCTS
                c["provenance"]["leakage_groups"].append("work-order:synthetic:"+PRODUCTS[r]["work_order"])
                c["provenance"]["parameterized_group"]=f"new-family-intent-{(i-41)%18+1:02}"
        reviews.append(dict(case_id=c["id"],status="assistant_reviewed_non_independent",decision="accepted_as_candidate",
                            reviewer_type="assistant",reviewer="Codex assistant",human_reviewer=None,
                            independent=False,professional_factory_review=False,reviewed_at=stamp,
                            rationale=note,evidence_ids=[e["id"] for e in c["evidence"]],
                            scope="offline_question_answer_source_consistency_not_runtime_or_factory_approval"))
    cases+=new
    assert len(cases)==100 and Counter(c["category"] for c in cases)==COUNTS
    used={e["source_id"] for c in cases for e in c["evidence"]}
    sources={sid:s for sid,s in sources.items() if sid in used}
    for name,rows in [("cases.jsonl",cases),("reviews.jsonl",reviews)]:
        (DATA/name).write_text("\n".join(json.dumps(c,ensure_ascii=False,separators=(",",":")) for c in rows)+"\n",encoding="utf-8",newline="\n")
    write_json(DATA/"sources.json",dict(schema_version="manufacturing-gold-100-sources-v1",base_revision="1852279",current_live_verification=False,sources=sources))
    lineage=dict(legacy_dataset="evals/manufacturing_gold_v1",legacy_cases_sha256=digest((LEGACY/"cases.jsonl").read_bytes()),
                 inherited_cases=40,new_cases=60,coverage_plan_sha256=digest((CORPUS/"coverage.json").read_bytes()),
                 changes=[dict(case_id="MFG-GOLD-036",change="move_unasked_required_F04_to_optional_O01",old_case_sha256=digest(canonical(original[35])),reason=LEGACY_NOTES[35])],
                 coverage_departure="原6项公开导读主题改为6道有本地原件的中文公开资料题，因导读缺少原始网页快照；类别题数不变。",
                 review_migration="旧40题内嵌review仅保留在原数据集；新100题统一以reviews.jsonl为本轮助手审核记录，不把旧pending状态伪改成人工批准。")
    write_json(DATA/"lineage.json",lineage)
    (DATA/"REVIEW.md").write_text(render_review(cases,sources,reviews),encoding="utf-8",newline="\n")
    write_json(DATA/"manifest.json",dict(schema_version="manufacturing-gold-100-manifest-v1",dataset="manufacturing_gold_100_v1",date="2026-11-12",
        status="candidate_assistant_reviewed_non_independent",cases=100,categories=COUNTS,
        assistant_reviewed_cases=100,assistant_accepted_candidate_cases=100,pending_assistant_review=0,
        human_approved_cases=0,professional_factory_approved_cases=0,platform_approved_cases=0,
        executable=False,live_verified=False,model_calls=0,executed_cases=0,
        test_modes=dict(Counter(c["test_mode"] for c in cases)),
        files_sha256={name:digest((DATA/name).read_bytes()) for name in ["cases.jsonl","reviews.jsonl","sources.json","lineage.json","REVIEW.md"]},
        implementation_sha256={name:digest((ROOT/name).read_bytes()) for name in ["scripts/build_manufacturing_gold_100.py","scripts/manufacturing_gold_100_data.py","scripts/check_manufacturing_gold_100.py"]}))
    print(json.dumps(dict(cases=len(cases),sources=len(sources),categories=COUNTS,assistant_reviews=len(reviews),model_calls=0),ensure_ascii=False))


if __name__=="__main__":
    main()
