"""Execute explicitly queued quality evaluations with bounded model calls."""
import argparse
import logging
from pathlib import Path
import signal
import sys
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings
from backend.app.db.session import create_database_engine, create_session_factory
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.retrieval_service import RetrievalService
from backend.app.knowledge.milvus_store import MilvusVectorStore
from backend.app.quality.evaluation_worker import QualityEvaluationWorker


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-model-calls", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_model_calls: parser.error("启动评测 Worker 必须显式指定 --allow-model-calls")
    settings, stopped = Settings(), Event()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if hasattr(signal, "SIGTERM"): signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    engine = create_database_engine(settings.database_url)
    try:
        vectors = MilvusVectorStore(settings); vectors.verify()
        worker = QualityEvaluationWorker(create_session_factory(engine), settings,
            AnswerService(settings=settings, retrieval_service=RetrievalService(settings=settings, vector_store=vectors)))
        while not stopped.is_set():
            worker.run_once()
            if args.once: return 0
            stopped.wait(2)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__": raise SystemExit(main())
