"""A common metadata model for thermal analysis files from different instruments."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = ("ThermalAnalysisMetadata",)


class ThermalAnalysisMetadata(BaseModel):
    """Metadata read from a thermal analysis export.

    Only the fields that most formats can fill are named here. Anything else a
    parser finds in a file header is kept as an extra field, under a snake_case
    version of the name the instrument gave it.
    """

    model_config = ConfigDict(extra="allow")

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
