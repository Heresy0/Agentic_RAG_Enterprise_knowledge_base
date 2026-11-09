"""Load only server-owned ERPNext endpoint, company mapping and credentials."""
import json
import os
from pathlib import Path

from backend.app.integrations.erpnext import ERPNextConfig
from .app import create_app


def app():
    try:
        data = json.loads(Path(os.environ['ERPNEXT_ADAPTER_CONFIG']).read_bytes())
        config = ERPNextConfig.model_validate(dict(**data, api_key=os.environ['ERPNEXT_API_KEY'],
            api_secret=os.environ['ERPNEXT_API_SECRET'], read_token=os.environ['ERP_ADAPTER_READ_TOKEN']))
    except (KeyError, OSError, ValueError):
        raise RuntimeError('ERPNext适配配置或凭据无效。') from None
    return create_app(config)
