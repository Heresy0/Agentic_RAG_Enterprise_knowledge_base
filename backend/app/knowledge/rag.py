import hashlib
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from ..config import Settings
from ..db.models import EMBEDDING_DIMENSION
from ..documents.document_splitter import split_docx
from ..documents.pdf_splitter import split_pdf
from ..documents.text_splitter import split_markdown, split_txt
from .audited_embeddings import AuditedEmbeddings


CHUNKING_VERSION = "structured-v4"


def split_file(
    file_path: str | Path,
    *,
    document_name: str | None = None,
) -> list[Document]:
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在：{file_path}")

    if path.suffix.lower() == ".docx":
        return split_docx(
            path,
            document_name=document_name,
        )

    if path.suffix.lower() == ".pdf":
        return split_pdf(
            path,
            document_name=document_name,
        )

    if path.suffix.lower() in {".txt", ".md"}:
        split = split_markdown if path.suffix.lower() == ".md" else split_txt
        return split(path, document_name=document_name)

    raise ValueError(f"暂不支持该文件类型：{path.suffix.lower()}")


def create_embeddings(
    settings: Settings,
) -> Embeddings:
    """创建用于入库和查询的嵌入模型。"""
    return AuditedEmbeddings(OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=settings.chat_api_key,
        base_url=settings.chat_base_url,
        dimensions=EMBEDDING_DIMENSION,
        check_embedding_ctx_length=False,
        chunk_size=10,
        max_retries=0,
    ))


def calculate_file_hash(
    file_path: str | Path,
) -> str:
    """计算文件内容的SHA-256。"""
    path = Path(file_path).resolve()
    hasher = hashlib.sha256()

    with path.open("rb") as file:
        while data := file.read(1024 * 1024):
            hasher.update(data)

    return hasher.hexdigest()


def calculate_source_id(
    file_path: str | Path,
) -> str:
    """根据正式文件路径生成稳定的文档来源标识。"""
    path = Path(file_path).resolve()
    return hashlib.sha256(
        str(path).lower().encode("utf-8")
    ).hexdigest()
