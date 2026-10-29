"""Reviewed native-Chinese official sources; originals, never translated NIST.

One independently published document counts once. The two HTML articles are
archived verbatim and converted to Markdown with a disclosed conversion.
Publication/effective dates below are document-edition dates, not a claim that
every archived technical specification is the latest applicable regulation.
"""

EDITION = "manufacturing-zh-v3"
JL_CATALOG = "https://scjg.jl.gov.cn/fw/wsbl/jlxxfw/jljsgf/dfjzgf/"
JL_FILES = JL_CATALOG


def pdf(number, title, url, family, split="test", *, landing=None, publisher="吉林省市场监督管理厅", features=()):
    return dict(id=f"MFG-ZH{number:02}", title=title, source_url=url,
                landing_url=landing or JL_CATALOG, publisher=publisher,
                source_family=family, split=split, kind="public_pdf", format="pdf",
                group="公共参考", language="zh-CN", features=list(features))


SOURCES = [
    pdf(1, "工业产品生产单位落实质量安全主体责任监督管理规定_第75号令_2023",
        "https://scjgj.fujian.gov.cn/zt/gyxkgg/zzwj/202308/P020230811375289284467.pdf",
        "industrial-quality-responsibility-75", "development",
        landing="https://scjgj.fujian.gov.cn/zt/gyxkgg/zzwj/202308/t20230811_6224396.htm",
        publisher="国家市场监督管理总局（福建省市场监督管理局公开附件）",
        features=("中文原始PDF", "条款", "质量责任与日周月管控")),
    dict(id="MFG-ZH02", title="辽宁省工业产品生产销售单位落实质量安全主体责任工作指南_2023",
         source_url="https://scjg.ln.gov.cn/scjdglj/fw/wyk/tbtg/cpzlaqjg/2023070414585541222/index.shtml",
         landing_url="https://scjg.ln.gov.cn/scjdglj/fw/wyk/tbtg/cpzlaqjg/2023070414585541222/index.shtml",
         publisher="辽宁省市场监督管理局", source_family="industrial-quality-responsibility-75",
         split="development", kind="public_web", format="md", group="公共参考", language="zh-CN",
         content_xpath="//div[contains(concat(' ',normalize-space(@class),' '),' TRS_Editor ')]",
         features=["官方中文网页", "原始HTML保留", "进货查验与不合格品管理"]),
    dict(id="MFG-ZH03", title="推进重点工业产品质量安全追溯的实施意见及附件_2024",
         source_url="https://www.huitong.gov.cn/huitong/c133799/202410/b80f039537524cc5b646c790a27c4b6e.shtml",
         landing_url="https://www.huitong.gov.cn/huitong/c133799/202410/b80f039537524cc5b646c790a27c4b6e.shtml",
         publisher="国家市场监督管理总局（会同县人民政府公开转载）",
         source_family="industrial-product-traceability-2024", split="test", kind="public_web",
         format="md", group="公共参考", language="zh-CN",
         content_xpath="//div[contains(concat(' ',normalize-space(@class),' '),' p_box ')]",
         features=["官方中文网页", "原始HTML保留", "追溯数据字段与公开范围", "含附件全文"]),
    pdf(4, "制造业质量管理数字化实施指南_试行_2021",
        "https://gdkfq.ezhou.gov.cn/zwdt/ztzl/gdjkqhqzcfwzl/gjj/zcwj/kjcx/202310/P020250903408157648769.pdf",
        "manufacturing-quality-digitalization-2021",
        landing="https://gdkfq.ezhou.gov.cn/zwdt/ztzl/gdjkqhqzcfwzl/gjj/zcwj/kjcx/202310/t20231020_582526.html",
        publisher="工业和信息化部（鄂州葛店经济技术开发区官方转载）",
        features=("中文原始PDF", "多层级章节", "全流程物料追溯")),
    pdf(5, "盐雾试验设备校准规范_JJF吉92-2021", JL_FILES+"202108/P020210831582548526055.pdf", "jl-salt-mist-92-2021", "development", features=("中文原始PDF", "表格", "公式")),
    pdf(6, "砂尘试验设备校准规范_JJF吉121-2023", JL_FILES+"202312/P020231208306770938636.pdf", "jl-sand-dust-121-2023", features=("中文原始PDF", "表格", "公式")),
    pdf(7, "热工仪表校验仪校准规范_JJF吉31-2021", JL_FILES+"202108/P020210831581706202719.pdf", "jl-thermal-calibrator-31-2021", "development", features=("中文原始PDF", "长表格", "公式")),
    pdf(8, "压合机校准规范_JJF吉103-2022", JL_FILES+"202211/P020221125244262240961.pdf", "jl-compactor-103-2022", features=("中文原始PDF", "工艺设备", "公式")),
    pdf(9, "作动器校准规范_JJF吉102-2022", JL_FILES+"202211/P020221125243623917237.pdf", "jl-actuator-102-2022", features=("中文原始PDF", "工艺设备", "公式")),
    pdf(10, "旁路式数字压差计校准规范_JJF吉104-2022", JL_FILES+"202211/P020221125244968970079.pdf", "jl-differential-pressure-104-2022", "development", features=("中文原始PDF", "表格", "公式")),
    pdf(11, "工业内窥镜校准规范_吉林公开扫描原件", JL_FILES+"202012/P020201208480360995113.pdf", "jl-industrial-endoscope-archive", features=("中文原始PDF", "OCR必需", "扫描水印")),
    pdf(12, "表面轮廓测量仪校准规范_吉林公开扫描原件", JL_FILES+"202011/P020201119402544220995.pdf", "jl-surface-profile-archive", features=("中文原始PDF", "OCR必需", "扫描件")),
    pdf(13, "圆锥螺纹量规校准规范_吉林公开扫描原件", JL_FILES+"202011/P020201119401430105654.pdf", "jl-conical-thread-gauge-archive", features=("中文原始PDF", "OCR必需", "扫描件")),
    pdf(14, "卡规检查仪校准规范_吉林公开扫描原件", JL_FILES+"202011/P020201119400360575640.pdf", "jl-caliper-checker-archive", features=("中文原始PDF", "OCR必需", "扫描件")),
    pdf(15, "检验夹具校准规范_吉林公开扫描原件", JL_FILES+"202011/P020201119399220950619.pdf", "jl-inspection-fixture-archive", features=("中文原始PDF", "OCR必需", "扫描件")),
    pdf(16, "专用量规校准规范_吉林公开扫描原件", JL_FILES+"202011/P020201119398404525340.pdf", "jl-special-gauge-archive", features=("中文原始PDF", "OCR必需", "扫描件")),
    pdf(17, "同轴度测试仪校准规范_JJF吉116-2022", JL_FILES+"202211/P020221125252444897475.pdf", "jl-coaxiality-116-2022", "development", features=("中文原始PDF", "表格", "公式")),
    pdf(18, "DIN53211-4涂料黏度计校准规范_JJF吉114-2022", JL_FILES+"202211/P020221125251372727238.pdf", "jl-coating-viscometer-114-2022", features=("中文原始PDF", "表格", "公式")),
]

DEFERRED = [
    dict(title="机械设备企业质量管理核心过程实施指南", url="https://www.cnca.gov.cn/cms_files/filemanager/cnca/rdzt/xwqycs/sszn/202109/P020210924576465002194.pdf", reason="官方在线正文已核实，但原件下载返回403；临沂官方转载附件返回下载验证页。未计入18份、未用网页摘要冒充PDF。"),
    dict(title="在线激光测厚仪校准规范_JJF沪苏浙皖4018-2025", url="https://scjgj.sh.gov.cn/cmsres/88/8880d574340b4516821e9f94e07e8efd/578383ca2484b0d29a502f7a77b31191.pdf", reason="官方原件直接下载返回403；未收录。"),
    dict(title="机械行业较大危险因素辨识与防范指导手册_2016", url="https://www.mem.gov.cn/gk/gwgg/201604/W020171101488969491848.pdf", reason="原件下载返回405；未收录，也未作为现行要求。"),
    dict(title="关于征集2022年机械电子汽车制造业关键过程能力提升优秀案例的通知", url="https://gxt.shaanxi.gov.cn/cyfz/ctzc/202208/P020241010378022170996.pdf", reason="下载核查后确认为案例征集通知，未包含所需完整技术指南；不用于凑资料数量。"),
]

ALLOWED_HOSTS = frozenset({"scjgj.fujian.gov.cn", "scjg.ln.gov.cn", "www.huitong.gov.cn", "gdkfq.ezhou.gov.cn", "scjg.jl.gov.cn"})
