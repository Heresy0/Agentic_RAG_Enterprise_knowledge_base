import json
import os
from pathlib import Path

from backend.app.integrations.openmes import OpenMESConfig
from .app import create_app


def app():
    data=json.loads(Path(os.environ['OPENMES_ADAPTER_CONFIG']).read_text(encoding='utf-8'))
    return create_app(OpenMESConfig(**data,api_token=os.environ['OPENMES_API_TOKEN'],read_token=os.environ['OPENMES_ADAPTER_READ_TOKEN']))
