"""Verify frozen candidates and sources locally; never execute questions or APIs."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_manufacturing_gold_100 import COUNTS, DATA, LEGACY, canonical, digest, render_review


def require(condition,message):
    if not condition: raise ValueError(message)


def norm(text):
    return re.sub(r"\s+","",unicodedata.normalize("NFKC",text))


def value_at(data,path):
    for part in path.split("."): data=data[part]
    return data


def verify_sources(sources):
    from pypdf import PdfReader
    from docx import Document
    counters=Counter()
    for sid,s in sources.items():
        if "repository_path" not in s:
            require(s["kind"] in {"archived_business_snapshot","archived_mapping_snapshot"},"来源类型不合法")
            require(not s["live_verified_this_batch"] and digest(canonical(s["data"]))==s["projection_sha256"],"历史投影哈希或实时状态不正确")
            require(s["captured_at"] and s["capture_origin"]["artifact_sha256"],"历史来源定位缺失")
            counters["archived_projections"]+=1
            continue
        p=(ROOT/s["repository_path"]).resolve()
        require(p.is_relative_to(ROOT) and p.is_file(),"来源路径无效")
        blob=p.read_bytes()
        if s["checksum_mode"]=="lf_normalized_text":
            require(digest(blob.replace(b"\r\n",b"\n"))==s["content_sha256"],"来源LF哈希变化")
        else:
            require(s["checksum_mode"]=="raw_bytes" and digest(blob)==s["original_sha256"],"来源原字节哈希变化")
        units=s["units"]
        require(len({u["id"] for u in units})==len(units) and all(u["text"].strip() for u in units),"来源条目为空或重复")
        if p.suffix==".pdf":
            pages=[page.extract_text().strip() for page in PdfReader(p).pages]
            if s["unit_coverage"]=="whole_local_document":
                require([u["text"] for u in units]==pages,"PDF原文页提取不一致")
                require([u["physical_pages"] for u in units]==[[i] for i in range(1,len(pages)+1)],"PDF物理页定位不一致")
            else:
                for u in units:
                    matches=[i for i,t in enumerate(pages,1) if norm(u["text"]) in norm(t)]
                    require(matches and matches==u["physical_pages"],"PDF条目不能定位到原始页")
        elif p.suffix==".docx":
            doc=Document(p)
            lines=[x.text for x in doc.paragraphs if x.text.strip()]
            lines += [" | ".join(c.text for c in row.cells) for t in doc.tables for row in t.rows]
            require(lines==[u["text"] for u in units],"DOCX原文条目变化")
        else:
            text=p.read_text(encoding="utf-8")
            if s.get("unit_coverage")=="selected_sections_not_full_document":
                require(all(norm(u["text"]) in norm(text) for u in units),"选定条目不在原文")
            else:
                require([x.strip() for x in text.splitlines() if x.strip()]==[u["text"] for u in units],"文本原文条目变化")
        counters["repository_sources"]+=1
        counters["source_units"]+=len(units)
    return dict(counters)


def validate(cases,sources,reviews,manifest,lineage):
    require(len(cases)==100 and Counter(c["category"] for c in cases)==COUNTS,"题量分类不一致")
    require(len({c["question"] for c in cases})==100,"问题重复")
    require(set(sources)=={e["source_id"] for c in cases for e in c["evidence"]},"来源登记应与逐题实际引用一致")
    require(manifest["status"]=="candidate_assistant_reviewed_non_independent","候选状态不正确")
    require(manifest["assistant_reviewed_cases"]==manifest["assistant_accepted_candidate_cases"]==100 and manifest["pending_assistant_review"]==0,"助手复核数量错误")
    require(manifest["human_approved_cases"]==manifest["professional_factory_approved_cases"]==manifest["platform_approved_cases"]==0,"不能伪造人工或业务批准")
    require(manifest["executable"] is False and manifest["live_verified"] is False and manifest["model_calls"]==manifest["executed_cases"]==0,"不能伪造执行或实时结果")
    require(Counter(c["test_mode"] for c in cases)==manifest["test_modes"]=={"legacy_manufacturing_candidate":40,"offline_document_candidate":54,"integration_protocol_candidate":6},"测试模式混淆")
    require(len(reviews)==100 and len({r["case_id"] for r in reviews})==100,"复核记录缺失")
    review_map={r["case_id"]:r for r in reviews}
    legacy=[json.loads(x) for x in (LEGACY/"cases.jsonl").read_text(encoding="utf-8").splitlines()]
    old_sources=json.loads((LEGACY/"sources.json").read_text(encoding="utf-8"))["sources"]
    require(all(sources[k]==v for k,v in old_sources.items()),"旧来源投影被改写")
    require(lineage["legacy_cases_sha256"]==digest((LEGACY/"cases.jsonl").read_bytes()),"旧40题指纹变化")
    require(lineage["changes"][0]["old_case_sha256"]==digest(canonical(legacy[35])) and len(lineage["changes"])==1,"修订记录错误")
    field_count=text_count=0
    for i,c in enumerate(cases,1):
        require(c["id"]==f"MFG-GOLD-{i:03}" and c["review_id"]==c["id"],"编号不连续或审核关联错误")
        require(re.search(r"[\u4e00-\u9fff]",c["question"]) is not None,"问题不是中文")
        require(c["input"]["preconditions"],"前置条件缺失")
        p=c["provenance"]
        require(p["split"]=="regression_candidate" and p["independent_holdout"] is False and p["leakage_groups"],"不能伪造独立盲测或丢失分组")
        r=review_map[c["id"]]
        require(r["status"]=="assistant_reviewed_non_independent" and r["decision"]=="accepted_as_candidate","助手复核状态错误")
        require(r["reviewer_type"]=="assistant" and r["human_reviewer"] is None and r["independent"] is False and r["professional_factory_review"] is False,"不能把助手伪装成人工或工厂审核者")
        require(r["reviewed_at"] and r["rationale"].strip(),"逐题审核依据缺失")
        x=c["expected"]
        require(x["reference_answer"] and x["required_facts"] and x["forbidden_claims"],"标准答案或必答边界缺失")
        require(x["answerable"]==(x["behavior"]=="answer"),"可回答状态冲突")
        if c["test_mode"]=="offline_document_candidate":
            require(x["http_status"] is None and x["behavior"]=="answer" and not c["input"]["selection"],"离线资料题不能伪装HTTP接口执行")
        else:
            require(isinstance(x["http_status"],int) and ((x["http_status"]>=400)==(x["behavior"]=="error")),"协议HTTP行为不一致")
        es={e["id"]:e for e in c["evidence"]}
        require(es and len(es)==len(c["evidence"]) and r["evidence_ids"]==list(es),"引用或审核证据列表错误")
        for f in x["required_facts"]+x.get("optional_facts",[]):
            require(f["text"] and f["evidence_ids"] and set(f["evidence_ids"])<=set(es),"必答点缺少有效证据")
        for e in es.values():
            s=sources[e["source_id"]]
            if e["kind"]=="text":
                require(e["quote"]==next(u["text"] for u in s["units"] if u["id"]==e["unit_id"]),"引用摘录与来源不一致")
                text_count+=1
            elif e["kind"]=="full_text":
                require(s.get("unit_coverage")!="selected_sections_not_full_document" and e["unit_ids"]==[u["id"] for u in s["units"]],"不完整条目冒充全文")
            else:
                require(e["kind"]=="field" and s["kind"] in {"archived_business_snapshot","archived_mapping_snapshot"},"合成文档不能冒充API字段")
                v=value_at(s["data"],e["field_path"])
                require(v==e["value"] and type(v) is type(e["value"]),"业务字段值或类型不符")
                field_count+=1
        context=c["input"]["context_source_id"]
        if context:
            d=sources[context]["data"]; sel=c["input"]["selection"]
            require(sel["work_order_id"]==d["work_order_id"],"工单选择不一致")
            for key in ["operation_id","job_card_id"]:
                if key in sel: require(sel[key]==d["execution"][key],"工序或作业卡选择不一致")
            for parent,key in [("batch","batch_id"),("step","step_id")]:
                if key in sel: require(sel[key]==d[parent][key] and d[parent]["work_order_id"]==d["work_order_id"],"父子关系不一致")
        if c["category"]=="conversation": require(len(c["input"]["conversation_steps"])>=2,"会话多步协议缺失")
        if c["test_mode"]=="integration_protocol_candidate":
            require(c["input"]["selection_identifiers"]=="offline_aliases_require_isolated_resolution","合成别名不能直接作为真实ID")
            require("真实ID" in "".join(c["input"]["preconditions"]),"缺少真实ID解析前置")
            require(c["input"]["conversation_steps"][-1]["expected_http_status"]==x["http_status"]==409,"协议末步错误")
        if i<=40:
            original=deepcopy(legacy[i-1]); original.pop("review")
            original["test_mode"]="legacy_manufacturing_candidate"; original["review_id"]=c["id"]
            original["expected"]["optional_facts"]=[]
            if i==36:
                optional=original["expected"]["required_facts"].pop()
                original["expected"]["optional_facts"]=[dict(optional,id="O01",reason="题目只问执行状态能否推出检验签署完成，不把未问数量设为必答。")]
            require(c==original,"旧题继承超出已登记修订范围")
    return dict(text_evidence=text_count,field_evidence=field_count)


def damage_probes(cases,sources,reviews,manifest,lineage):
    def restore_unasked_requirement(c,s,r,m):
        item=c[35]["expected"]["optional_facts"].pop()
        c[35]["expected"]["required_facts"].append(dict(id="F04",text=item["text"],evidence_ids=item["evidence_ids"]))
    mutations={
        "伪造人工审核":lambda c,s,r,m:r[0].update(reviewer_type="human",human_reviewer="虚构人员"),
        "伪造独立盲测":lambda c,s,r,m:c[40]["provenance"].update(independent_holdout=True),
        "合成文档冒充API字段":lambda c,s,r,m:c[40]["evidence"][0].update(kind="field",field_path="actual_started_at",value="假值"),
        "业务字段类型篡改":lambda c,s,r,m:c[0]["evidence"][0].update(value=1),
        "必答点断开证据":lambda c,s,r,m:c[40]["expected"]["required_facts"][0].update(evidence_ids=[]),
        "摘录与原文不符":lambda c,s,r,m:c[40]["evidence"][0].update(quote="原文不存在的放行结论"),
        "离线题伪造HTTP结果":lambda c,s,r,m:c[40]["expected"].update(http_status=200),
        "会话协议被删除":lambda c,s,r,m:c[56]["input"].update(conversation_steps=[]),
        "未问细节恢复为必答":restore_unasked_requirement,
    }
    results={}
    for name,mutate in mutations.items():
        c,s,r,m=deepcopy(cases),deepcopy(sources),deepcopy(reviews),deepcopy(manifest)
        mutate(c,s,r,m)
        try: validate(c,s,r,m,lineage)
        except (ValueError,KeyError,StopIteration): results[name]="rejected"
        else: raise ValueError("损坏探针未拒绝："+name)
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probes",action="store_true")
    parser.add_argument("--report",type=Path)
    args=parser.parse_args()
    network_events=[]
    def no_network(event,values):
        if event in {"socket.connect","socket.getaddrinfo"}:
            network_events.append(event)
            raise RuntimeError("零模型离线检查禁止网络访问")
    sys.addaudithook(no_network)
    manifest=json.loads((DATA/"manifest.json").read_text(encoding="utf-8"))
    for name,expected in manifest["files_sha256"].items():
        require(digest((DATA/name).read_bytes())==expected,"冻结文件哈希变化："+name)
    for name,expected in manifest.get("implementation_sha256",{}).items():
        path=(ROOT/name).resolve()
        require(path.is_relative_to(ROOT) and digest(path.read_bytes())==expected,"构建或检查源码哈希变化："+name)
    cases=[json.loads(x) for x in (DATA/"cases.jsonl").read_text(encoding="utf-8").splitlines()]
    reviews=[json.loads(x) for x in (DATA/"reviews.jsonl").read_text(encoding="utf-8").splitlines()]
    bundle=json.loads((DATA/"sources.json").read_text(encoding="utf-8")); sources=bundle["sources"]
    require(bundle["current_live_verification"] is False,"不能伪造实时核验")
    lineage=json.loads((DATA/"lineage.json").read_text(encoding="utf-8"))
    result=dict(status="verified_assistant_reviewed_candidate",cases=100,categories=COUNTS,sources=len(sources),
                assistant_reviewed=100,human_approved=0,professional_factory_approved=0,
                executed_cases=0,model_calls=0,manifest_sha256=digest((DATA/"manifest.json").read_bytes()))
    result.update(verify_sources(sources))
    result.update(validate(cases,sources,reviews,manifest,lineage))
    require((DATA/"REVIEW.md").read_text(encoding="utf-8")==render_review(cases,sources,reviews),"逐题视图与原数据不一致")
    if args.probes: result["damage_probes"]=damage_probes(cases,sources,reviews,manifest,lineage)
    require(not network_events,"检查发生网络访问")
    result["network_connect_attempts"]=0
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__": main()
