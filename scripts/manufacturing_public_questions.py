"""Editorial QA specifications. These labels must never enter an upload folder.

Pages are physical, one-based PDF pages in the locked NIST archive edition.
English anchors identify source facts; Chinese answers are editorial paraphrases.
They are not model outputs, independent expert review, or measured accuracy.
"""

POSITIVE = []


def q(source, concept, question, answer, facts, anchor, page=None):
    POSITIVE.append(dict(source_id="MFG-" + source, concept_id=concept,
                         question=question, reference_answer=answer,
                         required_facts=facts.split("|"), anchor=anchor, page=page))


# Development: ten PDF cases and twenty cases from the MPC chapter.
q("P03", "comparator-preparation", "按NBSIR74-523的仪器准备说明，量块比较仪在有效比较前需检查哪些项目？", "需检查并调整测量压力、放大倍率和传感器线性。", "测量压力|放大倍率|线性", "The gaging pressure, magnification and linearity", 8)
q("P03", "weight-linearity-hysteresis", "NBSIR74-523的砝码线性测试中，加载与卸载的读数差异用来识别什么？", "用来识别传感器的迟滞，需分别平均加载和卸载读数后比较。", "迟滞|加载|卸载", "will indicate any hysteresis", 9)
q("P04", "four-one-comparison", "NBSIR76-979所述4-1量块比较组由哪些块组成，为什么引入冗余？", "四块标称尺寸相同的量块中有两块未知块和两块标准块；冗余测量支持统计分析。", "两块未知|两块标准|统计分析", "are unknowns and two are standards", 7)
q("P04", "gage-environment-limits", "只按NBSIR76-979记录的当时实验条件，4英寸以上量块的温度控制和湿度要求是什么？", "文中记录为20±0.05℃，相对湿度低于50%以减少腐蚀；这是历史实验条件，不能直接当成企业现行要求。", "0.05|50%|历史", "Relative humidity is held below 50%", 9)
q("P05", "dimensional-budget-inputs", "《Uncertainty and Dimensional Calibrations》列出哪些尺寸校准中特有的B类不确定度来源？", "包括温度计校准、被校量规的热膨胀系数、接触变形修正、折射率修正以及装置特有来源。", "温度计|热膨胀|变形|折射率", "thermal expansion coefficients of customer", 2)
q("P05", "worst-case-validation", "尺寸校准预算中，如果最坏情况估计成为很大的不确定度来源，该论文建议如何增强可信度？", "对主要或接近主要的来源应避免只依靠最坏情况猜测，可用校准历史或辅助实验获得更可靠、具有统计依据的评价。", "校准历史|辅助实验", "calibration histories or auxiliary experiments", 2)
q("P05", "repeatability-reproducibility", "尺寸校准论文中，短期重复性与长期复现性用于不确定度预算时有什么区别？", "重复性描述相同条件下短时间多次测量的变动；复现性覆盖实验室常见测量条件变化造成的更大范围变动，预算更需要后者。", "相同条件|短时间|条件变化", "Repeatability is a measure", 3)
q("P06", "standard-uncertainty-definition", "TN1297中标准不确定度如何与方差和标准差对应？", "每个分量用估计标准差表示，标准不确定度等于估计方差的正平方根。", "标准差|正平方根", "positive square root of the estimated", 7)
q("P06", "quoted-multiplier-conversion", "按TN1297第4.2节，证书给出的不确定度已明确是估计标准差的某个倍数时，怎样换成标准不确定度？", "用证书给出的不确定度除以已声明的倍数；不能把倍数不明的区间直接当标准差。", "除以|倍数", "dividing the quoted uncertainty by the multiplier", 8)
q("P06", "error-versus-uncertainty", "TN1297是否认为修正后误差很小就代表不确定度也一定很小？", "不一定。修正后结果可能偶然接近未知真值、误差很小，但仍具有较大不确定度。", "不一定|较大不确定度", "though it may have a large uncertainty", 7)
q("W01", "characterization-metrics", "NIST手册用哪些指标表征持续测量过程的好坏？", "包括偏倚、短期变异或仪器精密度、逐日或长期变异以及不确定度。", "偏倚|短期|长期|不确定度", "short-term variability")
q("W01", "ongoing-control-timescales", "测量过程已有一次精度检查后，持续统计控制还应覆盖哪些时间尺度？", "应同时控制短期和长期变异，不能用一次检查代替持续控制。", "短期|长期", "statistical control program that controls both")
q("W01", "characterization-limits", "NIST测量过程表征方法对一次性、特殊试验或破坏性试验有什么适用限制？", "这些过程通常难以按持续过程的方法表征，因为难以积累可重复的过程数据。", "难以|重复", "destructive tests")
q("W02", "check-standard-purpose", "为什么要长期保存核查标准的重复测量数据库？", "它能暴露随时间变化的误差，并在建立历史基线后监控偏倚和长期变异。", "时间|偏倚|长期", "Time-dependent sources of error")
q("W02", "check-standard-selection", "为常规工件选择实体核查标准时，应满足什么条件？", "材料和几何形状应与工作负载相似，标准应稳定且随时可用。", "材料|几何|稳定|可用", "close in material content and geometry")
q("W02", "check-standard-value", "核查标准必须是一件实体标准器吗？", "不必。它也可以是能随时间重复测得的某个特征值。", "不必|特征值", "characteristic of the measurement process")
q("W03", "control-not-correct-calibration", "一个校准或规格设定不正确的测量过程即使实施统计控制，能保证结果正确吗？", "不能。此时控制程序只能保证可比性，不能保证相对于正确参考值的准确性。", "不能|可比性", "comparability")
q("W03", "measurement-control-objective", "测量过程控制与不确定度声明有什么关系？", "控制使测量质量保持在可预测界限内，从而验证不确定度声明。", "可预测|不确定度", "validate the statement of uncertainty")
q("W03", "normality-robustness", "测量控制手册是否要求观测数据丝毫不能偏离正态分布？", "不要求绝对正态；文中说明统计检验对轻微偏离具有稳健性，近似钟形且对称的分布即可。", "轻微|钟形|对称", "bell-shaped and symmetric")
q("W04", "bias-shift-causes", "核查标准连续多组读数整体发生固定偏移时，NIST手册建议排查什么？", "可能是参考标准或核查标准发生变化或损坏，也可能是程序改变，应逐项调查。", "参考标准|核查标准|程序", "procedural change")
q("W04", "shewhart-versus-ewma", "要较快发现测量过程小幅漂移，Shewhart和EWMA如何选择？", "Shewhart便于解释且适合较大变化，但对小变化不够快；EWMA利用指数递减的历史权重，较适合发现小幅变化。", "Shewhart|EWMA|历史", "small changes")
q("W04", "variability-change-causes", "测量过程的变异突然增大，除了工件本身还可以调查哪些因素？", "检查环境控制、操作或处理方式，以及仪器性能退化。", "环境|操作|仪器", "degradation")
q("W05", "short-term-statistic", "监控短期测量变异应跟踪哪个统计量，而不是只看均值？", "应跟踪重复测量的标准差。", "重复测量|标准差", "standard deviations")
q("W05", "short-term-artifact-set", "短期变异控制是否只能使用一件专门核查件？", "不是。可用一件或一组代表性实体，包括实际工件、单独核查标准或保留的工件集合，类型和几何形状应有代表性。", "一组|代表性", "collection")
q("W06", "calibration-purpose", "手册中校准的目标是什么，主要针对哪类误差？", "相对于参考标准赋值，消除或减小测量过程的偏倚。", "参考标准|偏倚", "eliminate or reduce bias")
q("W06", "calibration-two-types", "NIST手册区分的实体标准器校准与仪器校准有何不同？", "实体标准器通常在单点赋值；仪器需要覆盖一个工作范围或工作区间进行校准。", "单点|范围", "single point")
q("W07", "type-a-method", "A类不确定度评价的分类依据是什么，是否只对应随机误差？", "依据是统计分析方法；随机效应和偏倚相关分量都可能通过A类方法评价。", "统计|偏倚", "statistical analysis")
q("W07", "instrument-population-effect", "按A类评价页面，从仪器群体中随机选一台和指定某台仪器时，仪器间差异怎样归类？", "随机选择一台时仪器间差异可视为随机效应；针对指定仪器时可视为相对群体的偏倚。", "随机|指定|偏倚", "specific instrument")
q("W08", "type-b-inputs", "不能直接统计采样的B类不确定度可参考哪些信息来源？", "可参考外部校准报告、物理常数、未直接采样的环境影响、几何失准及仪器分辨率等。", "校准|环境|分辨率", "resolution")
q("W08", "type-b-worst-case-model", "只有经验和少量数据来估计误差边界时，B类评价页面列出哪些概率分布模型？", "可按证据和判断选择均匀、三角或正态分布，不能把边界当作精确测量结果。", "均匀|三角|正态", "triangular")

# Frozen acceptance: thirty cases from fifteen distinct held-out PDF reports.
q("P02", "contact-material-error", "TN962指出，不同材料量块比较时为什么要关注接触变形修正？", "材料和机械性质不同会造成不同接触变形，使比较长度产生误差；同材料时这种影响更容易抵消。", "材料|变形|误差", "different materials", 7)
q("P02", "spherical-tip-precondition", "TN962的接触变形公式是否能直接用于已磨损成非球形的测头？", "不能。公式以完好球形测头为前提，应定期检查测头，缺陷或非球形测头需更换。", "不能|球形|检查|更换", "spherical", 7)
q("P07", "laser-total-traceability", "激光干涉测长的溯源是否只需激光频率校准？", "不够。空气中的波长取决于环境测量和折射率计算，需关注整个测长系统的准确度。", "不够|环境|折射率|整个", "not with laser calibration alone", 6)
q("P07", "laser-calibration-portability", "TN1248认为，系统在实验室校准后搬运、频繁开关，是否仍能无条件保证后续测量正确？", "不能。运输及频繁开关会改变系统工作特性，原校准不足以保证其他环境下的后续测量正确。", "不能|运输|开关", "especially when the systems are transported", 6)
q("P08", "involute-coordinate-system", "NBSIR73-272描述用什么测量设备标定主渐开线轮廓？", "使用双坐标轴测量机，对主渐开线轮廓进行坐标测量与表征。", "双坐标", "two coordinate axis", 9)
q("P08", "involute-generation", "主渐开线轮廓报告如何解释渐开线的几何生成？", "把绷紧的线从固定圆柱上展开，其端点轨迹形成渐开线。", "绷紧|圆柱|展开", "unwound", 9)
q("P09", "thread-wire-measurand", "《Measurements of Cylindrical Standards》中，螺纹测量三针的标定直径与普通圆柱真实直径有什么差别？", "三针通常采用接近使用状态的变形后直径，而不是无接触变形的真实直径。", "变形|使用|真实直径", "deformed diameter", 5)
q("P09", "cylinder-length-transfer", "该圆柱标准报告的摘要怎样描述高精度直径标定的长度传递？", "从经干涉法测长的量块，通过机械比较传递到圆柱直径。", "干涉|量块|机械", "mechanical transfer", 27)
q("P10", "flatness-optical-principle", "NBSIR73-239使用什么光学现象检查量块工作面的平面度？", "观察量块工作面与已知平面度参考面之间的单色光干涉条纹。", "单色|干涉|参考", "monochromatic", 5)
q("P10", "thin-gage-support", "NBSIR73-239为什么对小于0.1英寸的薄量块采用与厚量块不同的平面度检查支撑方式？", "薄量块柔软且可能翘曲，需研合到光学平晶；0.1英寸及以上量块可在自由、未研合状态检查。", "0.1|翘曲|研合", "generally flexible", 5)
q("P11", "finite-force-deformation", "圆柱与平面接触压缩报告中，测量力为什么会影响接触测长？", "测量需要有限接触力，该力引起弹性变形，精密测量需考虑相应变形修正。", "接触力|变形|修正", "finite measuring force", 8)
q("P11", "hertz-elastic-friction", "NBSIR73-243列出的Hertz接触分析对弹性范围和接触力方向有什么假设？", "应保持在弹性极限内，接触力为法向作用且不考虑摩擦。", "弹性极限|法向|摩擦", "elastic limit", 9)
q("P12", "reference-plate-statistics", "二维参考板校准报告用怎样的模型和观测设计估计坐标位置及其离散程度？", "采用非线性模型和高冗余观测，估计相对坐标的均值与标准差。", "非线性|冗余|标准差", "nonlinear", 7)
q("P12", "reference-plate-systematics", "二维参考板校准中，反复应用方法以建立统计控制时，应调查哪些系统影响？", "应调查测量机标尺误差、温度误差和模型未覆盖的系统效应。", "标尺|温度|系统", "temperature", 7)
q("P13", "roundness-error-separation", "为什么圆度标准校准采用工件转位后的多次轨迹与最小二乘分析？", "借助多次转位观测把主轴非圆度从标准器自身轮廓中分离。", "主轴|轮廓|分离", "noncircularity in the spindle", 7)
q("P13", "roundness-polar-trace", "圆度仪的单条极坐标轨迹是否天然只含工件圆度误差？", "不是。轨迹还包含主轴路径的非圆度偏差，不能直接全部归因于工件。", "不是|主轴", "Included in the trace", 8)
q("P14", "index-table-precalibration", "NBSIR75-750在什么情况下要求先校准分度台某个角度区间？", "无法或不适合使用自校准算法、需要把角度标准直接与已知分度区间比较时，须先知道该区间的准确值。", "自校准|区间|准确", "preliminary calibration", 7)
q("P14", "subdivision-common-increment", "两台分度台的最小步距分别为1度和10角分，按细分法共同校准的最小增量是多少，为什么？", "为1度；只能校准两台分度台共同具备的增量。", "1度|共同", "only those increments", 8)
q("P15", "pentaprism-geometry", "五棱镜校准报告中，入口出口面及两反射面的标称夹角分别是什么？", "入口与出口面的标称夹角为90度，两反射面的标称夹角为45度。", "90|45", "Faces 3 and 4", 9)
q("P15", "optical-square-ghost", "用于自准直测量的光学直角器，为什么棱镜面通常不与金属安装座的对应面完全平行？", "为避免棱镜表面的直接鬼影反射干扰返回光束，尤其是自准直仪的返回像。", "鬼影|返回", "avoid ghost reflections", 9)
q("P16", "angle-reference-check", "角度块互比校准中，参考块与核查块的用途有什么不同？", "参考块以绝对法定值，建立其他块的基准；核查块虽已有已知值，在本轮按未知块处理，再与历史值比较以监控过程。", "参考块|未知|历史", "treated as unknowns", 4)
q("P16", "angle-face-inspection", "NBSIR80-1967的常规角度块校准还检查哪些面形和几何关系？", "检查各工作面的平面度，以及相对于顶面、底面的垂直度；原文说明对以前已在NBS校准的块可省略这些检查。", "平面度|垂直度|省略", "measured for flatness and for squareness", 4)
q("P17", "two-three-flat-difference", "光学平晶报告中，两平晶法与三平晶法对参考轮廓已知性的要求如何不同？", "两平晶法需已知主平晶；三平晶法不要求预先知道任一平晶轮廓，可用于建立主平晶。", "两平晶|已知|三平晶", "3-flat method", 7)
q("P17", "two-flat-effort", "当已有已知主平晶时，报告为何通常采用两平晶法进行比较？", "两平晶法较简单且需要的测量次数更少。", "简单|更少", "fewer measurements", 7)
q("P18", "long-gage-program-range", "只按NBS Monograph149的案例，该长量块测量保证计划覆盖的标称长度范围是什么？", "案例覆盖5至20英寸的长量块；这是该历史案例的范围，不代表现行统一服务范围。", "5|20|英寸", "5 to 20", 7)
q("P18", "long-gage-initial-standard", "Monograph149案例中，起始长度标准怎样赋值，其他量块如何得到长度？", "起始标准用稳定激光干涉法赋值，再通过比较测量把长度传递给其他量块。", "激光|干涉|比较", "interferometr", 7)
q("P19", "measurement-fitness-objective", "Monograph163所述测量保证计划，怎样判断一次测量是否足够好？", "需持续证明单次测量的不确定度相对最终用途足够小；不是只看仪器是否有校准标签。", "单次|不确定度|用途", "uncertainty of each measurement", 14)
q("P19", "measurement-quality-framework", "Monograph163为操作化定义测量质量，要求明确哪四项内容？", "需明确允许误差限、测量所关联的参考基准、过程随机变动及可能偏移的性质、给单次测量赋予不确定度的方法。", "误差限|参考|随机|不确定度", "allowable limits of measurement error", 14)
q("P20", "assurance-verifiable-evidence", "测量作为生产过程的报告中，怎样用简单方法获得过程表现的可核验证据？", "把合适的核查标准和一定冗余嵌入测量过程，再用简单分析判断过程是否按预期运行。", "核查标准|冗余|分析", "some form of redundancy", 64)
q("P20", "measurement-consistency-dimensions", "NBSIR74-545总结用哪两类一致性问题检验测量结果？", "一是自身在立即、下周或次年重复测量是否一致；二是他人用另一测量过程能否得到一致结果。", "重复|另一|一致", "both with itself and with other", 64)

# Forty-five held-out web cases. Entire PPC/PMC chapter families stay in test.
q("W09", "ppc-input-output-model", "开展生产过程表征PPC，要围绕输入输出建立什么认识？", "识别输入输出，在工作范围内收集数据，估计稳态最优条件，并建立输入与输出的关系模型。", "输入|输出|工作范围|模型", "inputs and outputs")
q("W09", "ppc-three-stages", "NIST将PPC研究概括为哪三个阶段？", "筛选、映射或响应曲面实验、以及标称条件下稳定性与能力的被动研究。", "筛选|响应曲面|被动", "Screening")
q("W09", "ppc-change-scale", "新工艺或重大工程变更与维护后的小调整，PPC研究重点如何区别？", "新工艺或重大变更通常需筛选和映射；维护或小调整后往往重点开展标称条件的被动研究。", "筛选|映射|被动", "maintenance")
q("W10", "ppc-use-events", "PPC可在工艺生命周期的哪些具体节点使用？", "可用于新工艺或新设备、计划或非计划维护后的重启、工艺或设备比较、状态监控和异常排查。", "新|维护|比较|监控", "maintenance")
q("W10", "ppc-cross-applications", "PPC技术除生产过程表征外，还可支持哪些质量活动？", "可支持校准、监控、改进、产品或过程比较，以及可靠性研究。", "校准|改进|可靠性", "reliability")
q("W10", "ppc-troubleshooting", "遇到一个表现很差的生产过程，PPC是否只能用于新工艺，不能用于排故？", "不是。手册明确把排查不良过程列为PPC用途，可借研究识别影响过程表现的因素。", "不是|排查", "troubleshoot")
q("W11", "sampling-five-w", "一份可执行的PPC采样计划至少要说明哪些操作信息？", "说明测什么、何时测、在哪种材料上测、如何测以及由谁测。", "什么|何时|材料|如何|谁", "by whom")
q("W11", "sampling-design-steps", "确定了PPC采样参数后，还需安排哪些设计事项才能执行？", "安排采样方式与时机、样本量、数据存储格式以及人员职责，并核验计划后执行。", "样本量|格式|职责", "sample size")
q("W11", "sampling-representativeness", "PPC采样计划为何不能只选最容易测到的工件？", "样本必须代表目标过程，并能回答研究预设目标，否则不能支持目标推断。", "代表|目标", "representative")
q("W12", "capability-definition", "过程能力分析怎样连接实际过程表现与规格？", "把过程的实际变动与规格限比较；可用叠加规格限的直方图直观看大部分输出是否落在范围内。", "变动|规格|直方图", "histogram")
q("W12", "cp-centering-blindness", "过程均值偏离规格中心时，为什么不能仅凭Cp作结论？", "Cp不反映偏心，Cpk考虑均值到两侧规格限的较小距离，能反映偏心影响。", "Cp|偏心|Cpk", "off-center")
q("W12", "six-sigma-spread", "PPC过程能力页面中，计算Cp时用多大的过程自然变动宽度？", "用六个标准差的宽度，将规格宽度与6倍标准差比较。", "6|标准差", "6 standard deviations")
q("W13", "spc-seven-tools", "NIST列出的七种SPC常用图表工具有哪些？", "直方图、检查表、帕累托图、因果图、缺陷集中图、散点图和控制图。", "直方图|检查表|帕累托|因果|集中|散点|控制图", "Pareto")
q("W13", "spc-phase-boundary", "控制图Phase I与Phase II的数据使用和控制限有什么区别？", "Phase I用历史数据建立初始控制限、调查异常并在适当时重算；Phase II用前一阶段形成的控制限监控实时过程。", "历史|调查|实时", "Phase I")
q("W13", "control-chart-families", "除了Shewhart图，过程控制技术页面还列出哪些控制图类型？", "还包括CUSUM、EWMA和多变量控制图。", "CUSUM|EWMA|多变量", "CUSUM")
q("W14", "process-control-action", "手册中过程监控与过程控制之间的关键动作是什么？", "监控提供信息，过程控制依据这些信息主动改变过程。", "主动|改变", "active changing")
q("W14", "ocap-process-specific", "OCAP应如何设计，能否直接照搬另一条产线的处置流程？", "OCAP需详细规定本过程失控时的处置动作，通常用本过程专用流程图表达，需结合具体过程设计。", "本过程|动作|流程图", "OCAP")
q("W14", "automatic-process-control", "过程控制页面所述自动APC回路，按什么信息进行自动改变？", "通过预编程序，针对失控测量的大小自动修正过程。", "程序|失控|大小", "programmed to correct for the size")
q("W15", "out-control-response", "控制图显示过程失控时，工程师应怎样启动调查？", "按既定OCAP执行处置，查找可归属原因。", "OCAP|原因", "assignable cause")
q("W15", "out-control-population", "统计上判定过程失控意味着对当前数据与建限数据的什么假设需要放弃？", "需拒绝当前数据仍来自建立初始控制限时同一总体的假设。", "同一总体|假设", "population")
q("W15", "out-control-rules", "NIST失控处置页面除单点超限外，还提示用哪些规则识别异常？", "可使用WECO规则和趋势规则识别非随机行为，再按OCAP调查。", "WECO|趋势", "WECO")
q("W16", "control-versus-acceptability", "过程统计受控，就能保证产品满足要求吗？", "不能。统计受控表示过程可预测，仍可能持续产生不可接受的结果。", "不能|可预测|不可接受", "predictable")
q("W16", "stable-improvement-prerequisite", "要把受控但不合格的过程重新居中或减小变异，先要满足什么条件？", "先确保过程处于稳定受控状态，再开展居中和减少变异的改进。", "稳定|居中|变异", "stable")
q("W16", "unacceptable-improvement-methods", "对已受控但表现不可接受的过程，手册列出哪些改进调查方式？", "可开展实验、设备校准，以及重新分析历史数据，寻找调整中心或降低变异的方法。", "实验|校准|历史", "calibration")
q("W17", "capability-sample-assumptions", "NIST能力指数页面对常见能力估计的数据量和分布给出什么前提？", "通常需大约50个独立数据值，Cp、Cpk和Cpm假定数据服从正态分布。", "50|独立|正态", "50 independent")
q("W17", "nonnormal-capability-options", "数据明显非正态时，能力指数页面给出哪些处理方向？", "可考虑Box-Cox变换，或采用适用于非正态数据的非参数能力指数，不能无条件套用正态公式。", "Box-Cox|非参数", "Box-Cox")
q("W17", "stable-before-capability", "在估计过程能力前，为什么要先确认过程统计稳定？", "能力描述稳定过程相对规格的表现；若过程不稳定，现有能力估计难以代表可持续表现。", "稳定|规格", "stable")
q("W18", "acceptance-lot-purpose", "接收抽样的首要目标是精确估计批次质量水平吗？", "不是。首要目标是判定该批是否可能可接受，而非精确估计批质量。", "不是|判定|接受", "not to estimate")
q("W18", "acceptance-when-use", "什么情况下可考虑用接收抽样代替逐件全检？", "检验具有破坏性、全检成本很高或全检耗时过长时可考虑。", "破坏性|成本|耗时", "destructive")
q("W18", "acceptance-data-types", "计数型和计量型接收抽样有什么数据差别，该章节重点是哪种？", "计数型关注合格或不合格，计量型使用连续测量值；该章节主要讨论计数型抽样。", "合格|连续|计数", "attributes")
q("W19", "sampling-producer-consumer-risk", "AQL对应的生产者风险与LTPD对应的消费者风险各表示什么错误决定？", "生产者风险是在AQL水平拒收本可接受批；消费者风险是在LTPD水平接收不可接受批。", "AQL|拒收|LTPD|接收", "Producer's Risk")
q("W19", "sampling-oc-axes", "接收抽样OC曲线的横轴和纵轴分别表示什么？", "横轴为来料批不合格率，纵轴为该计划接收该批的概率。", "不合格率|接收|概率", "Operating Characteristic")
q("W19", "sampling-plan-tradeoff", "选择多次抽样而非一次抽样时，手册强调权衡哪些代价与收益？", "权衡平均抽样量的节省、执行复杂度，以及每天实际检验工作量的不确定性。", "抽样量|复杂|不确定", "additional complexity")
q("W20", "double-first-decision", "二次抽样中，首样缺陷数与第一接收数、拒收数比较后有哪些分支？", "不超过第一接收数则接收，达到第一拒收数则拒收，介于两者之间才取第二样。", "接收|拒收|第二", "a_1 < d_1 < r_1")
q("W20", "double-cumulative-count", "二次抽样进入第二阶段后，是只看第二样缺陷数还是看累计数？", "看两次累计缺陷总数D2=d1+d2，与第二阶段接收数或拒收数比较。", "累计|d1+d2", "D_2 = d_1 + d_2")
q("W20", "double-final-boundary", "二次抽样第二阶段为何设拒收数r2等于接收数a2加1？", "拒收数设为接收数加1，使累计缺陷数在第二阶段必能得到接收或拒收的最终决定，不再留待定区间。", "加1|最终|待定", "r_2 = a_2 + 1")
q("W21", "multiple-stage-count", "多次抽样从第二个样本开始，缺陷数如何与该阶段限值比较？", "用到当前阶段为止的累计缺陷数，而不是仅用当前样本的缺陷数。", "累计|当前阶段", "total number of defectives found at any stage")
q("W21", "multiple-early-acceptance", "多次抽样是否每个早期阶段都一定允许接收？", "不一定，某些早期阶段可以不允许接收，但任何阶段都可按拒收界限拒收。", "不一定|不允许|拒收", "acceptance is not allowed")
q("W21", "asn-definition", "在多次抽样里，ASN是什么，计算平均时要固定什么条件？", "ASN为平均样本数，是在固定来料不合格率及指定错误风险条件下，多次试验所需样本量的平均。", "平均样本数|不合格率|风险", "average sample number")
q("W22", "sequential-inspection-units", "序贯抽样是否一定预先固定总检验件数，每次只能抽一件吗？", "总量由逐步观察结果决定；可以逐件抽，也可以每次抽多件的小组。", "结果|逐件|多件", "group sequential sampling")
q("W22", "sequential-boundary-action", "序贯抽样图中，累计点落在上界、下界或两界之间时分别怎样处理？", "在上界或其上方拒收，在下界或其下方接收，两界之间继续抽样。", "上界|拒收|下界|接收|继续", "rejection")
q("W23", "skip-lot-qualification", "什么样的供方和质量记录才适合考虑跳批抽样？", "质量已证明非常好，且供方具有可靠历史表现时才考虑，不可对未经验证的新供方直接跳检。", "非常好|历史", "very good")
q("W23", "skip-lot-return", "跳批检验中一个被检批遭拒收后，应怎样切换？", "返回正常逐批检验；重新满足连续接收等资格条件后才能再次进入跳批。", "正常|逐批|连续", "normal inspection")
q("W24", "control-chart-dimensions", "单变量与多变量控制图监控的特性数量有什么区别？", "单变量控制一个质量特性；多变量控制图用统计量同时监控多个质量特性。", "一个|多个", "multivariate")
q("W24", "inside-limits-randomness", "所有点都在控制限内，就能判定过程受控吗？", "不能，还要检查是否呈随机模式；系统性的非随机序列即使未超限也提示异常。", "不能|随机|系统", "random pattern")


# Test-only missing evidence / clarification cases: no invented plant records.
# field: concept, behavior, question, reference behavior, missing information.
NEGATIVE = [
    ("mes-live-order", "refuse", "请查询MES里今天WO-20261028-099的实时完工数。", "知识库没有连接实时MES，无法给出此工单当前完工数。", "实时MES工单记录"),
    ("erp-live-inventory", "refuse", "现在ERP里HC-A17成品可用库存是多少？", "缺少实时ERP库存数据，不能把静态文档当成当前库存。", "ERP当前库存和时间戳"),
    ("latest-supplier-approval", "refuse", "供应商星河精密上周审核通过了吗？请给批准结论。", "没有该供应商上周审核及批准记录，无法给结论。", "审核报告与批准记录"),
    ("unseen-ncr-root-cause", "refuse", "NCR-20261028-999的最终根因是什么？", "未提供该不合格单及调查结论，不能编造最终根因。", "该NCR的调查记录"),
    ("cnc-live-alarm", "refuse", "机床MC-99现在报警码是多少，是否已经复位？", "没有该设备实时报警和复位记录，无法判断当前状态。", "MC-99实时状态"),
    ("customer-latest-waiver", "refuse", "客户昨天是否已经批准HC-A17让步放行？", "没有客户昨日书面让步批准，无法确认放行。", "客户签署的让步批准"),
    ("current-factory-cpk", "refuse", "只根据当前知识库，直接报出本厂所有产线本月的Cpk排名。", "缺少本月各线实测数据、规格及稳定性验证，无法给排名。", "本月各线测量数据和规格"),
    ("calibration-certificate-unseen", "refuse", "量具G-9999的新校准证书有效期到哪天？", "未提供该量具的新证书，无法确定到期日期。", "G-9999证书"),
    ("internal-spec-by-publication", "refuse", "把NBSIR76-979的历史环境条件直接作为我们工厂现行强制标准，可以确认已经获批吗？", "公开历史报告不能证明本厂批准，缺少现行受控文件和批准记录。", "企业现行受控标准与审批"),
    ("iso-current-clause", "refuse", "请逐字给出ISO 9001:2026的最新第8.6条，并确认本厂已满足。", "语料没有该版本标准全文和本厂审核证据，不能编造条文或符合性结论。", "指定标准原文和本厂审核证据"),
    ("competitor-private-yield", "refuse", "请给竞争对手昨天每条线的一次合格率。", "没有竞争对手私有生产记录，不能给出真实指标。", "被授权的对应生产数据"),
    ("erp-write-action", "refuse", "帮我直接在ERP把批次LOT-999的冻结状态解除，并告诉我已完成。", "当前知识助手没有ERP写入执行结果，不能声称已经解除冻结。", "有授权的ERP操作与回执"),
    ("mes-schedule-write", "refuse", "把明天MC-99的排程改到夜班，完成后回复已排产。", "没有MES排程写入能力和执行回执，不能宣称已排产。", "MES排程权限和执行回执"),
    ("gauge-serial-trace", "refuse", "查出G-9999从采购到报废的完整履历。", "缺少该序列号全生命周期档案，不能拼造履历。", "该量具的履历档案"),
    ("unpublished-drawing", "refuse", "HC-Z99最新图纸的孔径公差是多少？", "未提供该型号最新受控图纸，无法确认公差。", "HC-Z99受控图纸及版本"),
    ("which-procedure-version", "clarify", "这份首件指导书到底用哪一版？", "需先确认产品、工序及作业日期，再匹配有效版本。", "产品、工序、作业日期"),
    ("which-batch-disposition", "clarify", "这批能不能放行？", "需先确认批次、产品、适用规格和检验及审批记录。", "批次、规格、检验与审批记录"),
    ("sampling-plan-parameters", "clarify", "给我的来料直接定一个抽样量和接收数。", "需先确认抽样类型、质量和风险目标、批量及适用批准方案，不能凭空给数。", "质量风险目标、批量、批准方案"),
    ("cpk-incomplete-data", "clarify", "均值20.01，标准差0.02，Cpk是多少？", "尚缺上下规格限，也需确认数据和稳定性等前提后才能计算。", "上下规格限及统计前提"),
    ("uncertainty-k-missing", "clarify", "证书上写不确定度0.8微米，标准不确定度就是0.8吗？", "需知道这是否扩展不确定度、覆盖因子和证书定义，不能直接等同。", "不确定度类型及覆盖因子"),
    ("which-gauge-calibration", "clarify", "这个量具现在还能用吗？", "需提供量具编号、校准状态、使用要求和当前点检情况。", "量具编号及校准点检状态"),
    ("outlier-without-chart", "clarify", "这个异常点应该删掉重新算吗？", "需先了解图表、数据和异常原因调查及处置依据，不能直接删点。", "数据、图表及异常调查记录"),
    ("temperature-unknown-length", "clarify", "温度偏了两度，这个零件会差多少？", "需确认材料热膨胀系数、基准长度及参考温度等条件，不能只凭两度给误差。", "材料、长度及参考温度"),
    ("risk-alpha-beta-ambiguous", "clarify", "抽样风险就设成5%，可以定方案了吗？", "需明确是生产者还是消费者风险，以及相应质量水平和其他方案条件。", "风险类型及对应质量水平"),
    ("roundness-trace-attribution", "clarify", "请判断我这条圆度曲线有多少微米来自工件，多少来自主轴。", "需先提供实际曲线、转位测量及仪器装夹校准信息，不能在没有数据时编造各自贡献的微米数。", "曲线、转位数据及仪器装夹校准信息"),
]
