"""Build a local Chinese corpus. No DB, embeddings, application or provider calls.

Run this authoring command with the bundled artifact Python (reportlab).
Existing source files are referenced, never rewritten. Public web originals are
not republished: four new files are short, attributed Chinese summaries.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.manufacturing_zh_expansion_data import PRODUCTS, PUBLIC_SUMMARIES

CORPUS = ROOT / "sample_docs/manufacturing_zh_expansion_v1"
EDITION = "manufacturing-zh-expansion-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def rel(path):
    return Path(path).relative_to(ROOT).as_posix()


def units(*sections):
    return [{"unit_id": f"u{i:02}", "heading": heading, "text": text}
            for i, (heading, text) in enumerate(sections, 1)]


def render_pdf(path, title, introduction, sections, font):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from xml.sax.saxutils import escape

    styles = {
        "title": ParagraphStyle("title", fontName=font, fontSize=17, leading=25, spaceAfter=14, textColor=colors.black),
        "heading": ParagraphStyle("heading", fontName=font, fontSize=12, leading=18, spaceBefore=9, spaceAfter=5, keepWithNext=True),
        "body": ParagraphStyle("body", fontName=font, fontSize=10.5, leading=17, spaceAfter=7, alignment=TA_LEFT, wordWrap="CJK"),
    }
    flow = [Paragraph(escape(title), styles["title"]), Paragraph(escape(introduction), styles["body"])]
    for section in sections:
        flow += [Paragraph(escape(section["heading"]), styles["heading"]), Paragraph(escape(section["text"]), styles["body"])]
    flow.append(Spacer(1, 6))

    def footer(canvas, doc):
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(42, 24, "制造业演示资料  工艺参数与业务记录均为合成  待人工审核")
        canvas.drawRightString(A4[0] - 42, 24, str(doc.page))

    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=38, bottomMargin=42,
                            leftMargin=42, rightMargin=42, title=title, author="制造业平台演示资料")
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)


def write_document(sid, title, sections, *, kind, role, fmt="md", product=None, op=None, revision="1.0", **extra):
    group = "合成业务" if kind == "synthetic_business" else "公开参考导读"
    path = CORPUS / "documents" / group / f"{sid}_{title}.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    intro = ("本文件由助手编写，属于中文合成演示草案，未经工艺或质量人员批准。产品、人员角色、参数、日期和记录均为虚构；不能据此操作设备、认定真实生产状态或绕过平台审核。"
             if kind == "synthetic_business" else
             "本文件是基于中文官方公开页面编写的简短导读，不是原文、官方译本或企业受控规程。事实摘要与平台应用边界分别标注，待人工核对。")
    if fmt == "pdf":
        render_pdf(path, title, intro, sections, "CorpusChinese")
    else:
        lines = (["# " + title, "", intro, ""] if fmt == "md" else [title, "", intro, ""])
        for item in sections:
            lines += [("## " if fmt == "md" else "") + item["heading"], "", item["text"], ""]
        path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    record = dict(id=sid, title=title, repository_path=rel(path), sha256=sha(path),
                  bytes=path.stat().st_size, format=fmt, language="zh-CN", source_kind=kind,
                  document_role=role, source_family=product["family"] if product else extra.get("source_family"),
                  origin="new", scan=False, review_status="pending_human_review", approved=False,
                  indexed=False, live_verified_this_batch=False,
                  evidence=[dict(item, scope="synthetic_rule_or_record" if product else
                                ("author_application_boundary" if item["heading"] == "应用边界" else "source_summary")) for item in sections])
    if product:
        record.update(product_models=[product["model"]], operation_codes=[op["code"]] if op else [o["code"] for o in product["ops"]],
                      work_order_ids=[product["work_order"]] if role in {"record", "notice", "handover"} else [],
                      scope_kind="work_order" if role in {"record", "notice", "handover"} else "product_operation",
                      document_code=f"ZH-{role.upper()}-{product['family']}-{op['code'] if op else 'ALL'}",
                      business_revision=revision,
                      effective_from="2026-01-01" if revision == "1.0" and role == "instruction" else product["switch"],
                      effective_to=str(date.fromisoformat(product["switch"]) - timedelta(days=1)) if revision == "1.0" and role == "instruction" else None,
                      interval_convention="inclusive_dates", applicability_review_state="draft",
                      rights_status="assistant_authored_synthetic_demo", factory_approved=False)
    record.update(extra)
    return record


def synthetic_documents():
    records = []
    for p in PRODUCTS:
        for index, op in enumerate(p["ops"]):
            for rev, text in [("1.0", op["old"]), ("2.0", op["new"])]:
                sid = f"ZH-{p['family']}-{index * 2 + (1 if rev == '1.0' else 2):02}"
                sections = units(
                    ("适用范围", f"本指导书仅适用于演示产品{p['model']}的{op['code']}{op['name']}，不得因产品名称相似用于其他系列。文件业务版本为{rev}，全部条款为合成规则。"),
                    ("版本区间", f"本版从{'2026-01-01' if rev == '1.0' else p['switch']}起适用，{'至' + str(date.fromisoformat(p['switch']) - timedelta(days=1)) + '结束，起止日期均包含当日' if rev == '1.0' else '结束日期尚未设定'}。产品、工序和时间窗口均匹配且完成业务适用性审核后才能用于平台选版。"),
                    ("作业要求", text),
                    ("变更后复核", op["trigger"] if rev == "2.0" else "异常发现后保持物料与记录可追溯，提请质量人员确认后再处理。"),
                    ("工具与记录", f"使用{op['tool']}核对并保存本工单记录。引用本指导书只能证明作业要求，不能证明现场已经执行，也不能替代检验签署。"),
                )
                records.append(write_document(sid, f"{p['model']}_{op['code']}{op['name']}_{'历史版' if rev=='1.0' else '现行演示版'}", sections,
                               kind="synthetic_business", role="instruction", product=p, op=op, revision=rev))
            sid = f"ZH-{p['family']}-{5 + index:02}"
            sections = units(
                ("适用对象", f"本规范适用于{p['model']}的{op['code']}{op['name']}。检验限值和样例数据均为演示假设，不能用于真实设备或产品验收。"),
                ("检查要求", op["check"]), ("取样与触发", op["sample"]),
                ("演示记录辨析", op["measurement"]),
                ("签署与处置", "操作员记录、质量复核结论和放行签署分别保存。缺失或超限项保持待复核或待处置；交接签名、设备启动时间和完成数量均不能代填质量结论。"),
                ("来源追溯", f"登记产品{p['model']}、工单{p['work_order']}、工序{op['code']}、物料批号{p['lot']}、文件版次及原始记录编号，保留更正前的历史记录。"),
            )
            records.append(write_document(sid, f"{p['model']}_{op['code']}检验要求与记录辨析", sections,
                           kind="synthetic_business", role="inspection", fmt="pdf", product=p, op=op))
        instructions = [f"ZH-{p['family']}-{i:02}" for i in (2, 4)]
        sections = units(
            ("变更对象", f"演示变更单DEMO-ECN-{p['family']}-02将{p['model']}的两份作业指导书更新至2.0版，切换日期为{p['switch']}，仅适用于本系列明确列出的两道工序。"),
            ("变更内容", "；".join(f"{op['code']}新增要求：{op['new']}" for op in p["ops"])),
            ("切换条件", "工单时间只能作为选版条件之一；仍须核对产品、工序、有效区间和已审核适用性。通知已编写不表示平台审核已批准；本资料包所有映射仍是草案。"),
            ("已开工工单", "未取得明确书面切换指示的既有工单，不因新通知存在就自动切换指导书；多个书面指示冲突时应保留冲突并请求审核者确认。"),
            ("关联文件", "对应的新指导书来源编号为" + "、".join(instructions) + "，引用变更单不能替代对所选指导书原文及版本的核对。"),
        )
        records.append(write_document(f"ZH-{p['family']}-07", f"{p['model']}工艺变更通知", sections, kind="synthetic_business", role="change_notice", product=p))
        ops = p["ops"]
        sections = units(
            ("记录身份", f"本台账为合成离线记录，工单{p['work_order']}，产品{p['model']}，计划数量{p['quantity']}件，物料{p['material']}，物料批号{p['lot']}；不来自ERPNext或OpenMES实时API。"),
            ("工单时间", f"计划开工为{p['date']}08:00:00+08:00，工单级实际开工未登记。计划时间不得当作实际时间，任何子级记录也不得补填工单级实际开工。"),
            ("工序与作业卡", f"ERPNext离线夹具设计：工序子行DEMO-ROW-{p['family']}-1属于{ops[0]['code']}，工序实际开工为{p['date']}08:12:00+08:00；作业卡DEMO-JC-{p['family']}-1实际开工为08:17:00+08:00，日期同本工单演示日期。第二工序子行DEMO-ROW-{p['family']}-2和作业卡DEMO-JC-{p['family']}-2均未登记实际开工。卡与工序分别取值，不能互补。"),
            ("批次与步骤", f"OpenMES离线夹具设计：批次DEMO-BATCH-{p['family']}-1实际开工为{p['date']}09:00:00+08:00，步骤DEMO-STEP-{p['family']}-1实际开工为09:06:00+08:00，日期同本工单演示日期；同批次步骤DEMO-STEP-{p['family']}-2未登记实际开工。另有同工单批次DEMO-BATCH-{p['family']}-2，其步骤DEMO-STEP-{p['family']}-3与第一批次不构成父子关系。"),
            ("记录状态", f"{ops[0]['code']}：{ops[0]['observed']}，但{ops[0]['unknown']}；{ops[1]['code']}：{ops[1]['observed']}，但{ops[1]['unknown']}。这些状态为样例事实，不表示真实系统当前状态。"),
            ("指导书映射草案", f"步骤DEMO-STEP-{p['family']}-1拟分配来源{instructions[0]}的2.0版，步骤DEMO-STEP-{p['family']}-2拟分配来源{instructions[1]}的2.0版。两个分配均待人工审核，尚未登记到平台，不能称为已批准映射或用于线上会话绑定。"),
        )
        records.append(write_document(f"ZH-{p['family']}-08", f"{p['model']}工单执行记录与离线夹具设计", sections, kind="synthetic_business", role="record", fmt="txt", product=p))
        sections = units(
            ("例外身份", f"演示例外单DEMO-EX-{p['family']}-01只针对{p['work_order']}，不适用于同型号其他工单，也不是平台已审核授权。"),
            ("例外要求", p["exception"]),
            ("审核边界", "本通知尚未审核，保留草案状态；不能把作者编写时间当作批准时间。若后续审核版本与当前快照不同，必须重新读取并冻结新版本，保留旧版本。"),
            ("引用用途", "本通知可说明假设场景的例外条件，不能证明例外已经执行、质量已经签署或平台已经授予访问权限。"),
        )
        records.append(write_document(f"ZH-{p['family']}-09", f"{p['model']}工单例外通知草案", sections, kind="synthetic_business", role="notice", product=p))
        sections = units(
            ("交接身份", f"合成交接记录DEMO-HO-{p['family']}-01，工单{p['work_order']}，日期{p['date']}，对象为本系列两道工序，全部角色与事实均为演示设定。"),
            ("交接内容", p["handover"]),
            ("未完成项", "；".join(f"{op['code']}：{op['unknown']}" for op in p["ops"]) + "。"),
            ("追溯与更正", "接班后新增的检查、处置和签署应另建记录，注明记录号和时间；不得覆盖本交接原件，也不得把后来的合格结论回填为交接时已知事实。"),
        )
        records.append(write_document(f"ZH-{p['family']}-10", f"{p['model']}班次交接与未完成项", sections, kind="synthetic_business", role="handover", product=p))
    return sorted(records, key=lambda r: r["id"])


def references():
    records = []
    old = json.loads((ROOT / "sample_docs/manufacturing_workorder_v1/manifest.json").read_text(encoding="utf-8-sig"))
    for item in old["documents"]:
        path = ROOT / item["file"]
        records.append(dict(id="BASE-" + item["id"], title=path.stem, repository_path=item["file"], sha256=sha(path),
                            bytes=path.stat().st_size, format=path.suffix[1:], language="zh-CN", source_kind="synthetic_business",
                            source_family="legacy-HC-A17", origin="existing_reference", scan=False,
                            review_status="pending_human_review", approved=False, indexed=False,
                            live_verified_this_batch=False, legacy_manifest="sample_docs/manufacturing_workorder_v1/manifest.json"))
    public_root = ROOT / "sample_docs/manufacturing_zh_v3"
    old = json.loads((public_root / "manifest.json").read_text(encoding="utf-8-sig"))
    for i, item in enumerate(old["documents"][:8], 1):
        path = public_root / item["file"]
        records.append(dict(id=f"ZH-PUB-{i:02}", title=item["title"], repository_path=rel(path), sha256=sha(path),
                            bytes=path.stat().st_size, format=path.suffix[1:], language="zh-CN", source_kind=item["source_kind"],
                            source_family=item["source_family"], origin="existing_reference", scan=False,
                            review_status="pending_human_review", approved=False, indexed=False, live_verified_this_batch=False,
                            source_url=item["source_url"], landing_url=item["landing_url"], publisher=item["publisher"],
                            rights_status=item["rights_status"], use_note=item["use_note"],
                            validity_status=item["validity_status"], legacy_manifest=rel(public_root / "manifest.json")))
    return records


def coverage(records):
    """Sixty evidence topics, NOT executable questions, answers, or gold approval."""
    by_id = {r["id"]: r for r in records}
    rows = []

    def add(category, text, sid, unit, dependency):
        r = by_id[sid]
        anchor = next(x for x in r["evidence"] if x["unit_id"] == unit)
        rows.append(dict(id=f"TOPIC-{len(rows)+1:03}", category=category, intent=text, source_id=sid, unit_id=unit,
                         evidence_quote=anchor["text"], status="evidence_topic_only", human_review="pending",
                         runtime_prerequisite=dependency, source_family=r["source_family"], independent_holdout=False))

    for p in PRODUCTS:
        f = p["family"]
        for index, op in enumerate(p["ops"]):
            add("guidance", f"{p['model']} {op['code']}新版完整要求及缺项处理", f"ZH-{f}-{2+index*2:02}", "u03", "人工核对语义与适用性")
            add("guidance", f"{p['model']} {op['code']}检验条件与样例记录判断", f"ZH-{f}-{5+index:02}", "u04", "结合该规范u02完整检查要求复核，不能只读样例")
        for intent, unit in [("工单级实际开工未知与计划时间边界", "u02"), ("所选工序与作业卡分别取值", "u03"),
                             ("同工单第二作业卡空值保持未知", "u03"), ("执行记录缺项不能补成质量完成", "u05")]:
            add("erpnext", f"{p['model']} {intent}", f"ZH-{f}-08", unit, "后续独立ERP测试环境建夹具、真实GET复读及冻结；当前仅合成离线设计")
        for intent, unit in [("批次与步骤各自实际开工", "u04"), ("步骤未开工不取批次时间", "u04"),
                             ("跨批次步骤选择拒绝", "u04"), ("步骤指导书映射草案不等于已审核", "u06")]:
            add("openmes", f"{p['model']} {intent}", f"ZH-{f}-08", unit, "后续独立MES测试环境建夹具、核对父子关系及审核映射；当前仅离线设计")
        for intent, sid, unit in [("例外范围不能扩至同型号其他工单", f"ZH-{f}-09", "u01"),
                                  ("草案不能作为已批准通知", f"ZH-{f}-09", "u03"),
                                  ("交接签名不能替代质量放行", f"ZH-{f}-10", "u02"),
                                  ("历史记录不能回填为当前已知结论", f"ZH-{f}-10", "u04")]:
            add("safety", f"{p['model']} {intent}", sid, unit, "后续结合现有平台权限、来源及只读契约设计前置条件；文档不是权限证据")
        for intent in ["切换所选步骤应新建会话", "映射修订变化后旧会话失效"]:
            add("conversation", f"{p['model']} {intent}", f"ZH-{f}-08", "u06", "后续在隔离环境实际审核映射并构造多步会话；草案不是线上绑定")
    for sid, unit, intent in [("ZH-PUB-09", "u01", "数字化转型需求方供给方与主管部门定位"),
                               ("ZH-PUB-09", "u02", "先局部后扩展的实施路径"),
                               ("ZH-PUB-10", "u01", "典型场景指引的覆盖范围"),
                               ("ZH-PUB-10", "u02", "工艺知识与生产进度场景区别"),
                               ("ZH-PUB-11", "u01", "版次年份与发布日期区分"),
                               ("ZH-PUB-12", "u01", "质量基础设施涉及的能力")]:
        add("guidance", intent, sid, unit, "对照中文官方原文复核摘要；不能以导读代替工厂技术规程")
    return dict(status="coverage_plan_not_gold_dataset", new_topic_count=len(rows),
                legacy_question_count=40, target_candidate_count=100,
                approved_new_questions=0, executable=False, rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/simhei.ttf"))
    args = parser.parse_args()
    if (CORPUS / "manifest.json").exists():
        raise SystemExit("目标版本已经存在；保留已生成版本，另建新版，不覆盖。")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont("CorpusChinese", str(args.font)))
    records = references() + synthetic_documents()
    for source in PUBLIC_SUMMARIES:
        sections = units(("事实摘要一", source["paragraphs"][0]), ("事实摘要二", source["paragraphs"][1]), ("应用边界", source["paragraphs"][2]))
        sections.append(dict(unit_id="u04", heading="中文来源", text=f"原文：{source['source_title']}。发布机构：{source['publisher']}。网页发布日期：{source['published']}。来源链接：{source['url']}。"))
        records.append(write_document(source["id"], source["title"], sections, kind="public_summary", role="reference",
                       source_family=source["family"], source_url=source["url"], publisher=source["publisher"],
                       published_date=source["published"], rights_status="new_short_summary_original_not_republished",
                       validity_status="locked_reference_latest_applicability_not_certified"))
    records.sort(key=lambda r: r["id"])
    dump(CORPUS / "sources.json", dict(edition=EDITION, documents=records))
    dump(CORPUS / "coverage.json", coverage(records))
    manifest = dict(edition=EDITION, status="offline_corpus_candidate_pending_human_review", language="zh-CN",
                    total_documents=len(records), new_documents=sum(r["origin"] == "new" for r in records),
                    referenced_documents=sum(r["origin"] == "existing_reference" for r in records),
                    public_documents=12, new_synthetic_documents=30, scan_documents=0,
                    formats=dict(Counter(r["format"] for r in records)), approved_documents=0,
                    indexed=False, external_model_calls=0, runtime_modified=False,
                    files_sha256={name: sha(CORPUS / name) for name in ["sources.json", "coverage.json"]})
    dump(CORPUS / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
