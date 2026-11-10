"""Preview by default; --apply changes only explicit grants in an existing tenant."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.manufacturing.grants import BusinessGrantConfig, apply_grants, preview_grants


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--operation", choices=("grant", "revoke"), default="grant")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--database-url-env", default="DATABASE_URL")
    args = parser.parse_args(argv)
    if args.config.stat().st_size > 65536:
        raise ValueError("配置文件过大。")
    config = BusinessGrantConfig.model_validate_json(args.config.read_bytes())
    if not args.apply:
        result = preview_grants(config, args.operation)
    else:
        from backend.app.db.session import create_database_engine
        from sqlalchemy.orm import Session
        url = os.environ.get(args.database_url_env)
        if not url:
            raise ValueError("未提供指定数据库环境变量。")
        engine = create_database_engine(url)
        try:
            with Session(engine) as session, session.begin():
                result = apply_grants(session, config, operation=args.operation)
        finally:
            engine.dispose()
    print(json.dumps(result, ensure_ascii=True))
    return result


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(status="failed", error_type=type(exc).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
