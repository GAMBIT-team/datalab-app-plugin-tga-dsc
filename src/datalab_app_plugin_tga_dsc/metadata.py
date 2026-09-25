"""A common metadata model for thermal analysis files from different instruments."""

import json
from datetime import datetime
from typing import Any

import pydantic
from pydantic import BaseModel, Field

__all__ = ("ThermalAnalysisMetadata",)

PYDANTIC_V1 = pydantic.VERSION.startswith("1.")
"""Released versions of datalab-server still use pydantic 1, the development
version uses pydantic 2; the model supports both."""


class ThermalAnalysisMetadata(BaseModel):
    """Metadata read from a thermal analysis export.

    Only the fields that most formats can fill are named here. Anything else a
    parser finds in a file header is kept as an extra field, under a snake_case
    version of the name the instrument gave it.
    """

    if PYDANTIC_V1:

        class Config:
            extra = "allow"

    else:
        model_config = pydantic.ConfigDict(extra="allow")

    file_format: str = Field(description="The parser that read the file.")
    original_filename: str | None = None
    sample_name: str | None = None
    sample_mass_mg: float | None = Field(
        None, description="Sample mass entered on the instrument, in mg."
    )
    instrument: str | None = None
    method: str | None = None
    operator: str | None = None
    comment: str | None = None
    measured_at: datetime | None = Field(None, description="When the measurement was started.")
    signals: list[str] = Field(
        default_factory=list,
        description="The recorded signals, named (with units) as the instrument names them.",
    )

    def to_dict(self) -> dict[str, Any]:
        """Return the metadata as JSON-compatible values, extras included and
        unset fields left out."""
        if PYDANTIC_V1:
            return json.loads(self.json(exclude_none=True))
        return self.model_dump(mode="json", exclude_none=True)
