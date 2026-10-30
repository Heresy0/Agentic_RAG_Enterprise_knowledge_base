"""Prepare a NEW demo tenant only; dry-run by default, no Keycloak/model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.manufacturing_corpus_data import GROUPS, ROLE_PERMISSIONS


def validated_subjects(subjects):
    if set(subjects) != set(ROLE_PERMISSIONS):
        raise ValueError("须提供全部五个演示角色对应的 Keycloak sub")
    values = list(subjects.values())
    if any(not isinstance(value, str) or not value.strip() or value != value.strip()
           or len(value) > 255 or value.startswith("REPLACE_") for value in values):
        raise ValueError("请先替换示例中的占位 sub，不可使用空值或首尾空白")
    if len(set(values)) != len(values):
        raise ValueError("五个角色必须对应不同的身份")
    return subjects


def create_demo(session, *, tenant_name, subjects):
    """Caller owns the transaction. Refuse updates to any existing identity/tenant."""
    from sqlalchemy import select
    from backend.app.db.models import KnowledgeBase, KnowledgeBaseUserGrant, Tenant, User

    subjects = validated_subjects(subjects)
    if not tenant_name.strip() or len(tenant_name.strip()) > 200:
        raise ValueError("企业名称不能为空且须在200字符内")
    if session.scalar(select(Tenant.id).where(Tenant.name == tenant_name.strip())) is not None:
        raise ValueError("只允许新建演示企业；同名企业已存在，不会修改")
    if session.scalar(select(User.id).where(User.external_subject.in_(subjects.values()))) is not None:
        raise ValueError("至少一个身份已映射到现有用户，不会移动或覆盖")
    tenant = Tenant(name=tenant_name.strip())
    session.add(tenant)
    session.flush()
    bases = {group: KnowledgeBase(tenant_id=tenant.id, name=group,
             visibility="company" if group == "公共参考" else "restricted") for group in GROUPS}
    session.add_all(bases.values())
    session.flush()
    for role, subject in subjects.items():
        user = User(tenant_id=tenant.id, external_subject=subject, name=role)
        session.add(user)
        session.flush()
        for group, permission in ROLE_PERMISSIONS[role].items():
            session.add(KnowledgeBaseUserGrant(tenant_id=tenant.id, user_id=user.id,
                        knowledge_base_id=bases[group].id, permission=permission))
    session.flush()
    return dict(tenant_id=str(tenant.id), knowledge_bases={name: str(kb.id) for name, kb in bases.items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subjects", type=Path, required=True, help="JSON object: role -> existing Keycloak sub")
    parser.add_argument("--tenant-name", default="衡川精密制造演示")
    parser.add_argument("--apply", action="store_true", help="Explicitly create a new tenant in configured DATABASE_URL")
    args = parser.parse_args(argv)
    subjects = validated_subjects(json.loads(args.subjects.read_text(encoding="utf-8-sig")))
    if not args.apply:
        print(json.dumps(dict(mode="dry_run_no_database_access", tenant_name=args.tenant_name,
                              groups=GROUPS, roles=ROLE_PERMISSIONS, next_step="核对身份映射后显式使用 --apply"), ensure_ascii=False, indent=2))
        return 0
    from backend.app.config import required_env
    from backend.app.db.session import create_database_engine, create_session_factory
    engine = create_database_engine(required_env("DATABASE_URL"))
    try:
        with create_session_factory(engine)() as session:
            with session.begin():
                result = create_demo(session, tenant_name=args.tenant_name, subjects=subjects)
    finally:
        engine.dispose()
    print(json.dumps(dict(mode="created", **result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
