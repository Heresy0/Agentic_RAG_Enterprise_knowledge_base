"""Initialize native authentication, scoped service roles and the collection.

Only the one-shot Compose init service receives the administrator secret.
An existing deployment must supply the current root credential via a file if
it has already changed the vendor default. No credentials enter stdout/errors.
"""
import argparse
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import read_milvus_token_file
from backend.app.knowledge.milvus_store import MilvusVectorStore, VectorStoreUnavailable

READ_PRIVILEGES = {"Search", "Query", "DescribeCollection", "IndexDetail", "GetLoadState"}
WRITE_PRIVILEGES = READ_PRIVILEGES | {"Upsert", "Delete"}
SERVICE_ACCOUNTS = {"api": ("kb_api", "kb_api_read", READ_PRIVILEGES),
                    "worker": ("kb_worker", "kb_worker_write", WRITE_PRIVILEGES)}


def client(uri, collection, token):
    return MilvusVectorStore(SimpleNamespace(milvus_uri=uri, milvus_collection=collection,
        milvus_token=token, milvus_timeout_seconds=30))


def authenticate_admin(uri, collection, target, *, allow_default_root=False, current=None):
    if target.partition(":")[0] != "root" or target == "root:Milvus":
        raise RuntimeError("管理员密钥必须是 root 的非默认凭据")
    store = client(uri, collection, target)
    # Prove that the server rejects anonymous access before any credential change.
    try:
        client(uri, collection, "")._call("users/list")
    except VectorStoreUnavailable:
        pass
    else:
        raise RuntimeError("Milvus 尚未开启认证，拒绝初始化账号")
    try:
        store._call("users/list")
        return store
    except VectorStoreUnavailable:
        if not current and not allow_default_root:
            raise RuntimeError("管理员认证失败；首次初始化需要 --allow-default-root") from None
    old = current or "root:Milvus"
    if old.partition(":")[0] != "root":
        raise RuntimeError("当前管理员密钥必须属于 root")
    previous = client(uri, collection, old)
    previous._call("users/list")
    previous._call("users/update_password", userName="root", password=old.partition(":")[2],
                   newPassword=target.partition(":")[2])
    store._call("users/list")
    return store


def configure_services(admin, tokens):
    roles = set(admin._call("roles/list"))
    users = set(admin._call("users/list"))
    for purpose, (username, role, privileges) in SERVICE_ACCOUNTS.items():
        token = tokens[purpose]
        if token.partition(":")[0] != username:
            raise RuntimeError("服务密钥的账号名称不匹配")
        if role not in roles:
            admin._call("roles/create", roleName=role)
        # Fail closed if an operator has granted broader permissions. Do not
        # silently keep wildcard, database/global or unexpected collection grants.
        grants = admin._call("roles/describe", roleName=role, dbName="*")
        for grant in grants:
            # Milvus represents DescribeCollection as a Global object even
            # when grant_privilege_v2 specifies an exact collection name.
            object_type = "Global" if grant.get("privilege") == "DescribeCollection" else "Collection"
            if (grant.get("dbName") != "default" or grant.get("objectName") != admin.collection
                    or grant.get("privilege") not in privileges or grant.get("objectType") != object_type):
                raise RuntimeError("Milvus 服务角色存在超出预期的授权，停止初始化")
        for privilege in sorted(privileges):
            admin._call("roles/grant_privilege_v2", roleName=role, privilege=privilege,
                        dbName="default", collectionName=admin.collection)
        if username not in users:
            admin._call("users/create", userName=username, password=token.partition(":")[2])
        user_roles = admin._call("users/describe", userName=username)
        if not isinstance(user_roles, list) or any(not isinstance(value, str) for value in user_roles):
            raise RuntimeError("Milvus 返回无效的账号角色信息")
        if set(user_roles) - {role, "public"}:
            raise RuntimeError("Milvus 服务账号存在额外角色，停止初始化")
        admin._call("users/grant_role", userName=username, roleName=role)
        # A stale password file must not pass an otherwise idempotent setup.
        service = client(admin.uri, admin.collection, token)
        service.verify()
        service.count('id != ""')


def publish_service_token(directory, token, *, uid, gid):
    """Copy into a service's private volume for a non-root runtime.

    Compose file-backed secret uid/mode overrides are not portable on Linux.
    The root init process reads host secrets; runtimes only mount their own
    derived credential directory read-only and cannot access admin secrets.
    """
    import secrets
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chown(directory, uid, gid)
    os.chmod(directory, 0o700)
    temporary = directory / (".token-" + secrets.token_hex(8))
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token + "\n")
        os.chown(temporary, uid, gid)
        os.chmod(temporary, 0o400)
        temporary.replace(directory / "token")
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="初始化 Milvus 服务认证及最小权限账号。")
    parser.add_argument("--admin-token-file", default="/run/secrets/milvus_admin_token")
    parser.add_argument("--api-token-file", default="/run/secrets/milvus_api_token")
    parser.add_argument("--worker-token-file", default="/run/secrets/milvus_worker_token")
    parser.add_argument("--current-admin-token-file")
    parser.add_argument("--allow-default-root", action="store_true")
    parser.add_argument("--publish-service-secrets", action="store_true",
                        help="仅在 root 初始化容器内发布服务专属只读密钥卷")
    args = parser.parse_args()
    try:
        admin = authenticate_admin(os.getenv("MILVUS_URI", "http://127.0.0.1:19531"),
            os.getenv("MILVUS_COLLECTION", "enterprise_chunks"), read_milvus_token_file(args.admin_token_file),
            allow_default_root=args.allow_default_root,
            current=read_milvus_token_file(args.current_admin_token_file) if args.current_admin_token_file else None)
        admin.ensure_collection()
        tokens = {"api": read_milvus_token_file(args.api_token_file),
                  "worker": read_milvus_token_file(args.worker_token_file)}
        configure_services(admin, tokens)
        if args.publish_service_secrets:
            import pwd
            account = pwd.getpwnam("app")
            for purpose, token in tokens.items():
                publish_service_token("/run/milvus-" + purpose + "-credential", token,
                                      uid=account.pw_uid, gid=account.pw_gid)
    except (RuntimeError, ValueError) as exc:
        # All application errors above are sanitized; suppress exception chains.
        print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print("无法发布 Milvus 服务密钥文件，请检查初始化卷权限。", file=sys.stderr)
        return 1
    print("Milvus 管理员凭据与服务账号初始化完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
