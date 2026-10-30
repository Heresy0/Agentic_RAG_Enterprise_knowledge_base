# Type B evaluations

来源：https://www.itl.nist.gov/div898/handbook/mpc/section5/mpc54.htm

资料性质：NIST公开技术参考，不是模拟企业批准的工艺要求。

转换说明：2026-10-28 将官方HTML转为Markdown，保留原始HTML；图像链接不代表已识别图像内容。

Source: National Institute of Standards and Technology / National Bureau of Standards, U.S. Department of Commerce. No endorsement implied.

2. Measurement Process Characterization 
2.5. Uncertainty analysis 

### 2.5.4. Type B evaluations

Type B evaluations apply to both error and bias

Type B evaluations can apply to both random error and bias. The distinguishing feature is that the calculation of the uncertainty component is not based on a statistical analysis of data. The distinction to keep in mind with regard to random error and bias is that: 

- random errors cannot be corrected

- biases can, theoretically at least, be corrected or eliminated from the result.

Sources of type B evaluations

Some examples of sources of uncertainty that lead to type B evaluations are: 

- Reference standards calibrated by another laboratory

- Physical constants used in the calculation of the reported value

- Environmental effects that cannot be sampled

- Possible configuration/geometry misalignment in the instrument

- Lack of resolution of the instrument

Documented sources of uncertainty from other processes

Documented sources of uncertainty, such as calibration reports for reference standards or published reports of uncertainties for physical constants, pose no difficulties in the analysis. The uncertainty will usually be reported as an expanded uncertainty, U, which is converted to the standard uncertainty, 

u = U/k  

If the k factor is not known or documented, it is probably conservative to assume that k = 2.

Sources of uncertainty that are local to the measurement process

Sources of uncertainty that are local to the measurement process but which cannot be adequately sampled to allow a statistical analysis require type B evaluations. One technique, which is widely used, is to estimate the worst-case effect, a, for the source of interest, from 

- experience

- scientific judgment

- scant data

A standard deviation, assuming that the effect is two-sided, can then be computed based on a uniform, triangular, or normal distribution of possible effects. 

Following the Guide to the Expression of Uncertainty of Measurement (GUM), the convention is to assign infinite degrees of freedom to standard deviations derived in this manner.
