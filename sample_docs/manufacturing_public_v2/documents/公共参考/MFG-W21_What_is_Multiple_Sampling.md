# What is Multiple Sampling

来源：https://www.itl.nist.gov/div898/handbook/pmc/section2/pmc25.htm

资料性质：NIST公开技术参考，不是模拟企业批准的工艺要求。

转换说明：2026-10-28 将官方HTML转为Markdown，保留原始HTML；图像链接不代表已识别图像内容。

Source: National Institute of Standards and Technology / National Bureau of Standards, U.S. Department of Commerce. No endorsement implied.

6. Process or Product Monitoring and Control 
6.2. Test Product for Acceptability: Lot Acceptance Sampling 

### 6.2.5. What is Multiple Sampling?

Multiple Sampling is an extension of the double sampling concept

Multiple sampling is an extension of double sampling. It involves inspection of 1 to \(k\) successive samples as required to reach an ultimate decision. 

Mil-Std 105D suggests \(k=7\) is a good number. Multiple sampling plans are usually presented in tabular form.

Procedure for multiple sampling

The procedure commences with taking a random sample of size \(n_1\) from a large lot of size \(N\) and counting the number of defectives, \(d_1\). 

If \(d_1 \le a_1\), the lot is accepted. 
If \(d_1 \ge r_1\), the lot is rejected. 
If \(a_1 < d_1 < r_1\), another sample is taken.

If subsequent samples are required, the first sample procedure is repeated sample by sample. For each sample, the total number of defectives found at any stage, say stage \(i\), is $$ D_i = \sum_{j=1}^i d_j $$ This is compared with the acceptance number \(a_i\) and the rejection number \(r_i\) for that stage until a decision is made. Sometimes acceptance is not allowed at the early stages of multiple sampling; however, rejection can occur at any stage.

Efficiency measured by the ASN

Efficiency for a multiple sampling scheme is measured by the average sample number (ASN) required for a given Type I and Type II  set of errors. The number of samples needed when following a multiple sampling scheme may vary from trial to trial, and the ASN represents the average of what might happen over many trials with a fixed incoming defect level.
