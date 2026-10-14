"""Conservative extractive selection inside an already authorized parent.

No fetch, model, summarization or business-version adjudication. Ambiguous,
complex and dependency questions retain the complete bounded parent.
"""
import re

from langchain_core.documents import Document

from .query_planner import current_query_plan


def _terms(question):
    text = re.sub(r'是什么|有哪些|是多少|是否|请问|说明|如何|怎么|什么|多少|公司|项目|规定', '', question)
    words = set(re.findall(r'[A-Za-z][A-Za-z0-9_-]{2,}', text))
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def select_parent_context(document, question):
    content, metadata = document.page_content, document.metadata
    if metadata.get('chunk_type') != 'parent' or not question:
        return document
    plan = current_query_plan()
    # Preserve bridges and version comparisons rather than using local relevance
    # as proof that a multi-hop dependency is unnecessary.
    complex_query = bool((plan and (plan.strategy == 'agentic' or plan.subquestions))
        or re.search(r'比较|对比|分别|差异|相同|跨|先.*再|根据.*(?:判断|核对)|[？?；;].+\S', question))
    headings = list(re.finditer(r'^## .+$', content, re.M))
    anchor = str(metadata.get('matched_child_section') or '')
    terms = _terms(question)
    reason = 'complex_question' if complex_query else 'conservative_fallback'
    selected = content
    spans = [(0, len(content))]
    if not complex_query and len(content) >= 650 and len(headings) >= 3 and len(terms) >= 3 and anchor:
        sections = [(match.start(), headings[i + 1].start() if i + 1 < len(headings) else len(content),
                     match.group()[3:].strip()) for i, match in enumerate(headings)]
        anchors = [i for i, (_, _, title) in enumerate(sections) if title == anchor]
        if anchors:
            keep = set(anchors)
            for i, (start, end, title) in enumerate(sections):
                body = content[start:end]
                # Conditions, tables, negative facts and applicability remain
                # visible, even when they are not lexically similar to the query.
                if (sum(term in body for term in terms) >= 2
                        or re.search(r'适用|范围|版本|生效|边界|例外|条件|定义', title)
                        or '|' in body
                        or re.search(r'不得|禁止|尚未|不能|不代表|未确认|冲突|争议', body)):
                    keep.add(i)
            # Keep the exact anchor body and first section's document preamble.
            spans = [(start, end) for i, (start, end, _) in enumerate(sections) if i in keep]
            if headings[0].start():
                spans.insert(0, (0, headings[0].start()))
            selected = '\n\n'.join(content[start:end].strip() for start, end in spans)
            child = str(metadata.get('matched_child_content') or '').strip()
            if not child or child not in selected or len(selected) >= len(content):
                selected, spans = content, [(0, len(content))]
            else:
                reason = 'anchor_and_related_sections'
    return Document(id=document.id, page_content=selected, metadata={**metadata,
        'parent_selection': dict(reason=reason, original_characters=len(content),
                                 selected_characters=len(selected), spans=spans)})
