"""Reviewed synthetic manufacturing rules and independent regression annotations.

The company, products, limits and events are fictional. No model or network calls.
"""

EDITION = "manufacturing-lite-v1"
AS_OF = "2026-10-28"
GROUPS = ("公共参考", "生产工艺", "质量检验")


def document(number, title, suffix, group, code, sections, *, version="1.0",
             effective="2026-01-01", until=None, status="现行", scope="衡川精密已授权人员",
             features=(), scan=False):
    return dict(id=f"MFG-{number:02}", title=title, format=suffix, group=group,
                file=f"documents/{group}/{number:02}_{title}.{suffix}", document_code=code,
                business_version=version, effective_from=effective, effective_to=until,
                business_status=status, applicability=scope, sections=sections,
                features=list(features), scan=scan, source_kind="synthetic_business")


DOCUMENTS = [
    document(1, "量具使用与测量准备手册", "pdf", "公共参考", "HC-MEA-001", [
        ("heading", "1 测量准备"),
        ("paragraph", "测量前清洁量具测量面与工件接触面，确认量具编号、校准有效期和零位。"
         "发现零位异常、读数跳变或校准过期时，不得将该量具用于本次检验，应交质量人员确认。"),
        ("heading", "2 量具能力登记"),
        ("table", [["量具型号", "测量范围", "分辨力", "最大允许误差", "使用限定"],
                   ["CAL-150", "0至150 mm", "0.01 mm", "±0.02 mm", "外尺寸及深度，使用前确认零位"],
                   ["MIC-025", "0至25 mm", "0.001 mm", "±0.003 mm", "外尺寸，使用测力装置"]]),
        ("paragraph", "量具分辨力不等于最大允许误差；型号能力登记不替代具体量具的校准记录。"
         "不得把CAL-150的分辨力填入MIC-025的最大允许误差栏。"),
        ("heading", "3 记录要求"),
        ("paragraph", "记录原始读数和原始单位，保留零值、负号和小数位。不得将未实际测量的值填为零。"),
    ], features=("型号参数表", "单位与脚注")),
    document(2, "首件确认与工序交接流程", "pdf", "生产工艺", "HC-FLOW-001", [
        ("heading", "1 首件确认"),
        ("paragraph", "操作员按适用指导书完成首件检查，检验员核对全部必检项目。"
         "报工完成只说明已登记生产数量，不证明首件确认或质量签署已经完成。"),
        ("heading", "2 工序交接的必要条件"),
        ("paragraph", "恢复后续作业前必须同时满足以下条件："),
        ("paragraph", "1. 适用指导书要求的首件检查全部合格。\n2. 检验记录写明工单、批次和量具编号。"),
        ("page_break", ""),
        ("paragraph", "3. 质量人员完成签署，生产班长确认记录齐全。\n4. 存在未关闭的暂停要求时，须按书面批准的恢复条件执行。"),
        ("paragraph", "以上条件共同适用。仅有口头同意、报工记录或计划签署时间，均不能证明这些条件已经满足。"),
    ], features=("跨页条件", "重复页眉")),
    document(3, "检验项目与量具对照表", "pdf", "质量检验", "HC-QC-MAP", [
        ("heading", "1 对照表适用说明"),
        ("paragraph", "本表登记检查项目与记录要求，具体产品公差以对应检验规范为准。所有备注均属于该行要求。"),
        ("table", [["项目编号", "检查项目", "指定量具", "记录单位", "必要备注"]] + [
            [f"IP-{i:02}", f"辅助尺寸检查点{i:02}", "CAL-150" if i % 2 else "MIC-025", "mm",
             "每个检查点独立记录读数、量具编号和检查人，不允许使用上一检查点的数值代填"]
            for i in range(1, 25)]),
        ("paragraph", "IP-01使用CAL-150，IP-02使用MIC-025。检查点编号、量具型号和单位必须在同一记录中对应。"),
    ], features=("长表", "重复表头", "参数对应")),
    document(4, "定位治具更换通知扫描件", "pdf", "生产工艺", "HC-NOT-026", [
        ("heading", "1 通知内容"),
        ("paragraph", "工单WO-017，产品HC-A17，工序OP-20。2026年10月26日14:10更换定位治具。"),
        ("paragraph", "更换后按2.0版指导书重新进行首件确认。连续3件全部合格并由质量人员签署后，方可恢复后续作业。"),
        ("paragraph", "本通知记录的是更换事件及后续要求，不是复检完成记录。"),
    ], effective="2026-10-26", features=("OCR必需", "图片型PDF"), scan=True),
    document(6, "HC-A17_OP20作业指导书历史版", "docx", "生产工艺", "HC-WI-A17-020", [
        ("heading", "1 适用作业"),
        ("paragraph", "适用于产品HC-A17的OP-20工序。2026年9月30日及以前已开工的工单沿用本版；"
         "如有针对该工单的书面变更，按书面变更执行。"),
        ("heading", "2 首件要求"),
        ("paragraph", "每次开工连续检查2件，全部项目合格后由质量人员签署。"
         "更换定位治具后必须重新进行首件确认，不得沿用更换前的首件结论。"),
        ("heading", "3 作业记录"),
        ("paragraph", "记录工单号、批次号、指导书版本和签署人。本版归档后仍用于其有效期间的历史查询。"),
    ], version="1.0", until="2026-09-30", status="历史归档", scope="HC-A17的OP-20工序"),
    document(7, "HC-A17_OP20作业指导书现行版", "docx", "生产工艺", "HC-WI-A17-020", [
        ("heading", "1 适用作业"),
        ("paragraph", "适用于产品HC-A17的OP-20工序，自2026年10月1日起新开工工单使用2.0版。"
         "此前已开工工单的沿用与例外按ECN-2026-018执行。"),
        ("heading", "2 首件确认条件"),
        ("paragraph", "出现下列任一情形时必须重新进行首件确认："),
        ("list", "新工单开工。"),
        ("list", "更换定位治具。"),
        ("list", "连续停机超过4小时后重新开工。"),
        ("paragraph", "首件确认须连续检查3件，所有必检项目全部合格，并由质量人员签署后才能恢复后续作业。"
         "不允许只检1件，也不能用操作员口头确认替代质量签署。"),
        ("heading", "3 检验与交接"),
        ("paragraph", "必检项目按HC-QC-A17-001执行，交接按HC-FLOW-001执行。"
         "本指导书没有给出主轴转速、切削速度或进给量，操作前必须取得适用的批准工艺文件。"),
    ], version="2.0", effective="2026-10-01", scope="HC-A17的OP-20工序", features=("条件列表", "必要签署")),
    document(8, "HC-A17首件检验规范", "docx", "质量检验", "HC-QC-A17-001", [
        ("heading", "1 检验范围"),
        ("paragraph", "适用于HC-A17的OP-20首件检验，须按指导书要求逐件完成全部必检项目。"),
        ("table", [["项目", "标准", "工具", "条件或备注"],
                   ["外径", "19.95至20.05 mm，包含上下限", "MIC-025", "记录每件原始读数"],
                   ["孔深", "12.00至12.10 mm，包含上下限", "CAL-150", "清洁孔内切屑后测量"],
                   ["外观", "无可见毛刺和碰伤", "目视", "全部首件逐件检查"],
                   ["记录与签署", "工单、批次、量具编号及质量人员签署齐全", "检验记录", "缺项时不得确认首件通过"]]),
        ("heading", "2 原始记录"),
        ("paragraph", "外径和孔深是两个独立检查项目，不得混用上下限。零读数也必须记录。"
         "外观合格不能替代尺寸检查，尺寸合格不能替代质量签署。"),
    ], effective="2026-10-01", scope="HC-A17的OP-20首件检验", features=("表格条件", "公差与单位")),
    document(9, "工艺变更通知ECN-2026-018", "docx", "生产工艺", "ECN-2026-018", [
        ("heading", "1 变更对象与时间"),
        ("paragraph", "仅适用于HC-A17的OP-20工序。2026年10月1日起新开工工单由指导书1.0版切换为2.0版。"
         "判断依据为工单实际开工日期，不按报工日期或文档上传日期选择版本。"),
        ("heading", "2 在制工单与例外"),
        ("paragraph", "2026年9月30日及以前已经开工的工单继续沿用1.0版，即使在10月报工也不自动切换。"
         "仅当工艺负责人针对具体工单签发书面切换通知时，才按通知切换。没有通知不能推定已切换。"),
        ("heading", "3 变更内容"),
        ("paragraph", "首件数量由连续2件改为连续3件；2.0版增加连续停机超过4小时后重新首件确认。"
         "两版均要求更换定位治具后重新首件确认，并由质量人员签署。"),
        ("paragraph", "HC-A18不属于本次变更范围，应查询其独立的指导书，不能套用HC-A17要求。"),
    ], effective="2026-10-01", scope="HC-A17的OP-20工序"),
    document(10, "产品工序与文件路由", "md", "公共参考", "HC-ROUTE-001", [
        ("heading", "1 产品与资料位置"),
        ("table", [["产品", "工序", "指导书编号", "资料归口"],
                   ["HC-A17", "OP-20", "HC-WI-A17-020", "生产工艺知识库"],
                   ["HC-A18", "OP-10", "HC-WI-A18-010", "须由对应工艺负责人提供"]]),
        ("paragraph", "路由登记只用于定位文件，不证明用户已获访问权限，也不替代正文中的作业要求。"
         "HC-A18的正式指导书正文未收录；本包不包含实时库存、采购或MES接口数据。"),
        ("heading", "2 提问提供的信息"),
        ("paragraph", "查询作业要求时写明产品型号、工序和实际业务日期；需要依据交接记录时提供工单号。"),
    ]),
    document(11, "工艺常见问题", "md", "生产工艺", "HC-FAQ-001", [
        ("heading", "1 作业与正式文件"),
        ("paragraph", "问：FAQ和正式指导书写法不一致时怎么办？\n答：先核对适用产品、工序和时间。"
         "同一场景内以已批准的正式文件为依据；仍有冲突时提交工艺负责人核实，不自行选择更宽松要求。"),
        ("heading", "2 记录示例"),
        ("paragraph", "下列示例仅展示记录字段，不是对任意工单的执行证明。"),
        ("code", '{"work_order": "WO-EXAMPLE", "instruction_revision": "待核实", "note": "#不是章节|也不是表格"}'),
        ("paragraph", "问：只有计划检验时间能否证明已完成？\n答：不能，须查实际检验与签署记录。"),
    ], features=("FAQ", "代码围栏", "标题层级")),
    document(12, "检验记录填写与追溯规则", "md", "质量检验", "HC-QC-REC", [
        ("heading", "1 必填记录"),
        ("paragraph", "首件检验记录必须填写工单号、批次号、指导书版本、量具编号、检验人、检验时间、逐项结果和质量签署。"),
        ("heading", "2 借用量具"),
        ("paragraph", "借用量具必须同时满足以下条件："),
        ("list", "核对校准在有效期内，点检及零位确认正常。"),
        ("list", "登记借出方、借入方、量具编号和归还时间。"),
        ("paragraph", "归还前清洁并复核状态；异常时隔离，不得只在交接记录中写正常后直接归还。"),
        ("table", [["字段", "允许值示例"], ["记录标记", r"原始读数\|复核读数"], ["空值说明", "未检与零读数分开登记"]]),
    ], features=("条件列表", "转义竖线")),
    document(13, "历史文件查询与变更说明", "md", "公共参考", "HC-DOC-001", [
        ("heading", "1 历史查询"),
        ("paragraph", "已归档不等于不能回答历史问题。查询当时要求应使用当时适用的版本，并保留原文中的条件和例外。"),
        ("heading", "2 业务版本与上传版本"),
        ("paragraph", "上传时间、入库状态、索引版本和检索排名均不能单独证明工艺文件现行。"
         "应结合生效时间、适用产品、工序及明确替代关系判断。"),
        ("paragraph", "涉及具体工单的例外切换须有对应书面通知；没有收录通知不等于该通知一定不存在，也不允许猜测其内容。"),
    ]),
    document(14, "工单WO-017班次交接记录", "txt", "生产工艺", "HC-HO-017", [
        ("heading", "1 工单背景"),
        ("paragraph", "工单号：WO-017\n产品型号：HC-A17\n工序：OP-20\n实际开工时间：2026年10月26日08:10\n批次号：B-1026-017"),
        ("heading", "2 当班记录"),
        ("paragraph", "2026年10月26日14:10更换定位治具。14:20交接时首件复检尚未完成，质量人员尚未签署。"
         "现有记录未载明后续实际完成时间。"),
        ("list", "1. 将治具更换事件移交下一班，按适用指导书重新确认。"),
        ("list", "2. 保留旧记录，不将计划完成时间填为实际完成时间。"),
    ], effective="2026-10-26", features=("交接记录", "实际与计划区分")),
    document(15, "工单WO-018班次交接记录", "txt", "生产工艺", "HC-HO-018", [
        ("heading", "1 工单背景"),
        ("paragraph", "工单号：WO-018\n产品型号：HC-A17\n工序：OP-20\n实际开工日期：2026年9月28日\n报工日期：2026年10月2日"),
        ("heading", "2 版本交接"),
        ("paragraph", "截至2026年10月2日交接，未收到针对WO-018的书面版本切换通知。"
         "交接登记引用指导书1.0版。报工月份变化不构成工艺版本自动切换依据。"),
    ], effective="2026-10-02", features=("历史业务时间",)),
    document(16, "首件检验记录B-1026-017", "txt", "质量检验", "HC-QC-017", [
        ("heading", "1 更换治具前首件"),
        ("paragraph", "工单WO-017，批次B-1026-017，指导书2.0版。"
         "2026年10月26日08:40记录的外径读数依次为19.98 mm、20.02 mm、20.01 mm；量具编号MIC-017。"
         "三件孔深均记录为12.05 mm，使用CAL-017；三件外观均记录无可见毛刺和碰伤。"),
        ("heading", "2 记录边界"),
        ("paragraph", "本记录是14:10治具更换前的首件记录，不能证明更换后的首件复检已完成。"
         "本记录不包含后续复检的实际完成时间。"),
    ], effective="2026-10-26"),
    document(17, "量具点检与隔离交接记录", "txt", "质量检验", "HC-GAGE-017", [
        ("heading", "1 记录时点"),
        ("paragraph", "记录截至2026年10月28日10:00。CAL-017校准有效期至2026年12月31日，点检与零位确认正常。"),
        ("heading", "2 隔离登记"),
        ("paragraph", "CAL-018校准有效期已于2026年9月30日结束，目前已隔离，待重新校准确认，不得用于正式检验。"
         "本记录没有给出CAL-018重新投入使用的日期。"),
    ], effective="2026-10-28", features=("状态与日期",)),
]

PUBLIC_REFERENCE = dict(
    id="MFG-05", title="NIST量块手册公开参考", format="pdf", group="公共参考",
    file="documents/公共参考/05_NIST量块手册公开参考.pdf", source_kind="public_original",
    source_url="https://nvlpubs.nist.gov/nistpubs/Legacy/MONO/nistmonograph180.pdf",
    landing_url="https://www.nist.gov/publications/gage-block-handbook-0",
    publisher="National Institute of Standards and Technology",
    usage_terms_url="https://www.nist.gov/nist-research-library/library-faqs",
    attribution="Reprinted courtesy of the National Institute of Standards and Technology, U.S. Department of Commerce. Not copyrightable in the United States.",
    applicability="公开计量技术参考，不替代企业批准的作业或检验文件",
    features=["公开原始PDF", "英文", "长文档", "图表", "OCR必需"],
)

BY_ID = {item["id"]: item for item in [*DOCUMENTS, PUBLIC_REFERENCE]}


def question(number, text, answer, facts, evidence=(), *, category="事实查询", as_of=AS_OF):
    docs = [BY_ID[key] for key, _ in evidence]
    groups = list(dict.fromkeys(doc["group"] for doc in docs)) or ["公共参考", "生产工艺", "质量检验"]
    return dict(id=f"MFG-Q{number:02}", question=text, reference_answer=answer,
                answerable=bool(facts), required_facts=facts,
                expected_evidence=[dict(document=BY_ID[key]["file"].split("/")[-1], text=quote)
                                   for key, quote in evidence],
                source_groups=groups, expected_scope=groups[0] if len(groups) == 1 else "全部可访问",
                source_file_paths=[doc["file"] for doc in docs],
                required_source_documents=list(dict.fromkeys(doc["file"].split("/")[-1] for doc in docs)),
                citation_requirement="all_supporting_sources", category=category,
                difficulty="medium" if len(docs) > 1 else "easy", as_of=as_of,
                annotation_version=EDITION, profile="全范围测试用户",
                manual_review_required=True,
                semantic_checks=[answer], optional_facts=[], forbidden_claims=[])


QUESTIONS = [
    question(1, "2026年10月新开工的HC-A17 OP-20，首件须检查几件，何时才能恢复后续作业？",
             "连续检查3件，全部必检项目合格并由质量人员签署后才能恢复后续作业。",
             ["3件", "全部合格", "质量人员签署"],
             [("MFG-07", "首件确认须连续检查3件，所有必检项目全部合格，并由质量人员签署后才能恢复后续作业")], category="条件完整性"),
    question(2, "2026年9月新开工的HC-A17 OP-20，首件数量和签署要求是什么？",
             "按1.0版连续检查2件，全部项目合格后由质量人员签署。", ["2件", "质量人员签署"],
             [("MFG-06", "每次开工连续检查2件，全部项目合格后由质量人员签署")], as_of="2026-09-28"),
    question(3, "根据WO-017交接记录，该工单应参考哪版指导书，判断依据是什么？",
             "参考2.0版，因为该工单于2026年10月26日开工，属于10月1日起新开工范围。",
             ["2.0版", "10月26日", "10月1日"],
             [("MFG-14", "实际开工时间：2026年10月26日08:10"),
              ("MFG-09", "2026年10月1日起新开工工单由指导书1.0版切换为2.0版")], category="跨文档选版"),
    question(4, "WO-018在10月报工，是否就自动切换2.0版？",
             "不自动切换。9月28日已开工，且没有具体工单书面切换通知，继续沿用1.0版。",
             ["1.0版", "9月28日", "书面"],
             [("MFG-15", "实际开工日期：2026年9月28日"),
              ("MFG-09", "2026年9月30日及以前已经开工的工单继续沿用1.0版，即使在10月报工也不自动切换")], category="版本例外"),
    question(5, "按NIST量块手册，1英寸等于多少毫米？", "1英寸等于25.4毫米。", ["25.4"],
             [("MFG-05", "define the inch as 25.4 millimeters, exactly")], category="公开资料查询"),
    question(6, "截至2026年10月28日10:00，CAL-018能否用于正式检验？",
             "不能。校准有效期已于2026年9月30日结束，已隔离，待重新校准确认。",
             ["9月30日", "隔离", "重新校准"], [("MFG-17", "目前已隔离，待重新校准确认，不得用于正式检验")]),
    question(7, "CAL-150与MIC-025的分辨力和最大允许误差分别是什么？",
             "CAL-150分辨力0.01 mm、最大允许误差±0.02 mm；MIC-025分辨力0.001 mm、最大允许误差±0.003 mm。",
             ["0.01", "±0.02", "0.001", "±0.003"],
             [("MFG-01", "CAL-150"), ("MFG-01", "MIC-025")], category="表格参数"),
    question(8, "HC-A17 OP-20首件的外径、孔深和外观要求分别是什么，还有哪些记录条件？",
             "外径19.95至20.05 mm、孔深12.00至12.10 mm，均包含上下限；外观无可见毛刺和碰伤；工单、批次、量具编号及质量人员签署齐全。",
             ["19.95", "20.05", "12.00", "12.10", "毛刺", "碰伤", "质量人员签署"],
             [("MFG-08", "19.95至20.05 mm"), ("MFG-08", "12.00至12.10 mm"),
              ("MFG-08", "工单、批次、量具编号及质量人员签署齐全")], category="表格条件"),
    question(9, "WO-017更换定位治具后应完成哪些确认，才能恢复后续作业？",
             "按2.0版重新首件确认，连续3件全部必检项目合格，并由质量人员签署后才能恢复后续作业。",
             ["3件", "质量人员签署"], [("MFG-14", "14:10更换定位治具"),
             ("MFG-07", "首件确认须连续检查3件，所有必检项目全部合格，并由质量人员签署后才能恢复后续作业")], category="跨文档条件"),
    question(10, "2.0版指导书规定，哪些情况需要重新进行首件确认？",
             "新工单开工、更换定位治具、连续停机超过4小时后重新开工，任一情形均须重新首件确认。",
             ["新工单", "定位治具", "超过4小时"], [("MFG-07", "出现下列任一情形时必须重新进行首件确认")], category="列表完整性"),
    question(11, "借用量具需要同时满足哪些条件？",
             "校准有效、点检与零位正常，并登记借出方、借入方、量具编号和归还时间；归还前清洁并复核，异常时隔离。",
             ["校准", "零位", "借出方", "借入方", "量具编号", "归还时间", "隔离"],
             [("MFG-12", "借用量具必须同时满足以下条件"), ("MFG-12", "归还前清洁并复核状态")], category="列表条件"),
    question(12, "首件检查都合格就能直接交接吗，流程还要求什么？",
             "还须记录工单、批次和量具编号，质量人员签署、生产班长确认齐全；未关闭暂停要求须按书面批准的恢复条件执行。",
             ["量具编号", "质量人员", "生产班长", "书面批准"],
             [("MFG-02", "检验记录写明工单、批次和量具编号"),
              ("MFG-02", "质量人员完成签署，生产班长确认记录齐全"),
              ("MFG-02", "存在未关闭的暂停要求时，须按书面批准的恢复条件执行")], category="跨页条件"),
    question(13, "ECN-2026-018是否也适用于HC-A18？", "不适用，HC-A18须查询独立指导书。", ["HC-A18", "独立"],
             [("MFG-09", "HC-A18不属于本次变更范围，应查询其独立的指导书")], category="适用对象"),
    question(14, "HC-A17 OP-20两版指导书的首件数量有什么差异？",
             "1.0版连续2件，2.0版连续3件。", ["1.0", "2件", "2.0", "3件"],
             [("MFG-06", "每次开工连续检查2件"), ("MFG-07", "首件确认须连续检查3件")], category="版本比较"),
    question(15, "归档的1.0版是否还能用于回答2026年9月的作业要求？",
             "可以，用于其当时适用范围的历史查询；归档不意味着历史问题不能回答。", ["历史", "适用"],
             [("MFG-13", "已归档不等于不能回答历史问题"),
              ("MFG-06", "本版归档后仍用于其有效期间的历史查询")], category="历史版本"),
    question(16, "是否可以只按上传时间或检索排名判断哪份工艺文件现行？",
             "不能，需结合生效时间、适用产品、工序及明确替代关系判断。", ["生效时间", "适用产品", "工序", "替代关系"],
             [("MFG-13", "上传时间、入库状态、索引版本和检索排名均不能单独证明工艺文件现行")], category="版本口径"),
    question(17, "HC-A17 OP-20的批准主轴转速是多少？", "资料未提供批准的主轴转速，不能猜测。", [], category="资料不足"),
    question(18, "WO-017更换定位治具后的首件复检实际几点完成？", "未提供后续复检实际完成时间。", [], category="资料不足"),
    question(19, "现在HC-A17成品库存有多少件？", "未接入实时库存，现有资料无法确认。", [], category="资料不足"),
    question(20, "HC-B99的OP-30首件检验上下限是多少？", "未提供HC-B99 OP-30适用规范。", [], category="资料不足"),
]

ROLE_PERMISSIONS = {
    "操作员": {"公共参考": "viewer", "生产工艺": "viewer"},
    "工艺工程师": {"公共参考": "viewer", "生产工艺": "editor", "质量检验": "viewer"},
    "质量工程师": {"公共参考": "viewer", "生产工艺": "viewer", "质量检验": "editor"},
    "知识管理员": {group: "admin" for group in GROUPS},
    "仅参考资料用户": {"公共参考": "viewer"},
}
