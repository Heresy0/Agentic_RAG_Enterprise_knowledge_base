"""Reviewed official source inventory for the public-first manufacturing pack.

The NIST e-Handbook pages are independent entries of ONE handbook, not 24 books.
Manufacturer candidates with unresolved reuse terms are recorded but not fetched.
"""
EDITION = "manufacturing-public-v2"
TERMS_URL = "https://www.nist.gov/copyrights-disclaimers"
ATTRIBUTION = "Source: National Institute of Standards and Technology / National Bureau of Standards, U.S. Department of Commerce. No endorsement implied."
GAGE_INDEX = "https://www.nist.gov/pml/sensor-science/dimensional-metrology/selected-publications-dimensional-metrology-gage-blocks"
OTHER_INDEX = "https://www.nist.gov/pml/sensor-science/dimensional-metrology/selected-publications-dimensional-metrology-other"
UNCERTAINTY_INDEX = "https://www.nist.gov/pml/sensor-science/dimensional-metrology/selected-publications-dimensional-metrology-uncertainty-and"
ASSURANCE_INDEX = "https://www.nist.gov/pml/sensor-science/dimensional-metrology/selected-publications-dimensional-metrology-measurement"


def pdf(number, filename, title, year, index, *, split="test"):
    archive = {
        "NISTJResNov-Dec97-102-6.pdf": "jres/102/6/j26doi.pdf",
        "NISTTechnicalNote1297s.pdf": "legacy/tn/nbstechnicalnote1297.pdf",
        "NBSReport10-987.pdf": "Legacy/RPT/nbsreport10987.pdf",
    }.get(filename)
    if archive is None:
        category = "IR" if filename.startswith("NBSIR") else "TN" if filename.startswith("NBSTechnical") else "MONO"
        archive = "Legacy/" + category + "/" + filename.lower()
    return dict(id=f"MFG-P{number:02}", title=title, kind="public_pdf", format="pdf", group="公共参考",
                source_url="https://nvlpubs.nist.gov/nistpubs/" + archive,
                alternate_url="https://emtoolbox.nist.gov/Publications/" + filename,
                landing_url=index, publication_year=year, source_family=filename.removesuffix(".pdf"),
                split=split, terms_url=TERMS_URL, attribution=ATTRIBUTION,
                usage="historical_technical_reference_not_company_approved_procedure")


PDF_SOURCES = [
    dict(id="MFG-P01", title="The Gage Block Handbook", kind="public_pdf", format="pdf", group="公共参考",
         source_url="https://nvlpubs.nist.gov/nistpubs/Legacy/MONO/nistmonograph180.pdf", landing_url=GAGE_INDEX,
         publication_year=1995, source_family="NIST-MONO-180-1995", split="development", terms_url=TERMS_URL,
         attribution=ATTRIBUTION, usage="historical_technical_reference_not_company_approved_procedure",
         existing_id="MFG-05"),
    pdf(2, "NBSTechnicalNote962.pdf", "Contact Deformation in Gage Block Comparisons", 1978, GAGE_INDEX),
    pdf(3, "NBSIR74-523.pdf", "Preparations for Gage Block Comparison Measurements", 1974, GAGE_INDEX, split="development"),
    pdf(4, "NBSIR76-979.pdf", "Intercomparison Procedures for Gage Blocks Using Electromechanical Comparators", 1976, GAGE_INDEX, split="development"),
    pdf(5, "NISTJResNov-Dec97-102-6.pdf", "Uncertainty and Dimensional Calibrations", 1997, UNCERTAINTY_INDEX, split="development"),
    pdf(6, "NISTTechnicalNote1297s.pdf", "Guidelines for Evaluating and Expressing the Uncertainty of NIST Measurement Results", 1994, UNCERTAINTY_INDEX, split="development"),
    pdf(7, "NBSTechnicalNote1248.pdf", "Traceability of Laser Interferometric Length Measurements", 1988, UNCERTAINTY_INDEX),
    pdf(8, "NBSIR73-272.pdf", "On Characterizing Master Involute Profiles", 1973, OTHER_INDEX),
    pdf(9, "NBSIR73-136.pdf", "Measurements of Cylindrical Standards", 1973, OTHER_INDEX),
    pdf(10, "NBSIR73-239.pdf", "Gage Block Flatness and Parallelism Measurement", 1973, OTHER_INDEX),
    pdf(11, "NBSIR73-243.pdf", "On the Compression of a Cylinder in Contact with a Plane Surface", 1973, OTHER_INDEX),
    pdf(12, "NBSIR74-532.pdf", "A Method of Calibrating Two-dimensional Reference Plates", 1974, OTHER_INDEX),
    pdf(13, "NBSIR79-1758.pdf", "The Calibration of a Roundness Standard", 1979, OTHER_INDEX),
    pdf(14, "NBSIR75-750.pdf", "The Calibration of Indexing Tables by Subdivision", 1975, OTHER_INDEX),
    pdf(15, "NBSIR76-993.pdf", "The Calibration of a Pentaprism", 1976, OTHER_INDEX),
    pdf(16, "NBSIR80-1967.pdf", "The Calibration of Angle Blocks by Intercomparison", 1980, OTHER_INDEX),
    pdf(17, "NBSIR75-975.pdf", "The Calibration of an Optical Flat by Interferometric Comparison to a Master Optical Flat", 1975, OTHER_INDEX),
    pdf(18, "NBSMonograph149.pdf", "Measurement Assurance Program A Case Study Length Measurements", 1975, ASSURANCE_INDEX),
    pdf(19, "NBSMonograph163.pdf", "Measurement Assurance for Gage Blocks", 1979, ASSURANCE_INDEX),
    pdf(20, "NBSIR74-545.pdf", "Notes on the Fundamentals of Measurement and Measurement as a Production Process", 1974, ASSURANCE_INDEX),
]


def web(number, path, title, *, split="test"):
    chapter = path.split("/")[0]
    return dict(id=f"MFG-W{number:02}", title=title, kind="public_web", format="md", group="公共参考",
                source_url="https://www.itl.nist.gov/div898/handbook/" + path,
                source_family="NIST-eHandbook-" + chapter, split=split, terms_url=TERMS_URL,
                attribution=ATTRIBUTION, usage="technical_reference_not_company_approved_procedure")


WEB_SOURCES = [
    web(1, "mpc/section1/mpc11.htm", "What are the issues for characterization", split="development"),
    web(2, "mpc/section1/mpc12.htm", "What is a check standard", split="development"),
    web(3, "mpc/section2/mpc21.htm", "Issues in controlling the measurement process", split="development"),
    web(4, "mpc/section2/mpc22.htm", "How are bias and variability controlled", split="development"),
    web(5, "mpc/section2/mpc23.htm", "How is short-term variability controlled", split="development"),
    web(6, "mpc/section3/mpc31.htm", "Issues in calibration", split="development"),
    web(7, "mpc/section5/mpc53.htm", "Type A evaluations", split="development"),
    web(8, "mpc/section5/mpc54.htm", "Type B evaluations", split="development"),
    web(9, "ppc/section1/ppc11.htm", "What is Production Process Characterization"),
    web(10, "ppc/section1/ppc12.htm", "What are PPC studies used for"),
    web(11, "ppc/section3/ppc33.htm", "Define Sampling Plan"),
    web(12, "ppc/section4/ppc46.htm", "Assessing Process Capability"),
    web(13, "pmc/section1/pmc12.htm", "What are Process Control Techniques"),
    web(14, "pmc/section1/pmc13.htm", "What is Process Control"),
    web(15, "pmc/section1/pmc14.htm", "What to do if the process is Out of Control"),
    web(16, "pmc/section1/pmc15.htm", "What to do if In Control but Unacceptable"),
    web(17, "pmc/section1/pmc16.htm", "What is Process Capability"),
    web(18, "pmc/section2/pmc21.htm", "What is Acceptance Sampling"),
    web(19, "pmc/section2/pmc22.htm", "Kinds of Lot Acceptance Sampling Plans"),
    web(20, "pmc/section2/pmc24.htm", "What is Double Sampling"),
    web(21, "pmc/section2/pmc25.htm", "What is Multiple Sampling"),
    web(22, "pmc/section2/pmc26.htm", "What is a Sequential Sampling Plan"),
    web(23, "pmc/section2/pmc27.htm", "What is Skip Lot Sampling"),
    web(24, "pmc/section3/pmc31.htm", "What are Control Charts"),
]

DEFERRED_SOURCES = [
    dict(publisher="NBS", url="https://emtoolbox.nist.gov/Publications/NBSReport10-987.pdf", status="alternate_archive_unavailable", downloaded=False,
         reason="Toolbox TLS connection failed; canonical NIST archive candidate returned 404. Replaced by NBSIR73-272, not counted as acquired."),
    dict(publisher="Mitutoyo", url="https://www.mitutoyo.com/literature/quick-guide-to-precision-measuring-instruments/", status="reuse_terms_pending", downloaded=False),
    dict(publisher="Renishaw", url="https://www.renishaw.com/resourcecentre/en/details/Installation-guide-OMP40-2-optical-machine-probe--141394?lang=en", status="reuse_terms_pending", downloaded=False),
    dict(publisher="Sandvik Coromant", url="https://www.sandvik.coromant.cn/en-gb/knowledge", status="reuse_terms_pending", downloaded=False),
]
