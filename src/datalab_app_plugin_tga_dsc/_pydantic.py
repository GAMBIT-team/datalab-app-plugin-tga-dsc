"""Helpers for supporting pydantic 1 and 2.

Released versions of datalab-server still use pydantic 1, while the development
version uses pydantic 2, so the models here work with either. Models configure
themselves with a ``class Config`` under pydantic 1 and ``model_config`` under
pydantic 2.
"""

import json
from typing import Any, TypeVar

import pydantic
from pydantic import BaseModel

__all__ = ("PYDANTIC_V1", "dump", "validate")

PYDANTIC_V1 = pydantic.VERSION.startswith("1.")

M = TypeVar("M", bound=BaseModel)


def dump(model: BaseModel) -> dict[str, Any]:
    """Return a model as JSON-compatible values, extras included and unset
    fields left out."""
    if PYDANTIC_V1:
        return json.loads(model.json(exclude_none=True))
    return model.model_dump(mode="json", exclude_none=True)


def validate(model: type[M], data: Any) -> M:
    """Validate ``data`` as an instance of ``model``."""
    if PYDANTIC_V1:
        return model.parse_obj(data)
    return model.model_validate(data)
