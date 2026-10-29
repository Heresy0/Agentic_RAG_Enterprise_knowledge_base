"""Acquire/build the native Chinese edition without DB access or model calls."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlsplit

import requests
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_manufacturing_public import dump, sha
from scripts.manufacturing_zh_sources import SOURCES, DEFERRED, EDITION, ALLOWED_HOSTS

MAX_BYTES = 20 * 1024 * 1024
CONVERSION = "official-zh-html-v1"
BASELINES = ("sample_docs/manufacturing_lite", "sample_docs/manufacturing_public_v2",
             "evals/manufacturing_lite", "evals/manufacturing_public_v2")


def baseline_fingerprints():
    files = [p for folder in BASELINES for p in (ROOT / folder).rglob('*') if p.is_file()]
    files.append(ROOT / 'sample_docs/manufacturing_public_v2.sources.json')
    return {p.relative_to(ROOT).as_posix(): sha(p.read_bytes()) for p in sorted(files)}


def guard_output(path):
    target=path.resolve()
    if target==ROOT or any(target.is_relative_to((ROOT/b).resolve()) for b in BASELINES):
        raise ValueError('Output must not modify an existing baseline')


def checked_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in ALLOWED_HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Only reviewed official HTTPS hosts are allowed')
    return url


def fetch(url):
    for _ in range(6):
        with requests.get(checked_url(url), timeout=(30, 60), allow_redirects=False, stream=True,
                          headers={'User-Agent': 'EnterpriseKnowledgeCorpus/3.0'}) as response:
            if response.is_redirect:
                url = checked_url(urljoin(url, response.headers['Location']))
                continue
            response.raise_for_status()
            pieces, size = [], 0
            for part in response.iter_content(65536):
                size += len(part)
                if size > MAX_BYTES:
                    raise ValueError('Source exceeds the application upload limit')
                pieces.append(part)
            return b''.join(pieces), url
    raise ValueError('Too many redirects')


def raw_path(cache, source):
    return cache / (source['id'] + ('.pdf' if source['format'] == 'pdf' else '.html'))


def acquire(cache):
    cache.mkdir(parents=True, exist_ok=True)
    def one(source):
        path, receipt = raw_path(cache, source), cache / (source['id']+'.json')
        if path.exists() or receipt.exists():
            saved = json.loads(receipt.read_text(encoding='utf-8'))
            if any(saved.get(k) != v for k, v in source.items()) or saved['sha256'] != sha(path.read_bytes()):
                raise ValueError('Cached source changed: '+source['id'])
            return saved
        raw, final = fetch(source['source_url'])
        if source['format'] == 'pdf' and not raw.startswith(b'%PDF-'):
            raise ValueError('Expected a complete original PDF: '+source['id'])
        if source['kind'] == 'public_web' and b'<html' not in raw.lower():
            raise ValueError('Expected the official HTML article: '+source['id'])
        path.write_bytes(raw)
        row = {**source, 'final_url': final, 'sha256': sha(raw), 'bytes': len(raw),
               'retrieved_at': datetime.now(timezone.utc).isoformat(),
               'rights_status': 'official_public_access; no blanket open-redistribution licence asserted',
               'use_note': '研究与评测参考；保留署名和来源。公开发行全文前须另核复用条件。',
               'validity_status': 'locked_document_edition; latest_applicability_not_certified'}
        dump(receipt, row)
        print(json.dumps({'downloaded': source['id'], 'bytes': len(raw)}), flush=True)
        return row
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(one, s): s for s in SOURCES}
        for future in as_completed(pending):
            try:
                rows.append(future.result())
            except Exception as exc:
                errors.append(dict(id=pending[future]['id'], error=type(exc).__name__))
    dump(cache/'acquisition.json', dict(edition=EDITION, sources=sorted(rows,key=lambda s:s['id']), errors=errors, deferred=DEFERRED))
    if errors:
        raise ValueError('Acquisition incomplete; successful downloads are retained for a safe retry')
    return rows


def html_to_markdown(raw, source):
    tree = html.fromstring(raw.decode('utf-8-sig'), base_url=source['source_url'])
    found = tree.xpath(source['content_xpath'])
    if len(found) != 1:
        raise ValueError('The reviewed article body selector must match exactly once: '+source['id'])
    root = found[0]
    for bad in root.xpath('.//script|.//style|.//iframe|.//noscript|.//comment()'):
        bad.drop_tree()
    def clean(value):
        return re.sub(r'\s+', ' ', value or '')
    def render(node):
        tag = node.tag.lower() if isinstance(node.tag,str) else ''
        if tag == 'table':
            # Expand merged cells rather than silently shifting column meaning.
            grid, spans = [], {}
            for tr in node.xpath('./tr|./thead/tr|./tbody/tr|./tfoot/tr'):
                cells, col = [], 0
                def inherited():
                    nonlocal col
                    while col in spans:
                        value, remaining = spans[col]
                        cells.append(value)
                        if remaining <= 1: del spans[col]
                        else: spans[col] = (value, remaining-1)
                        col += 1
                for td in tr.xpath('./td|./th'):
                    inherited()
                    value = clean(' '.join(td.itertext())).strip().replace('|', '\\|')
                    colspan, rowspan = int(td.get('colspan','1')), int(td.get('rowspan','1'))
                    for _ in range(colspan):
                        cells.append(value)
                        if rowspan > 1: spans[col] = (value, rowspan-1)
                        col += 1
                inherited()
                if any(cells): grid.append(cells)
            if not grid: return ''
            width = max(map(len,grid))
            lines = ['| '+' | '.join(row+['']*(width-len(row)))+' |' for row in grid]
            lines.insert(1, '| '+' | '.join(['---']*width)+' |')
            return '\n\n'+'\n'.join(lines)+'\n\n'
        if tag == 'img':
            return '\n![原文图示，未据链接推断图像事实]('+urljoin(source['source_url'],node.get('src',''))+')\n'
        if tag == 'br': return '\n'
        value = clean(node.text)
        for child in node: value += render(child)+clean(child.tail)
        if re.fullmatch(r'h[1-6]',tag): return '\n\n'+'#'*min(int(tag[1])+1,6)+' '+value.strip()+'\n\n'
        if tag == 'li': return '\n- '+value.strip()+'\n'
        if tag in ('p','div','ul','ol','section'): return '\n\n'+value.strip()+'\n\n'
        return value
    body = re.sub(r'\n[ \t]+','\n',render(root))
    body = re.sub(r'\n{3,}','\n\n',body).strip()
    if len(re.findall(r'[\u4e00-\u9fff]',body)) < 500:
        raise ValueError('Article body is incomplete or is only an attachment notice')
    return (f"# {source['title']}\n\n来源：{source['source_url']}\n\n发布机构：{source['publisher']}\n\n"
            "资料性质：官方中文公开原文，作为知识参考，不替代企业批准的作业文件。\n\n"
            "转换说明：从官网正文转为 Markdown，保留原始 HTML；合并表格单元格重复展开。图片未自动转写。\n\n"
            +body+'\n')


def build(cache, output, lock):
    guard_output(output); guard_output(lock)
    if lock.resolve().is_relative_to((output/'documents').resolve()) or cache.resolve().is_relative_to(output.resolve()):
        raise ValueError('Raw cache and source lock must stay outside the upload directory')
    if lock.exists() or (output.exists() and any(output.iterdir())):
        raise ValueError('Choose a new edition/output; frozen corpora and locks are never overwritten')
    guard = baseline_fingerprints()
    acquisition = json.loads((cache/'acquisition.json').read_text(encoding='utf-8'))
    canonical = {r['id']:r for r in SOURCES}
    rows = acquisition['sources']
    if acquisition.get('errors') or len(rows) != 18 or {r['id'] for r in rows} != set(canonical):
        raise ValueError('Expected all 18 reviewed originals without acquisition errors')
    documents, hashes = [], set()
    for row in rows:
        if any(row.get(k) != v for k,v in canonical[row['id']].items()):
            raise ValueError('Unreviewed source metadata')
        checked_url(row['source_url']); checked_url(row['final_url'])
        raw = raw_path(cache,row).read_bytes()
        if row['sha256'] != sha(raw) or row['bytes'] != len(raw) or row['sha256'] in hashes:
            raise ValueError('Changed, missing or duplicated original')
        hashes.add(row['sha256'])
        payload = raw if row['format']=='pdf' else html_to_markdown(raw,row).encode('utf-8')
        if row['format']=='pdf':
            from pypdf import PdfReader
            reader = PdfReader(raw_path(cache,row))
            if not len(reader.pages): raise ValueError('Empty PDF')
        relative = 'documents/公共参考/'+row['id']+'_'+row['title']+'.'+row['format']
        documents.append({**row, 'file':relative, 'sha256':sha(payload), 'raw_sha256':sha(raw),
                          'bytes':len(payload), 'raw_bytes':len(raw),
                          'physical_pages':len(reader.pages) if row['format']=='pdf' else None,
                          'source_kind':'public_original' if row['format']=='pdf' else 'public_web_conversion',
                          'conversion_version':CONVERSION if row['format']=='md' else None})
        target = output/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(payload)
        if row['kind']=='public_web':
            original = output/'originals'/(row['id']+'.html')
            original.parent.mkdir(parents=True,exist_ok=True)
            original.write_bytes(raw)
    if baseline_fingerprints()!=guard: raise ValueError('Existing baseline changed during build')
    dump(lock, dict(edition=EDITION, source_count=18, sources=rows, deferred=DEFERRED,
                   external_model_calls=0, baseline_fingerprints=guard))
    manifest = dict(edition=EDITION, total_documents=18, public_documents=18, synthetic_documents=0,
                    language='zh-CN', counts=dict(Counter(d['format'] for d in documents)),
                    group_counts={'公共参考':18}, source_lock_sha256=sha(lock.read_bytes()),
                    total_pdf_pages=sum(d['physical_pages'] or 0 for d in documents), documents=documents)
    dump(output/'manifest.json',manifest)
    return {k:v for k,v in manifest.items() if k!='documents'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('acquire','build'))
    p.add_argument('--cache',type=Path,default=ROOT/'tmp/manufacturing-zh-v3/raw')
    p.add_argument('--output',type=Path,default=ROOT/'sample_docs/manufacturing_zh_v3')
    p.add_argument('--source-lock',type=Path,default=ROOT/'sample_docs/manufacturing_zh_v3.sources.json')
    a=p.parse_args()
    result=acquire(a.cache) if a.action=='acquire' else build(a.cache,a.output,a.source_lock)
    print(json.dumps({'acquired':len(result)} if isinstance(result,list) else result,ensure_ascii=False))


if __name__=='__main__': main()
