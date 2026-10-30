# What are Process Control Techniques

来源：https://www.itl.nist.gov/div898/handbook/pmc/section1/pmc12.htm

资料性质：NIST公开技术参考，不是模拟企业批准的工艺要求。

转换说明：2026-10-28 将官方HTML转为Markdown，保留原始HTML；图像链接不代表已识别图像内容。

Source: National Institute of Standards and Technology / National Bureau of Standards, U.S. Department of Commerce. No endorsement implied.

6. Process or Product Monitoring and Control 
6.1. Introduction 

### 6.1.2. What are Process Control Techniques?

Statistical Process Control (SPC)

Typical process control techniques

There are many ways to implement process control. Key monitoring and investigating tools include: 

- Histograms

- Check Sheets

- Pareto Charts

- Cause and Effect Diagrams

- Defect Concentration Diagrams

- Scatter Diagrams

- Control Charts

All these are described in Montgomery (2000). This chapter will focus (Section 3) on control chart methods, specifically: 

- Classical Shewhart Control charts,

- Cumulative Sum (CUSUM) charts

- Exponentially Weighted Moving Average (EWMA) charts

- Multivariate control charts

Underlying concepts

The underlying concept of statistical process control is based on a comparison of what is happening today with what happened previously. We take a snapshot of how the process typically performs or build a model of how we think the process will perform and calculate control limits for the expected measurements of the output of the process. Then we collect data from the process and compare the data to the control limits. The majority of measurements should fall within the control limits. Measurements that fall outside the control limits are examined to see if they belong to the same population as our initial snapshot or model. Stated differently, we use historical data to compute the initial control limits. Then the data are compared against these initial limits. Points that fall outside of the limits are investigated and, perhaps, some will later be discarded. If so, the limits would be recomputed and the process repeated. This is referred to as Phase I. Real-time process monitoring, using the limits from the end of Phase I, is Phase II.

Statistical Quality Control (SQC)

Tools of statistical quality control

Several techniques can be used to investigate the product for defects or defective pieces after all processing is complete. Typical tools of SQC (described in section 2) are: 

- Lot Acceptance sampling plans

- Skip lot sampling plans

- Military (MIL) Standard sampling plans

Underlying concepts of statistical quality control

The purpose of statistical quality control is to ensure, in a cost efficient manner, that the product shipped to customers meets their specifications. Inspecting every product is costly and inefficient, but the consequences of shipping non conforming product can be significant in terms of customer dissatisfaction. Statistical Quality Control is the process of inspecting enough product from given lots to probabilistically ensure a specified quality level.
