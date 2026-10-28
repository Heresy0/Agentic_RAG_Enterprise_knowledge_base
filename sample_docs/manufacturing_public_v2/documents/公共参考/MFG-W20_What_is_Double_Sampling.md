# What is Double Sampling

来源：https://www.itl.nist.gov/div898/handbook/pmc/section2/pmc24.htm

资料性质：NIST公开技术参考，不是模拟企业批准的工艺要求。

转换说明：2026-10-28 将官方HTML转为Markdown，保留原始HTML；图像链接不代表已识别图像内容。

Source: National Institute of Standards and Technology / National Bureau of Standards, U.S. Department of Commerce. No endorsement implied.

6. Process or Product Monitoring and Control 
6.2. Test Product for Acceptability: Lot Acceptance Sampling 

### 6.2.4. What is Double Sampling?

Double Sampling Plans

How double sampling plans work

Double and multiple sampling plans were invented to give a questionable lot another chance. For example, if in double sampling the results of the first sample are not conclusive with regard to accepting or rejecting, a second sample is taken. Application of double sampling requires that a first sample of size \(n_1\) is taken at random from the (large) lot. The number of defectives is then counted and compared to the first sample's acceptance number \(a_1\) and rejection number \(r_1\). Denote the number of defectives in sample 1 by \(d_1\) and in sample 2 by \(d_2\), then: 

If \(d_1 \le a_1\), the lot is accepted. 
If \(d_1 \ge r_1\), the lot is rejected. 
If \(a_1 < d_1 < r_1\), a second sample is taken.

If a second sample of size \(n_2\) is taken, the number of defectives, \(d_2\), is counted. The total number of defectives is \(D_2 = d_1 + d_2\). Now this is compared to the acceptance number \(a_2\) and the rejection number \(r_2\) of sample 2. In double sampling, \(r_2 = a_2 + 1\) to ensure a decision on the sample. 

If \(D_2 \le a_2\), the lot is accepted. 
If \(D_2 \ge r_2\), the lot is rejected.

Design of a Double Sampling Plan

Design of a double sampling plan

The parameters required to construct the OC curve are similar to the single sample case. The two points of interest are (\(p_1, \, 1-\alpha\)) and (\(p_2, \, \beta\)), where \(p_1\) is the lot fraction defective for plan 1 and \(p_2\) is the lot fraction defective for plan 2. As far as the respective sample sizes are concerned, the second sample size must be equal to, or an even multiple of, the first sample size. 

There exist a variety of tables that assist the user in constructing double and multiple sampling plans. The index to these tables is the \(p_2 / p_1\) ratio, where \(p_2 > p_1\). One set of tables, taken from the Army Chemical Corps  Engineering Agency for \(\alpha = 0.05\) and \(\beta = 0.10\), is given below:

|  | accept |  | approximation | values |
| --- | --- | --- | --- | --- |
| \(R = \) | numbers |  | of \(p n_1\) | for |
| \(p_2 / p_1\) | \(c_1\) | \(c_2\) | \(P = 0.95\) | \(P = 0.10\) |
|  |  |  |  |  |
| 11.90 | 0 | 1 | 0.21 | 2.50 |
| 7.54 | 1 | 2 | 0.52 | 3.92 |
| 6.79 | 0 | 2 | 0.43 | 2.96 |
| 5.39 | 1 | 3 | 0.76 | 4.11 |
| 4.65 | 2 | 4 | 1.16 | 5.39 |
| 4.25 | 1 | 4 | 1.04 | 4.42 |
| 3.88 | 2 | 5 | 1.43 | 5.55 |
| 3.63 | 3 | 6 | 1.87 | 6.78 |
| 3.38 | 2 | 6 | 1.72 | 5.82 |
| 3.21 | 3 | 7 | 2.15 | 6.91 |
| 3.09 | 4 | 8 | 2.62 | 8.10 |
| 2.85 | 4 | 9 | 2.90 | 8.26 |
| 2.60 | 5 | 11 | 3.68 | 9.56 |
| 2.44 | 5 | 12 | 4.00 | 9.77 |
| 2.32 | 5 | 13 | 4.35 | 10.08 |
| 2.22 | 5 | 14 | 4.70 | 10.45 |
| 2.12 | 5 | 16 | 5.39 | 11.41 |

|  | accept |  | approximation | values |
| --- | --- | --- | --- | --- |
| \(R =\) | numbers |  | of \(pn_1\) | for |
| \(p_2 / p_1\) | \(c_1\) | \(c_2\) | \(P = 0.95\) | \(P = 0.10\) |
|  |  |  |  |  |
| 14.50 | 0 | 1 | 0.16 | 2.32 |
| 8.07 | 0 | 2 | 0.30 | 2.42 |
| 6.48 | 1 | 3 | 0.60 | 3.89 |
| 5.39 | 0 | 3 | 0.49 | 2.64 |
| 5.09 | 0 | 4 | 0.77 | 3.92 |
| 4.31 | 1 | 4 | 0.68 | 2.93 |
| 4.19 | 0 | 5 | 0.96 | 4.02 |
| 3.60 | 1 | 6 | 1.16 | 4.17 |
| 3.26 | 1 | 8 | 1.68 | 5.47 |
| 2.96 | 2 | 10 | 2.27 | 6.72 |
| 2.77 | 3 | 11 | 2.46 | 6.82 |
| 2.62 | 4 | 13 | 3.07 | 8.05 |
| 2.46 | 4 | 14 | 3.29 | 8.11 |
| 2.21 | 3 | 15 | 3.41 | 7.55 |
| 1.97 | 4 | 20 | 4.75 | 9.35 |
| 1.74 | 6 | 30 | 7.45 | 12.96 |

Example

Example of a double sampling plan

We wish to construct a double sampling plan according to 

\(p_1 = 0.01\)     \(p_2 = 0.05\)     \(\beta = 0.10\)     \(n_1 = n_2\).

The plans in the corresponding table are indexed on the ratio $$ R = p_2 / p_1 = 5 \, . $$ We find the row whose \(R\) is closet to 5. This is the 5th row (\(R = 4.65\)). This gives \(c_1 = 2\) and \(c_2=4\). The value of \(n_1\) is determined from either of the two columns labeled \(pn_1\). 

The left holds \(\alpha\) constant at 0.05 (\(P = 0.95 = 1-\alpha\)) and the right holds \(\beta\) constant at 0.10 (\(P = 0.10\)). Then holding \(\alpha\) constant, we find \(pn_1 = 1.16\), so \(n_1 = 1.16 / p_1 = 116\). And, holding \(\beta\) constant, we find \(pn_1 = 5.39\), so \(n_1 = 5.39 / p_2 = 108\). Thus the desired sampling plan is

\(n_1 = 108\)     \(c_1 = 2\)     \(n_2 = 108\)     \(c_2 = 4\).

If we opt for \(n_2 = 2n_1\), and follow the same procedure using the appropriate table, the plan is: 

\(n_1 = 77\)     \(c_1 = 1\)     \(n_2 = 154\)     \(c_2 = 4\).

The first plan needs less samples if the number of defectives in sample 1 is greater than 2, while the second plan needs less samples if the number of defectives in sample 1 is less than 2.

ASN Curve for a Double Sampling Plan

Construction of the ASN curve

Since when using a double sampling plan the sample size depends on whether or not a second sample is required, an important consideration for this kind of sampling is the Average Sample Number (ASN) curve. This curve plots the ASN versus \(p'\), the true fraction defective in an incoming lot. 

We will illustrate how to calculate the ASN curve with an example. Consider a double-sampling plan \(n_1 = 50\), \(c_1 = 2\), \(n_2 = 100\), \(c_2 = 6\), where \(n_1\) is the sample size for plan 1, with accept number \(c_1\), and \(n_2, \, c_2\) are the sample size and accept number, respectively, for plan 2.

Let \(p' = 0.06\). Then the probability of acceptance on the first sample, which is the chance of getting two or less defectives, is 0.416 (using binomial tables). The probability of rejection on the second sample, which is the chance of getting more than six defectives, is (1-0.971) = 0.029. The probability of making a decision on the first sample is 0.445, equal to the sum of 0.416 and 0.029. With complete inspection of the second sample, the average size sample is equal to the size of the first sample times the probability that there will be only one sample plus the size of the combined samples times the probability that a second sample will be necessary. For the sampling plan under consideration, the ASN with complete inspection of the second sample for a \(p' = 0.06\) is $$ 50(0.445) + 150(0.555) = 106 \, . $$ The general formula for an average sample number curve of a double-sampling plan with complete inspection of the second sample is $$ \mbox{ASN} = n_1 P_1 + (n_1 + n_2)(1-P_1) = n_1 + n_2(1-P_1) \, . $$ where \(P_1\) is the probability of a decision on the first sample. The graph below shows a plot of the ASN versus \(p'\).

The ASN curve for a double sampling plan

![Plot of Average Sample Number versus Lot Fraction Defective](https://www.itl.nist.gov/div898/handbook/pmc/section2/gifs/asn.gif)
