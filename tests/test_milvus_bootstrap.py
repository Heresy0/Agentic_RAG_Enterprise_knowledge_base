from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.bootstrap_milvus import configure_services, authenticate_admin, publish_service_token, READ_PRIVILEGES, WRITE_PRIVILEGES
from scripts.initialize_milvus_secrets import initialize
from backend.app.knowledge.milvus_store import VectorStoreUnavailable


def test_secret_generation_preserves_existing_credentials(tmp_path):
    assert len(initialize(tmp_path)) == 3
    before = {p.name: p.read_text() for p in tmp_path.iterdir()}
    assert initialize(tmp_path) == []
    assert before == {p.name: p.read_text() for p in tmp_path.iterdir()}
    assert all(len(value.partition(":")[2].strip()) >= 40 for value in before.values())


def test_bootstrap_refuses_anonymous_server_before_mutations(monkeypatch):
    clients = []
    def create(*args):
        result = Mock()
        clients.append(result)
        return result
    monkeypatch.setattr("scripts.bootstrap_milvus.client", create)
    with pytest.raises(RuntimeError, match="尚未开启认证"):
        authenticate_admin("http://milvus", "chunks", "root:random-secret", allow_default_root=True)
    clients[0]._call.assert_not_called()


def test_rotation_retries_configured_credential_first_and_never_prints_password(monkeypatch):
    configured, anonymous, default = Mock(), Mock(), Mock()
    configured._call.side_effect = [VectorStoreUnavailable("Milvus 操作失败"), []]
    anonymous._call.side_effect = VectorStoreUnavailable("Milvus 操作失败")
    monkeypatch.setattr("scripts.bootstrap_milvus.client", Mock(side_effect=[configured, anonymous, default]))
    assert authenticate_admin("http://milvus", "chunks", "root:random-secret", allow_default_root=True) is configured
    assert default._call.call_args_list[-1].kwargs == {
        "userName": "root", "password": "Milvus", "newPassword": "random-secret"}


@pytest.mark.parametrize("drift", [
    {"objectType": "Collection", "dbName": "default", "objectName": "*", "privilege": "Query"},
    {"objectType": "Collection", "dbName": "default", "objectName": "chunks", "privilege": "Delete"},
    {"objectType": "Collection", "dbName": "other_db", "objectName": "chunks", "privilege": "Query"},
    {"objectType": "Global", "dbName": "default", "objectName": "chunks", "privilege": "Query"},
])
def test_existing_broader_role_fails_closed(drift):
    admin = SimpleNamespace(uri="http://milvus", collection="chunks", _call=Mock(side_effect=[
        ["kb_api_read", "kb_worker_write"], ["kb_api", "kb_worker"], [drift]]))
    with pytest.raises(RuntimeError, match="超出预期"):
        configure_services(admin, {"api": "kb_api:secret", "worker": "kb_worker:secret"})
    assert all("grant" not in call.args[0] for call in admin._call.call_args_list)


def test_read_account_never_receives_mutation_privileges(monkeypatch):
    admin = SimpleNamespace(uri="http://milvus", collection="chunks", _call=Mock())
    def call(operation, **kwargs):
        return {"roles/list": ["kb_api_read", "kb_worker_write"], "users/list": ["kb_api", "kb_worker"],
                "roles/describe": [], "users/describe": []}.get(operation, {})
    admin._call.side_effect = call
    monkeypatch.setattr("scripts.bootstrap_milvus.client", Mock(return_value=Mock()))
    configure_services(admin, {"api": "kb_api:secret", "worker": "kb_worker:secret"})
    grants = [c.kwargs for c in admin._call.call_args_list if c.args[0] == "roles/grant_privilege_v2"]
    assert {g["privilege"] for g in grants if g["roleName"] == "kb_api_read"} == READ_PRIVILEGES
    assert {g["privilege"] for g in grants if g["roleName"] == "kb_worker_write"} == WRITE_PRIVILEGES
    assert all(g["dbName"] == "default" and g["collectionName"] == "chunks" for g in grants)


def test_failed_secret_publication_keeps_previous_token_and_cleans_temporary_file(tmp_path, monkeypatch):
    chown = Mock()
    monkeypatch.setattr("scripts.bootstrap_milvus.os.chown", chown, raising=False)
    monkeypatch.setattr("scripts.bootstrap_milvus.os.chmod", Mock())
    publish_service_token(tmp_path, "reader:first-secret", uid=999, gid=999)
    chown.side_effect = [None, OSError("simulated ownership failure")]
    with pytest.raises(OSError):
        publish_service_token(tmp_path, "reader:second-secret", uid=999, gid=999)
    assert (tmp_path / "token").read_text().strip() == "reader:first-secret"
    assert [path.name for path in tmp_path.iterdir()] == ["token"]
