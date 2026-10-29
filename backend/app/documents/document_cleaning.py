"""Conservative, structure-preserving cleaning and content-free audit records.

Format adapters identify structure; clean_document is the common pre-split
entry. OCR/image enhancement and semantic rewriting are outside this module.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
import math
import re
import unicodedata

from .chunk_quality import CLEANING_VERSION, classify_chunk

_PAGE_NUMBER_PATTERN = re.compile(
    r"^(?:第\s*\d+\s*页|[-—–]\s*\d+\s*[-—–]|"
    r"page\s+\d+(?:\s+of\s+\d+)?|虚构测试资料\s+第\s*\d+\s*页)$", re.I,
)


def _digest(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def cleaning_trace(before, after, *, profile, rules=(), scope=None, counts=None):
    """Hashes refer to extracted text/structure, never to an entire source file."""
    record = dict(version=CLEANING_VERSION, profile=profile, scope=scope or {},
                  input_sha256=_digest(before), output_sha256=_digest(after),
                  changed=before != after, applied_rules=list(rules), counts=counts or {})
    return dict(id=_digest(record), **record)


def clean_text(text: str, *, profile: str, scope=None):
    original, rules = text, []
    def apply(name, value):
        nonlocal text
        if value != text:
            rules.append(name)
            text = value
    if profile == 'pdf_text':
        apply('unicode_nfkc', unicodedata.normalize('NFKC', text))
        apply('remove_nul', text.replace('\x00', ''))
        apply('remove_soft_hyphen', text.replace('\u00ad', ''))
        apply('join_english_hyphenation', re.sub(r'(?<=[A-Za-z])-\s*\n\s*(?=[A-Za-z])', '', text))
        apply('normalize_crlf', text.replace('\r\n', '\n'))
        apply('normalize_horizontal_space', '\n'.join(re.sub(r'[\t \u3000]+', ' ', line).strip() for line in text.split('\n')))
        apply('strip_edges', text.strip())
    elif profile == 'text_file':
        apply('remove_utf8_bom', text.removeprefix('\ufeff'))
        apply('normalize_newlines', text.replace('\r\n', '\n').replace('\r', '\n'))
        apply('remove_nul', text.replace('\x00', ''))
    elif profile == 'table_cell':
        apply('normalize_cell_whitespace', re.sub(r'\s+', ' ', text).strip())
    elif profile == 'prose':
        apply('strip_edges', text.strip())
    elif profile != 'literal':
        raise ValueError('Unknown cleaning profile')
    return text, cleaning_trace(original, text, profile=profile, rules=rules, scope=scope)


def clean_table(cells, *, scope=None):
    """Keep cell positions and empty cells; retain the existing blank-row rule."""
    rows, normalized, changed_cells, empty_cells, dropped_rows = [], [], 0, 0, 0
    for row in cells:
        values = []
        for cell in row:
            empty_cells += cell is None
            text, trace = clean_text(cell or '', profile='table_cell')
            changed_cells += trace['changed']
            values.append(text)
        if any(values):
            rows.append('|'.join(values))
            normalized.append(values)
        else:
            dropped_rows += 1
    rules = []
    if changed_cells:
        rules.append('normalize_cell_whitespace')
    if empty_cells:
        rules.append('normalize_empty_cells')
    if dropped_rows:
        rules.append('drop_empty_table_rows')
    return rows, cleaning_trace(cells, normalized, profile='table_cells', scope=scope,
        rules=rules, counts=dict(changed_cells=changed_cells, normalized_empty_cells=empty_cells,
                                dropped_empty_rows=dropped_rows))


def clean_document(units, *, source_format, upstream_traces=()):
    """Clean identified blocks before splitting, without flattening structures.

    ContentUnit and PdfUnit share kind/content/metadata fields. Dataclass copies
    keep the adapters' page, section, parser and format-specific metadata.
    """
    cleaned = []
    for index, unit in enumerate(units):
        metadata = dict(unit.metadata)
        traces = list(metadata.pop('_cleaning_traces', ()))
        for trace in upstream_traces:
            page = trace['scope'].get('page')
            if page is None or not hasattr(unit, 'page_start') or unit.page_start <= page <= unit.page_end:
                traces.append(trace)
        profile = 'literal' if unit.kind in ('table', 'code') else 'prose'
        scope = dict(format=source_format, unit=index, kind=unit.kind)
        if isinstance(unit.content, str):
            content, trace = clean_text(unit.content, profile=profile, scope=scope)
        else:
            content = list(unit.content)
            trace = cleaning_trace(unit.content, content, profile='structured_table', scope=scope)
        traces.append(trace)
        metadata['cleaning'] = dict(version=CLEANING_VERSION, block_input_sha256=trace['input_sha256'],
            block_output_sha256=trace['output_sha256'], traces=list({t['id']:t for t in traces}.values()))
        cleaned.append(replace(unit, content=content, metadata=metadata))
    return cleaned


def summarize_cleaning(chunks):
    # A parent block/page/file trace is repeated on its child chunks. Count it once.
    traces = {t['id']:t for chunk in chunks for t in chunk.metadata.get('cleaning', {}).get('traces', ())}
    return dict(version=CLEANING_VERSION, trace_count=len(traces),
                changed_stages=sum(t['changed'] for t in traces.values()),
                applied_rules=dict(Counter(rule for t in traces.values() for rule in t['applied_rules'])))


def _normalize_marginal_line(line):
    return re.sub(r'\s+', '', line.strip().lower())


def _safe_repeated_margin(line, document_name=''):
    if len(line) > 80 or re.search(
        r'\d|[|。！？；;:：]|必须|应当|不得|禁止|限额|上限|触发|应在|需要|条件|例外|'
        r'生效|失效|有效期|版本|审批人|负责人|签署|验收结果|不应|不能|不可|不自动|'
        r'仅|只有|如果|若|允许|可以|须|要求|不代表|不证明|确认|认定|已|重试|幂等', line,
    ):
        return False
    if classify_chunk(line, dict(document_name=document_name, section_path='')) == 'front_matter':
        return True
    title = re.sub(r'\.(?:pdf|docx?|md|txt)$', '', document_name, flags=re.I)
    title = _normalize_marginal_line(re.sub(r'^\d+[_、.\s-]+', '', title))
    normalized = _normalize_marginal_line(line)
    prefix = normalized[:-len(title)] if title and normalized.endswith(title) else ''
    return bool(prefix and re.fullmatch(r'[\w·&-]+(?:科技|公司|有限公司)', prefix))


def remove_repeated_marginalia(pages, *, document_name=''):
    normalized_counts = Counter()
    for page in pages:
        lines = [line for line in page.text.splitlines() if line]
        normalized_counts.update({_normalize_marginal_line(line) for line in lines[:2] + lines[-2:]
                                  if _safe_repeated_margin(line, document_name)})
    required = max(2, math.ceil(len(pages) * 0.6))
    repeated = {line for line, count in normalized_counts.items() if line and count >= required}
    cleaned = []
    for page in pages:
        lines = page.text.splitlines()
        nonempty = [i for i, line in enumerate(lines) if line]
        edges = set(nonempty[:2] + nonempty[-2:])
        footers = set(nonempty[-2:])
        kept = [line for i, line in enumerate(lines) if not (
            (i in edges and _safe_repeated_margin(line, document_name) and _normalize_marginal_line(line) in repeated)
            or (i in footers and _PAGE_NUMBER_PATTERN.fullmatch(line.strip())
                and int(re.search(r'\d+', line).group()) == page.number))]
        text = '\n'.join(kept).strip()
        trace = cleaning_trace(page.text, text, profile='pdf_marginalia', scope=dict(format='pdf', page=page.number),
            rules=['remove_safe_marginalia'] if len(kept) != len(lines) else [],
            counts=dict(removed_lines=len(lines)-len(kept)))
        cleaned.append(replace(page, text=text, cleaning_traces=[*page.cleaning_traces, trace]))
    return cleaned
