# Kinds of Lot Acceptance Sampling Plans

来源：https://www.itl.nist.gov/div898/handbook/pmc/section2/pmc22.htm

资料性质：NIST公开技术参考，不是模拟企业批准的工艺要求。

转换说明：2026-10-28 将官方HTML转为Markdown，保留原始HTML；图像链接不代表已识别图像内容。

Source: National Institute of Standards and Technology / National Bureau of Standards, U.S. Department of Commerce. No endorsement implied.

6. Process or Product Monitoring and Control 
6.2. Test Product for Acceptability: Lot Acceptance Sampling 

### 6.2.2. What kinds of Lot Acceptance Sampling Plans (LASPs) are there?

LASP is a sampling scheme and a set of rules

A lot acceptance sampling plan (LASP) is a sampling scheme and a set of rules for making decisions. The decision, based on counting the number of defectives in a sample, can be to accept the lot, reject the lot, or even, for multiple or sequential sampling schemes, to take another sample and then repeat the decision process.

Types of acceptance plans to choose from

LASPs fall into the following categories: 

- Single sampling plans: One sample of items is selected at random from a lot and the disposition of the lot is determined from the resulting information. These plans are usually denoted as (\(n,c\)) plans for a sample size \(n\), where the lot is rejected if there are more than \(c\) defectives. These are the most common (and easiest) plans to use although not the most efficient in terms of average number of samples needed.

- Double sampling plans: After the first sample is tested, there are three possibilities: 

- Accept the lot

- Reject the lot

- No decision

If the outcome is (3), and a second sample is taken, the procedure is to combine the results of both samples and make a final decision based on that information.

- Multiple sampling plans:  This is an extension of the double sampling plans where more than two samples are needed to reach a conclusion. The advantage of multiple sampling is smaller sample sizes.

- Sequential sampling plans:  This is the ultimate extension of multiple sampling where items are selected from a lot one at a time and after inspection of each item a decision is made to accept or reject the lot or select another unit.

- Skip lot sampling plans: Skip lot sampling means that only a fraction of the submitted lots are inspected.

Definitions of basic Acceptance Sampling terms

Deriving a plan, within one of the categories listed above, is discussed in the pages that follow. All derivations depend on the properties you want the plan to have. These are described using the following terms: 

- Acceptable Quality Level (AQL): The AQL is a percent defective that is the base line requirement for the quality of the producer's product. The producer would like to design a sampling plan such that there is a high probability of accepting a lot that has a defect level less than or equal to the AQL.

- Lot Tolerance Percent Defective (LTPD): The LTPD is a designated high defect level that would be unacceptable to the consumer. The consumer would like the sampling plan to have a low probability of accepting a lot with a defect level as high as the LTPD.

- Type I Error (Producer's Risk): This is the probability, for a given (\(n,c\)) sampling plan, of rejecting a lot that has a defect level equal to the AQL. The producer suffers when this occurs, because a lot with acceptable quality was rejected. The symbol \(\alpha\) is commonly used for the Type I error and typical values for \(\alpha\) range from 0.2 to 0.01.

- Type II Error (Consumer's Risk): This is the probability, for a given (\(n,c\)) sampling plan, of accepting a lot with a defect level equal to the LTPD. The consumer suffers when this occurs, because a lot with unacceptable quality was accepted. The symbol \(\beta\) is commonly used for the Type II error and typical values range from 0.2 to 0.01.

- Operating Characteristic (OC) Curve: This curve plots the probability of accepting the lot (Y-axis) versus the lot fraction or percent defectives (X-axis). The OC curve is the primary tool for displaying and investigating the properties of a LASP.

- Average Outgoing Quality (AOQ): A common procedure, when sampling and testing is non-destructive, is to 100 % inspect rejected lots and replace all defectives with good units. In this case, all rejected lots are made perfect and the only defects left are those in lots that were accepted. AOQs refer to the long term defect level for this combined LASP and 100 % inspection of rejected lots process. If all lots come in with a defect level of exactly \(p\), and the OC curve for the chosen (\(n,c\)) LASP indicates a probability \(p_a\) of accepting such a lot, over the long run the AOQ can easily be shown to be: $$ \mbox{AOQ} = \frac{p_a p (N - n)}{N} \, ,$$ where \(N\) is the lot size.

- Average Outgoing Quality Level (AOQL): A plot of the AOQ (Y-axis) versus the incoming lot \(p\) (X-axis) will start at 0 for \(p=0\), and return to 0 for \(p = 1\) (where every lot is 100 % inspected and rectified). In between, it will rise to a maximum. This maximum, which is the worst possible long term AOQ, is called the AOQL.

- Average Total Inspection (ATI): When rejected lots are 100 % inspected, it is easy to calculate the ATI if lots come consistently with a defect level of \(p\). For a LASP (\(n,c\)) with a probability \(p_a\) of accepting a lot with defect level \(p\), we have $$ \mbox{ATI} = n + (1-p_a)(N-n) \, , $$ where \(N\) is the lot size.

- Average Sample Number (ASN): For a single sampling LASP (\(n,c\)) we know each and every lot has a sample of size \(n\) can be calculated assuming all lots come in with a defect level of \(p\). A plot of the ASN, versus the incoming defect level \(p\), describes the sampling efficiency of a given LASP scheme.

The final choice is a tradeoff decision

Making a final choice between single or multiple sampling plans that have acceptable properties is a matter of deciding whether the average sampling savings gained by the various multiple sampling plans justifies the additional complexity of these plans and the uncertainty of not knowing how much sampling and inspection will be done on a day-by-day basis.
