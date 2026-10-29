# OCR小改动离线实验

在实际OCR前冻结两套配置、来源原件、采样页、27处独立检查及3段文字转写。原有55处扫描件证据仅作回归，原有150处证据的分块基线和120题保持不变。

| 配置 | 分辨率 | 首次识别 | 空结果回退 |
| --- | --- | --- | --- |
| baseline | 220 DPI | 现有默认PSM 3，chi_sim+eng | 无 |
| candidate | 300 DPI | 同上 | 仅空白/空字符串时，同一渲染图像再尝试一次PSM 6 |

应用默认值仍为220 DPI、不开启回退。实验通过环境变量`PDF_OCR_DPI`和`PDF_OCR_RETRY_EMPTY`显式启用候选配置；DPI限制为150–400。不会因为输出变长自动认证结果，不按文件名、页码或答案选择识别配置，不进行旋转、二值化、纠偏、去水印或文字猜测替换。

## 冻结与覆盖

- [plan.json](plan.json)：配置、冻结时间、原件目录和标注指纹；两配置均在相同本地镜像运行。
- [annotations.json](annotations.json)：9页独立样本、27处正文/标识/表格单元格/数字单位检查，3段原文转写；另有旧6份扫描件12页、55处既有证据。
- [原件目录](../../sample_docs/ocr_independent_v1/README.md)：3份、37页扫描PDF。9页全部按完整原页视觉复核，手写内容不作评分。

标注由同一助手编写和核对，不是独立专家认证。部分原页倒置/侧置，人工阅读时转正仅帮助核对，OCR仍使用原始方向。这些难例保留，不能为提高分数换页或旋转输入。新样本只有3份，不足以证明广泛泛化能力。

## 指标边界

严格检查只做NFKC与空白归一化，不忽略数字、正负号、百分号或单位错误。表格单元格文字匹配不代表行列关联恢复；需结合原页人工核对。

3段转写使用“参考片段到OCR页面某连续子串的最小字符编辑距离”，未转写的页眉和其他段落不受罚。它只是片段错误率，不是整页CER，也不用于选择或修复OCR输出。

旧55处是OCR页文字检查，不能直接把它的变化加到旧`120/150`分块匹配数上，更不是问答准确率。原有模型问答未运行，资料未入库。

## 复现

使用已有镜像`enterprise-knowledge-manufacturing:20261028-fe7221c`，镜像ID为`sha256:300b3abb4f132f2d5daa5e016ecdd38ed85c9469cc0dcabb73143f01824bdedb`。禁网、只读代码与原件挂载，临时输出目录单独可写；不挂载业务数据或密钥。

在安装真实Tesseract中文语言包和Poppler的同一运行时中，从仓库根目录执行：

```powershell
python scripts/run_ocr_small_experiment.py run --profile baseline --output tmp/ocr-small-experiment-v1/baseline.json
python scripts/run_ocr_small_experiment.py run --profile candidate --output tmp/ocr-small-experiment-v1/candidate.json
python scripts/run_ocr_small_experiment.py score --baseline tmp/ocr-small-experiment-v1/baseline.json --candidate tmp/ocr-small-experiment-v1/candidate.json --output tmp/ocr-small-experiment-v1/comparison.json
```

输出不覆盖旧文件，重复实验需使用新的结果路径。报告见[首轮对照](reports/20261029-offline-r1/README.md)。
