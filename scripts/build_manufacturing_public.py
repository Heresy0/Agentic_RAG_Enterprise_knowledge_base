"""Acquire official NIST sources and build an isolated, reproducible public pack.

No application database, uploads or model calls. Existing outputs are never
overwritten. Raw HTML is archived outside the upload directory.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urljoin, urlsplit

import requests
from lxml import html

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.manufacturing_public_sources import PDF_SOURCES, WEB_SOURCES, DEFERRED_SOURCES, EDITION

MAX_BYTES = 20 * 1024 * 1024
CONVERSION_VERSION = "nist-html-v1"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def checked_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not (parts.hostname == "nist.gov" or (parts.hostname or "").endswith(".nist.gov")) or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("Only reviewed HTTPS NIST sources are allowed")
    return url


def fetch(url):
    for _ in range(6):
        response = requests.get(checked_url(url), headers={"User-Agent": "EnterpriseKnowledgeCorpus/2.0 (+https://github.com/Heresy0/Multi-tenant-long-context-rag-engine)"},
                                timeout=(30, 60), allow_redirects=False, stream=True)
        with response:
            if response.is_redirect:
                url = checked_url(urljoin(url, response.headers["Location"]))
                continue
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Source exceeds the existing application upload limit")
                chunks.append(chunk)
            return b"".join(chunks), url
    raise ValueError("Too many redirects")


def raw_path(cache, source):
    return cache / (source["id"] + (".pdf" if source["kind"] == "public_pdf" else ".html"))


def acquire(cache):
    cache.mkdir(parents=True, exist_ok=True)

    def one(source):
        path = raw_path(cache, source)
        receipt = cache / (source["id"] + ".json")
        if path.exists() and receipt.exists():
            row = json.loads(receipt.read_text(encoding="utf-8"))
            if row["sha256"] != sha(path.read_bytes()) or row["source_url"] != source["source_url"]:
                raise ValueError("Cached source changed: " + source["id"])
            return row
        if source.get("existing_id"):
            original = ROOT / "sample_docs/manufacturing_lite/documents/公共参考/05_NIST量块手册公开参考.pdf"
            raw, final_url = original.read_bytes(), source["source_url"]
        else:
            raw, final_url = fetch(source["source_url"])
        if source["kind"] == "public_pdf" and not raw.startswith(b"%PDF-"):
            raise ValueError("Expected PDF: " + source["id"])
        if source["kind"] == "public_web" and b"<html" not in raw.lower():
            raise ValueError("Expected HTML: " + source["id"])
        path.write_bytes(raw)
        row = dict(**source, final_url=final_url, sha256=sha(raw), bytes=len(raw),
                   retrieved_at=datetime.now(timezone.utc).isoformat())
        dump(receipt, row)
        print(json.dumps(dict(downloaded=source["id"], bytes=len(raw))), flush=True)
        return row

    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(one, source): source for source in [*PDF_SOURCES, *WEB_SOURCES]}
        for future in as_completed(pending):
            source = pending[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                errors.append(dict(id=source["id"], source_url=source["source_url"], error_type=type(exc).__name__,
                                   http_status=exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response is not None else None))
    rows.sort(key=lambda row: row["id"])
    dump(cache / "acquisition.json", dict(edition=EDITION, sources=rows, errors=errors, deferred=DEFERRED_SOURCES))
    if errors:
        raise ValueError("Acquisition incomplete; inspect the recorded source errors and retry without replacing successful files")
    return rows


def html_to_markdown(raw, source):
    """Preserve text/list order and data tables; retain links to image-only facts."""
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = raw  # lxml honors the source's declared legacy encoding.
    tree = html.fromstring(decoded, base_url=source["source_url"])
    for element in tree.xpath("//comment()|//script|//style|//iframe|//noscript"):
        element.drop_tree()
    body = tree.find("body")
    if body is None:
        body = tree

    def clean(value):
        return re.sub(r"[\t\r\n ]+", " ", value or "")

    def render(element):
        tag = element.tag.lower() if isinstance(element.tag, str) else ""
        if tag == "img":
            alt = clean(element.get("alt", "")).strip()
            url = urljoin(source["source_url"], element.get("src", ""))
            if urlsplit(url).path.rsplit("/", 1)[-1] in {"nvgtbr.gif", "nvgbrbtm.gif"}:
                return ""
            return f"\n![{alt or '原文图示，文字解析未识别其内容'}]({url})\n"
        if tag == "br":
            return "\n"
        if tag == "table" and (element.get("border", "0") not in ("", "0") or element.xpath("./thead")):
            rows = element.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
            cells = [[clean(" ".join(cell.itertext())).strip().replace("|", "\\|") for cell in row.xpath("./th|./td")] for row in rows]
            if cells and len(cells[0]) >= 2 and not element.xpath(".//table"):
                width = max(map(len, cells))
                lines = ["| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in cells]
                lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
                return "\n\n" + "\n".join(lines) + "\n\n"
        if tag == "pre":
            return "\n\n```text\n" + "".join(element.itertext()) + "\n```\n\n"
        content = clean(element.text)
        for child in element:
            content += render(child) + clean(child.tail)
        if re.fullmatch(r"h[1-6]", tag):
            return "\n\n" + "#" * min(int(tag[1]) + 1, 6) + " " + content.strip() + "\n\n"
        if tag == "li":
            return "\n- " + content.strip() + "\n"
        if tag in ("p", "div", "tr", "td", "th", "ul", "ol", "table", "blockquote"):
            return "\n\n" + content.strip() + "\n\n"
        return content

    content = re.sub(r"\n[ \t]+", "\n", render(body))
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    content = re.sub(r"(?m)^(#{2,6}) ([0-9]+(?:\.[0-9]+)*\.?)\n\n\1 ([^\n]+)", r"\1 \2 \3", content)
    prefix = f"# {source['title']}\n\n来源：{source['source_url']}\n\n资料性质：NIST公开技术参考，不是模拟企业批准的工艺要求。\n\n转换说明：2026-10-28 将官方HTML转为Markdown，保留原始HTML；图像链接不代表已识别图像内容。\n\n{source['attribution']}\n\n"
    return prefix + content + "\n"


def build(cache, output, lock):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose an empty output directory; existing corpora are never overwritten")
    acquisition = json.loads((cache / "acquisition.json").read_text(encoding="utf-8"))
    locked = json.loads(lock.read_text(encoding="utf-8"))["sources"]
    pinned = {row["id"]: row for row in locked}
    canonical = {row["id"]: row for row in [*PDF_SOURCES, *WEB_SOURCES]}
    acquired = {row["id"]: row for row in acquisition["sources"]}
    if (len(pinned) != len(locked) or len(acquired) != len(acquisition["sources"])
            or set(pinned) != set(canonical) or set(acquired) != set(canonical) or acquisition.get("errors")):
        raise ValueError("Source lock must contain the reviewed 44-source inventory")
    for source in acquisition["sources"]:
        if any(source.get(key) != value or pinned[source["id"]].get(key) != value
               for key, value in canonical[source["id"]].items()):
            raise ValueError("Source metadata differs from the reviewed catalog")
        checked_url(source["final_url"])
        if pinned[source["id"]]["sha256"] != sha(raw_path(cache, source).read_bytes()):
            raise ValueError("Source hash differs from reviewed lock: " + source["id"])
    base = ROOT / "sample_docs/manufacturing_lite"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    entries = []
    for source in manifest["documents"]:
        src = base / source["file"]
        if sha(src.read_bytes()) != source["sha256"]:
            raise ValueError("Frozen baseline changed")
        dst = output / source["file"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        entries.append(dict(**source, split="development", source_family="manufacturing-lite-v1:" + source["id"], baseline=True))
    for source in acquisition["sources"]:
        if source.get("existing_id"):
            # Count the existing handbook once; attach the same canonical family.
            entry = next(row for row in entries if row["id"] == source["existing_id"])
            entry.update(source_family=source["source_family"], public_source_id=source["id"])
            continue
        name = source["id"] + "_" + re.sub(r"[^A-Za-z0-9-]+", "_", source["title"]).strip("_")
        relative = "documents/公共参考/" + name + "." + source["format"]
        dst = output / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        raw = raw_path(cache, source).read_bytes()
        dst.write_bytes(raw if source["format"] == "pdf" else html_to_markdown(raw, source).encode())
        if source["kind"] == "public_web":
            original = output / "originals" / (source["id"] + ".html")
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(raw)
        entries.append({**source, "file": relative, "sha256": sha(dst.read_bytes()), "bytes": dst.stat().st_size,
                        "source_kind": "public_original" if source["format"] == "pdf" else "public_web_conversion",
                        "raw_sha256": source["sha256"], "conversion_version": CONVERSION_VERSION if source["format"] == "md" else None,
                        "features": ["公开原始PDF", "历史版面", "OCR必需"] if source["format"] == "pdf" else ["官方网页转Markdown", "原始HTML保留"]})
    from collections import Counter
    result = dict(edition=EDITION, total_documents=len(entries), public_documents=44, synthetic_documents=16,
                  source_pdf_count=20, independent_web_entries=24, web_parent_handbooks=1,
                  counts=dict(Counter(row["format"] for row in entries)), group_counts=dict(Counter(row["group"] for row in entries)),
                  source_lock_sha256=sha(lock.read_bytes()), baseline_manifest_sha256=sha((base / "manifest.json").read_bytes()), documents=entries)
    dump(output / "manifest.json", result)
    return {key: value for key, value in result.items() if key != "documents"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("acquire", "build"))
    parser.add_argument("--cache", type=Path, default=ROOT / "tmp/manufacturing-public-v2/raw")
    parser.add_argument("--output", type=Path, default=ROOT / "sample_docs/manufacturing_public_v2")
    parser.add_argument("--source-lock", type=Path, default=ROOT / "sample_docs/manufacturing_public_v2.sources.json")
    args = parser.parse_args(argv)
    result = acquire(args.cache) if args.action == "acquire" else build(args.cache, args.output, args.source_lock)
    print(json.dumps(dict(acquired=len(result)) if isinstance(result, list) else result, ensure_ascii=False))


if __name__ == "__main__":
    main()
