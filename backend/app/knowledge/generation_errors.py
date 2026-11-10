"""Expected optional generation failures; all other failures propagate."""
import json

import httpx
from langchain_core.exceptions import OutputParserException
from openai import APIError
from pydantic import ValidationError
from requests import RequestException

from .rag_budget import RagBudgetExceeded


class InvalidGenerationOutput(ValueError):
    pass


# The optional-call budget may keep the original draft. EvaluationStopped,
# permissions, source changes, database failures and programming errors escape.
RECOVERABLE_GENERATION_ERRORS = (
    APIError, httpx.TransportError, RequestException, TimeoutError, ValidationError,
    OutputParserException, json.JSONDecodeError, InvalidGenerationOutput, RagBudgetExceeded,
)
