import pytest

from scripts import run_manufacturing_m1_tests as runner


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://localhost/postgres",
    "postgresql+psycopg://localhost/enterprise_knowledge",
    "postgresql+psycopg://localhost/manufacturing_m1_test_wrong",
    "postgresql+psycopg://example.invalid/manufacturing_m1_test_123456abcdef",
    "sqlite:///manufacturing_m1_test_123456abcdef",
])
def test_database_guard_rejects_business_or_remote_targets(url):
    with pytest.raises(ValueError): runner.checked_test_url(url)


def test_database_creation_is_explicit_and_preview_cannot_use_demo_url(monkeypatch,tmp_path):
    monkeypatch.delenv("MANUFACTURING_TEST_DATABASE_URL",raising=False)
    monkeypatch.setattr(runner,"ROOT",tmp_path)
    def unexpected(*args,**kwargs): pytest.fail("implicit database access")
    monkeypatch.setattr(runner,"create_engine",unexpected)
    with pytest.raises(ValueError,match="独立测试数据库"):
        runner.main([])


def test_output_path_is_checked_before_database_access(tmp_path,monkeypatch):
    def unexpected(*args,**kwargs): pytest.fail("database accessed before output validation")
    monkeypatch.setattr(runner,"create_engine",unexpected)
    with pytest.raises(ValueError,match="tmp"):
        runner.main(["--create-test-database","--output-dir",str(tmp_path)])
