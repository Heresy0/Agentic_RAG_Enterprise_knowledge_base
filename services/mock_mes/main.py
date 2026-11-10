"""Launch with uvicorn services.mock_mes.main:app --factory; secrets only from env."""
import os
from pathlib import Path

from backend.app.integrations.mes_contracts import MESConfigurationError, load_source_configs
from backend.app.manufacturing.contracts import WorkOrderFixtures
from .app import SourceCredential, create_app


def app():
    try:
        path = Path(os.environ["MOCK_MES_FIXTURES_FILE"])
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("oversized fixture")
        fixtures = WorkOrderFixtures.model_validate_json(path.read_bytes())
    except (KeyError, OSError, ValueError):
        # A mistaken fixture path must not print another local file's contents.
        raise MESConfigurationError("MES工单夹具配置无效，无法启动。") from None
    sources = load_source_configs(Path(os.environ["MANUFACTURING_SOURCES_FILE"]))
    config = sources.get(fixtures.tenant_id)
    if config is None:
        raise ValueError("MES夹具没有匹配的源配置。")
    credential = SourceCredential(config.tenant_id, config.source_system_id,
        frozenset(config.allowed_plants), config.token)
    return create_app(fixtures, (credential,))
