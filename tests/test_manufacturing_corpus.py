"""Actual corpus parsing and existing ACL checks; never call models or business DBs."""
import json
from pathlib import Path
import shutil

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from backend.app.db.base import Base
from backend.app.db.models import KnowledgeBase, KnowledgeBaseUserGrant, Tenant, User
from backend.app.documents.document_splitter import split_docx
from backend.app.documents.pdf_splitter import split_pdf
from backend.app.documents.policy_metadata import extract_policy
from backend.app.documents.ocr_review import apply_reviewed_ocr
from backend.app.documents.text_splitter import split_markdown, split_txt
from backend.app.security.authorization import AuthorizationService
from backend.app.security.principal import Principal
from scripts.build_manufacturing_corpus import build
from scripts.manufacturing_corpus_data import GROUPS, QUESTIONS, ROLE_PERMISSIONS
from scripts.validate_manufacturing_corpus import inventory, normalized
from scripts.provision_manufacturing_demo import create_demo, main as provision_main, validated_subjects

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "sample_docs/manufacturing_lite"
DATASET = ROOT / "evals/manufacturing_lite"


@pytest.fixture(scope="module")
def native_parts():
    result = {}
    for row in inventory(CORPUS)["documents"]:
        if "OCR必需" in row.get("features", []):
            continue
        result[row["id"]] = {"pdf": split_pdf, "docx": split_docx, "md": split_markdown,
                             "txt": split_txt}[row["format"]](CORPUS / row["file"])
    assert len(result) == 15
    return result


def test_source_inventory_and_question_annotations_are_separate_and_hash_bound():
    manifest = inventory(CORPUS)
    assert manifest["total_documents"] == 17
    assert manifest["synthetic_documents"] == 16 and manifest["public_documents"] == 1
    assert manifest["counts"] == {"pdf": 5, "docx": 4, "md": 4, "txt": 4}
    cases = [json.loads(line) for line in (DATASET / "single_turn.jsonl").read_text(encoding="utf-8").splitlines()]
    assert cases == QUESTIONS and len(cases) == 20
    assert sum(not row["answerable"] for row in cases) == 4
    assert all("reference_answer" not in path.read_text(encoding="utf-8-sig")
               for path in (CORPUS / "documents").rglob("*.md"))


def test_numbered_cross_page_conditions_are_not_lost_in_real_pdf(native_parts):
    groups = [part for part in native_parts["MFG-02"] if part.metadata["chunk_type"] == "rule_group"]
    assert any(part.metadata["page_start"] == 1 and part.metadata["page_end"] == 2
               and all(word in part.page_content for word in ("质量人员", "生产班长", "书面批准")) for part in groups)


def test_model_parameter_and_tolerance_rows_keep_their_original_relationships(native_parts):
    for identifier, rows in {
        "MFG-01": ["CAL-150|0至150 mm|0.01 mm|±0.02 mm", "MIC-025|0至25 mm|0.001 mm|±0.003 mm"],
        "MFG-08": ["外径|19.95至20.05 mm，包含上下限|MIC-025", "孔深|12.00至12.10 mm，包含上下限|CAL-150"],
    }.items():
        for expected in rows:
            assert any(normalized(expected) in normalized(part.page_content) for part in native_parts[identifier])
    tables = [part for part in native_parts["MFG-03"] if part.metadata["chunk_type"] == "table"]
    assert len(tables) >= 2
    assert all("项目编号" in part.page_content and "必要备注" in part.page_content for part in tables)


def test_native_word_policy_dates_and_revision_are_extracted_without_new_backend_schema(native_parts):
    old = extract_policy("\n".join(part.page_content for part in native_parts["MFG-06"]))
    new = extract_policy("\n".join(part.page_content for part in native_parts["MFG-07"]))
    assert old["document_code"] == new["document_code"] == "HC-WI-A17-020"
    assert old["business_version"] == "1.0" and old["business_status"] == "历史归档"
    assert old["effective_to"] == "2026-09-30"
    assert new["business_version"] == "2.0" and new["effective_from"] == "2026-10-01"
    assert new["applicability"] == "HC-A17的OP-20工序"


def test_markdown_example_and_txt_bom_do_not_change_identifiers_or_structure(native_parts):
    code = [part for part in native_parts["MFG-11"] if part.metadata["chunk_type"] == "code"]
    assert any("#不是章节|也不是表格" in part.page_content and "记录示例" in part.metadata["section_path"] for part in code)
    text = "\n".join(part.page_content for part in native_parts["MFG-17"])
    assert "CAL-018" in text and "2026年9月30日" in text and "不得用于正式检验" in text
    assert "\ufeff" not in text and "\ufffd" not in text


def test_manifest_rejects_paths_outside_upload_directory(tmp_path):
    copy = tmp_path / "corpus"
    shutil.copytree(CORPUS, copy)
    path = copy / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["documents"][0]["file"] = "../../README.md"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="越界"):
        inventory(copy)


def test_build_refuses_to_replace_existing_files_before_any_network_or_authoring(tmp_path):
    (tmp_path / "keep.txt").write_text("保留已有文件", encoding="utf-8")
    with pytest.raises(ValueError, match="不会被覆盖"):
        build(tmp_path, tmp_path / "evaluation", download_public=True)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "保留已有文件"
    substitute = tmp_path / "substitute.pdf"
    substitute.write_bytes(b"%PDF-" + b"x" * 10_000)
    with pytest.raises(ValueError, match="NIST原件哈希"):
        build(tmp_path / "new-corpus", tmp_path / "new-evaluation", reference_file=substitute)
    assert not (tmp_path / "new-corpus").exists()


def test_gbk_input_is_explicitly_rejected_instead_of_silently_decoded(tmp_path):
    path = tmp_path / "handover.txt"
    path.write_bytes("量具隔离，不得用于检验".encode("gbk"))
    with pytest.raises(UnicodeDecodeError):
        split_txt(path)


def test_scan_correction_is_bound_to_the_visual_reviewed_file_and_exact_phrase():
    scan = next(row for row in inventory(CORPUS)["documents"] if row["id"] == "MFG-04")
    observed = "工单W 0 -017,产品HCAA17,工序0P-20。周末值班"
    corrected, fixes = apply_reviewed_ocr(observed, file_hash=scan["sha256"], page=1)
    assert corrected == "工单WO-017，产品HC-A17，工序OP-20。周末值班"
    assert len(fixes) == 1 and fixes[0]["reviewed_by"] == "codex_visual_check"
    assert apply_reviewed_ocr(observed, file_hash="0" * 64, page=1) == (observed, [])
    assert apply_reviewed_ocr(observed, file_hash=scan["sha256"], page=2) == (observed, [])


def test_provision_dry_run_does_not_connect_to_database(tmp_path, monkeypatch, capsys):
    path = tmp_path / "subjects.json"
    path.write_text(json.dumps({role: f"new-{i}" for i, role in enumerate(ROLE_PERMISSIONS)}), encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert provision_main(["--subjects", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "dry_run_no_database_access"
    with pytest.raises(ValueError, match="占位"):
        validated_subjects(json.loads((DATASET / "subjects.example.json").read_text(encoding="utf-8")))
    with pytest.raises(ValueError, match="不同"):
        validated_subjects({role: "same-subject" for role in ROLE_PERMISSIONS})


def test_provision_creates_isolated_roles_and_refuses_existing_tenant_or_identity():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    subjects = {role: f"new-{i}" for i, role in enumerate(ROLE_PERMISSIONS)}
    try:
        with Session(engine) as session:
            with session.begin():
                result = create_demo(session, tenant_name="新演示企业", subjects=subjects)
            assert set(result["knowledge_bases"]) == set(GROUPS)
            assert session.scalar(select(func.count()).select_from(User)) == 5
            users = session.scalars(select(User)).all()
            for user in users:
                actual = AuthorizationService(session).list_permissions(
                    principal=Principal(user.id, user.tenant_id, user.external_subject))
                assert {str(key): value for key, value in actual.items()} == {
                    result["knowledge_bases"][group]: permission for group, permission in ROLE_PERMISSIONS[user.name].items()}
            with pytest.raises(ValueError, match="同名"):
                create_demo(session, tenant_name="新演示企业", subjects=subjects)
            with pytest.raises(ValueError, match="现有用户"):
                create_demo(session, tenant_name="另一个企业", subjects=subjects)
            assert session.scalar(select(func.count()).select_from(Tenant)) == 1
    finally:
        engine.dispose()


def test_failed_demo_transaction_leaves_no_partial_tenant():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            with pytest.raises(RuntimeError), session.begin():
                create_demo(session, tenant_name="应回滚的企业",
                            subjects={role: f"rollback-{i}" for i, role in enumerate(ROLE_PERMISSIONS)})
                raise RuntimeError("模拟事务失败")
            assert session.scalar(select(func.count()).select_from(Tenant)) == 0
            assert session.scalar(select(func.count()).select_from(User)) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("role", ROLE_PERMISSIONS)
def test_manufacturing_roles_use_existing_authorization_and_revocation(role):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            tenant, other = Tenant(name="制造演示"), Tenant(name="其他企业")
            session.add_all([tenant, other]); session.flush()
            user = User(tenant_id=tenant.id, external_subject="manufacturing-test", name=role)
            session.add(user); session.flush()
            bases = {group: KnowledgeBase(tenant_id=tenant.id, name=group,
                     visibility="company" if group == "公共参考" else "restricted") for group in GROUPS}
            foreign = KnowledgeBase(tenant_id=other.id, name="其他企业资料", visibility="company")
            session.add_all([*bases.values(), foreign]); session.flush()
            for group, permission in ROLE_PERMISSIONS[role].items():
                session.add(KnowledgeBaseUserGrant(tenant_id=tenant.id, user_id=user.id,
                            knowledge_base_id=bases[group].id, permission=permission))
            session.commit()
            principal = Principal(user.id, tenant.id, user.external_subject)
            authorization = AuthorizationService(session)
            assert authorization.list_permissions(principal=principal) == {
                bases[group].id: permission for group, permission in ROLE_PERMISSIONS[role].items()}
            assert not authorization.can_access(principal=principal, knowledge_base_id=foreign.id)
            if role == "操作员":
                assert not authorization.can_access(principal=principal, knowledge_base_id=bases["生产工艺"].id,
                                                     required_permission="editor")
            session.execute(delete(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id == user.id))
            session.commit()
            assert authorization.list_readable_knowledge_base_ids(principal=principal) == {bases["公共参考"].id}
    finally:
        engine.dispose()
