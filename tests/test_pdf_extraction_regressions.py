"""Reproduce layout and silent OCR loss without document-specific parser rules."""
import json
from pathlib import Path

from pypdf import PdfWriter
import pytest

from backend.app.documents import pdf_splitter as pdf


def word(text, x0, x1, top, bottom, size=12):
    return dict(text=text,x0=x0,x1=x1,top=top,bottom=bottom,size=size)


class Page:
    width=600
    def __init__(self,words): self.words=words
    def extract_words(self,**kwargs): return self.words


def test_long_single_column_lines_cannot_be_split_by_their_word_centers():
    words=[word('完整正文前半行',80,520,20,32),
           word('第一条',110,160,45,57),word('条款正文',180,520,45,57),
           word('接续短行。',80,160,70,82),
           word('第二条',110,160,95,107),word('另一条款正文',180,520,95,107)]
    assert pdf._extract_reading_order_text(Page(words)).splitlines()==[
        '完整正文前半行','第一条 条款正文','接续短行。','第二条 另一条款正文']


def test_real_columns_keep_reading_order_when_there_is_an_empty_gutter():
    words=[word('左栏第一行',60,200,20,32),word('右栏第一行',360,500,20,32),
           word('左栏第二行',60,200,45,57),word('右栏第二行',360,500,45,57)]
    assert pdf._extract_reading_order_text(Page(words)).splitlines()==[
        '左栏第一行','左栏第二行','右栏第一行','右栏第二行']


def test_subscript_stays_between_its_symbol_and_following_body():
    words=[word('标距',60,84,20,32),word('l',90,94,21,33),
           word('0',94,99,26,34,8),word('为夹持位置。',101,180,20,32),
           word('下一行正文。',60,144,45,57)]
    text=pdf._extract_reading_order_text(Page(words))
    assert text.splitlines()==['标距 l 0 为夹持位置。','下一行正文。']


@pytest.mark.parametrize('line',[
    '3 次，取平均值作为测量结果。',
    '5 个点。然后沿受力轴线逐点递增标准力值。',
    '6.3.1 在各测量点重复测量并记录结果。',
])
def test_numbered_sentence_is_retained_as_body(line):
    units=pdf.collect_pdf_units([pdf.ExtractedPage(1,'6 检查方法\n'+line,'pypdf')])
    assert any(line in str(u.content) for u in units)
    assert all(line not in u.section_path for u in units)


@pytest.mark.parametrize('text,expected',[
    ('5.2.3 如需测量拉向力,需要安装配套装\n置。','5.2.3 如需测量拉向力,需要安装配套装置。'),
    ('[[PDF_HEADING_1]]融合的发展目标和规划部署,优化质量方针,制\n定管理目标。',
     '融合的发展目标和规划部署,优化质量方针,制定管理目标。'),
    ('[[PDF_HEADING_1]]3 次。重复测量各个点,取其最大\n值。','3 次。重复测量各个点,取其最大值。'),
    ('(十四)加强资料管理。企业应将资料纳入数\n据管理范畴。','(十四)加强资料管理。企业应将资料纳入数据管理范畴。'),
])
def test_wrapped_body_cannot_be_lost_to_decimal_lists_or_font_heading_markers(text,expected):
    units=pdf.collect_pdf_units([pdf.ExtractedPage(1,'5 检查方法\n'+text,'pypdf')])
    assert len(units)==1
    assert units[0].content==expected
    assert units[0].section_path==('5 检查方法',)


def test_empty_ocr_page_cannot_silently_publish_other_pages(tmp_path,monkeypatch):
    path=tmp_path/'scan.pdf'
    writer=PdfWriter()
    for _ in range(2): writer.add_blank_page(width=600,height=800)
    writer.write(path)
    monkeypatch.setattr(pdf,'_page_has_images',lambda page:True)
    with pytest.raises(ValueError,match='OCR.*未提取'):
        pdf.split_pdf(path,ocr_page=lambda path,number:'' if number==1 else '扫描正文仅在第二页成功识别。')


def test_genuinely_blank_native_page_does_not_require_ocr(tmp_path):
    path=tmp_path/'blank.pdf'
    writer=PdfWriter()
    writer.add_blank_page(width=600,height=800)
    writer.write(path)
    assert pdf.split_pdf(path)==[]


def test_blocked_pdf_is_recorded_and_cached_as_blocked_not_a_parse_success(tmp_path,monkeypatch):
    from scripts import validate_manufacturing_public as audit
    corpus=tmp_path/'corpus'
    corpus.mkdir()
    (corpus/'manifest.json').write_text('{}')
    monkeypatch.setattr(audit,'inventory',lambda _:{'documents':[
        dict(id='scan',file='scan.pdf',format='pdf',sha256='test-source')]})
    calls=[]
    def blocked(_):
        calls.append(True)
        raise pdf.PdfTextExtractionError([2,3])
    monkeypatch.setattr(pdf,'split_pdf',blocked)
    cache=tmp_path/'parsed'
    result=audit.parse(corpus,cache,ocr=True,workers=1)
    assert result['summary']=={'blocked':1}
    saved=json.loads((cache/'scan.json').read_text(encoding='utf-8'))
    assert saved['chunks']==[] and saved['record']['page_numbers']==[2,3]
    assert saved['record']['reason']=='ocr_text_missing'
    assert audit.parse(corpus,cache,ocr=True,workers=1)['summary']=={'blocked':1}
    assert calls==[True]


@pytest.mark.parametrize('source,number,anchor',[
    ('MFG-ZH01',6,'未发现问题的,也应当予以记录,实行零风险报告。'),
    ('MFG-ZH08',8,'3次,取平均值作为测量结果'),
    ('MFG-ZH17',7,'l0为引伸计的夹持位置,此项不需校准。'),
])
def test_original_failure_pages_keep_clause_and_subscript(source,number,anchor):
    pytest.importorskip('pdfplumber')
    from scripts.evaluate_answers import normalize
    root=Path(__file__).resolve().parents[1]/'sample_docs/manufacturing_zh_v3'
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    entry=next(d for d in manifest['documents'] if d['id']==source)
    import pdfplumber
    with pdfplumber.open(root/entry['file']) as document:
        text=pdf._extract_reading_order_text(document.pages[number-1])
    units=pdf.collect_pdf_units([pdf.ExtractedPage(number,pdf.normalize_pdf_text(text),'pdfplumber')])
    assert any(normalize(anchor) in normalize(str(u.content)) for u in units)
