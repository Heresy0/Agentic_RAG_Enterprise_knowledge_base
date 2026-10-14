"""Build bounded extractive parents from current child text; never embed parents."""
import hashlib
import re

from sqlalchemy import delete
from ..db.models import DocumentParent

PARENT_CHUNKING_VERSION = 'section-parents-v1'
PARENT_MAX_CHARACTERS = 1500


def body_text(content):
    return re.sub(r'^文档[:：][^\n]*\n章节[:：][^\n]*\n(?:页码[:：][^\n]*\n)?\s*\n',
                  '', content, count=1).strip()


def _render(rows):
    # Keep table headers and literal code; do not summarize or rewrite facts.
    parts, previous = [], None
    for row in rows:
        section = str((row.metadata_json or {}).get('section_path',''))
        if section and section != previous:
            parts.append('## '+section)
        parts.append(body_text(row.content))
        previous = section
    return '\n\n'.join(parts)


def build_parent_records(document, chunks, *, max_characters=PARENT_MAX_CHARACTERS):
    if not 900 <= max_characters <= 3000:
        raise ValueError('父块长度必须在900至3000字符之间')
    rows = sorted(chunks, key=lambda row: row.chunk_index)
    if not rows:
        return []
    if len({row.vector_generation for row in rows}) != 1:
        raise ValueError('父块不能合并不同索引版本')
    if any((row.tenant_id, row.knowledge_base_id, row.document_id) !=
           (document.tenant_id, document.knowledge_base_id, document.id) for row in rows):
        raise ValueError('父块不能合并不同授权范围')
    groups = []
    if len(_render(rows)) <= max_characters:
        groups = [rows]
    else:
        current = []
        for row in rows:
            section = (row.metadata_json or {}).get('section_path', '')
            if current and (section != (current[-1].metadata_json or {}).get('section_path', '')
                            or len(_render([*current, row])) > max_characters):
                groups.append(current)
                current = []
            # Preserve an oversized child as a child; never truncate its evidence.
            if len(_render([row])) > max_characters:
                row.parent_id = None
                continue
            current.append(row)
        if current:
            groups.append(current)
    parents = []
    for group in groups:
        content = _render(group)
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        identity = ':'.join(map(str, [document.tenant_id, document.knowledge_base_id,
            document.id, document.version, document.content_hash, group[0].vector_generation,
            group[0].chunk_index, group[-1].chunk_index, digest, PARENT_CHUNKING_VERSION]))
        identifier = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        sections = list(dict.fromkeys(str((r.metadata_json or {}).get('section_path', '')) for r in group))
        for row in group:
            row.parent_id = identifier
        parents.append(DocumentParent(id=identifier, tenant_id=document.tenant_id,
            knowledge_base_id=document.knowledge_base_id, document_id=document.id,
            document_version=document.version, document_content_hash=document.content_hash,
            vector_generation=group[0].vector_generation, first_chunk_index=group[0].chunk_index,
            last_chunk_index=group[-1].chunk_index, content=content, content_hash=digest,
            metadata_json={'section_path': sections[0] if len(sections)==1 else document.file_name,
                           'sections': sections, 'parent_chunking_version': PARENT_CHUNKING_VERSION}))
    return parents


def publish_parents(session, document, chunks):
    parents = build_parent_records(document, chunks)
    session.execute(delete(DocumentParent).where(DocumentParent.tenant_id==document.tenant_id,
        DocumentParent.knowledge_base_id==document.knowledge_base_id,
        DocumentParent.document_id==document.id))
    session.add_all(parents)
    document.metadata_json = {**dict(document.metadata_json),
        'parent_chunking_version': PARENT_CHUNKING_VERSION, 'parent_count': len(parents)}
    return len(parents)
