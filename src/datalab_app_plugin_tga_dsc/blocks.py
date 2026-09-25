"""The datalab block for simultaneous TGA/DSC measurements."""

import warnings
from pathlib import Path

import bokeh.embed
from pydatalab.blocks.base import DataBlock, event, generate_js_callback_single_float_parameter

from datalab_app_plugin_tga_dsc._version import __version__
from datalab_app_plugin_tga_dsc.parsers import parse_thermal_file
from datalab_app_plugin_tga_dsc.plotting import create_linked_tga_plots
from datalab_app_plugin_tga_dsc.utils import (
    HEAT_Y_OPTIONS,
    MASS_Y_OPTIONS,
    X_OPTIONS,
    add_derived_columns,
    available_options,
)


class TGAInsituBlock(DataBlock):
    """Process simultaneous thermal analysis (TGA/DSC) data.

    The block reads a single ASCII export recorded continuously through a
    temperature programme. Two formats are supported: exports from a
    simultaneous TGA/DSC, and TGA exports from TA Instruments' Universal
    Analysis. Files without heat flow are shown as a single mass panel. Any
    metadata in the file is stored with the block.

    Mass is normalised against an initial mass, which defaults to the first
    balance reading and can be overridden in the plot. No baseline or buoyancy
    correction is applied.
    """

    version = __version__
    blocktype = "insitu-tga"
    name = "TGA/DSC"
    description = __doc__
    accepted_file_extensions = (".txt",)
    _prefers_async = False

    defaults = {
        "m0": None,
        "target_data_number": 5000,
        "data_granularity": None,
    }

    @property
    def plot_functions(self):
        """Return the plot generator used by datalab."""
        return (lambda: self._plot_function(),)

    @event()
    def set_m0(self, m0: float | str | None):
        """Set the initial sample mass, in mg, used to normalise the mass axis.

        Passing ``None`` or an empty value restores the default, which is the
        first balance reading in the file.
        """
        if isinstance(m0, str):
            m0 = m0.strip()
            if not m0:
                m0 = None
            else:
                try:
                    m0 = float(m0)
                except ValueError:
                    raise ValueError(f"Invalid value for m0: {m0}. Must be a number.")

        if m0 is not None and m0 <= 0:
            raise ValueError("Initial mass must be a positive number")

        self.data["m0"] = m0

    def _plot_function(self, file_path=None, link_plots=True):
        return self.generate_insitu_tga_plot(file_path=file_path, link_plots=link_plots)

    def process_and_store_data(self, file_path: str | Path):
        """Parse the export, derive plotting columns, and subsample its rows."""
        df, metadata = parse_thermal_file(file_path)
        self.data["metadata"] = metadata.to_dict()

        m0 = self.data.get("m0")
        if m0 in (None, ""):
            m0 = float(df["Weight"].iloc[0])
            self.data["m0"] = m0
        m0 = float(m0)

        df = add_derived_columns(df, m0=m0)

        data_granularity = self.data.get("data_granularity") or self.defaults["data_granularity"]
        if not data_granularity:
            target = int(
                self.data.get("target_data_number") or self.defaults["target_data_number"] or 5000
            )
            data_granularity = max(1, len(df) // target)
        data_granularity = int(data_granularity)
        self.data["data_granularity"] = data_granularity

        return df.iloc[::data_granularity]

    def generate_insitu_tga_plot(self, file_path: Path | None = None, link_plots: bool = True):
        """Generate linked mass and heat-flow panels for an ASCII export.

        The heat-flow panel is left out for files without heat flow.
        """
        if not file_path:
            if "file_id" not in self.data:
                return
            try:
                from pydatalab.file_utils import get_file_info_by_id
            except ImportError:
                raise RuntimeError(
                    "The `datalab-server[server]` extra must be installed to use this block with a database."
                )

            file_info = get_file_info_by_id(self.data["file_id"], update_if_live=True)
            file_path = Path(file_info["location"])

        if Path(file_path).suffix.lower() not in self.accepted_file_extensions:
            raise ValueError(
                f"Unsupported file extension (must be one of {self.accepted_file_extensions})"
            )

        df = self.process_and_store_data(file_path)

        layout = create_linked_tga_plots(
            df,
            x_options=available_options(df, X_OPTIONS),
            mass_y_options=available_options(df, MASS_Y_OPTIONS),
            heat_y_options=available_options(df, HEAT_Y_OPTIONS),
            x_default="t (min)",
            parameters={
                "m0": {
                    "label": "Initial mass m₀ (mg)",
                    "value": self.data["m0"],
                    "event": generate_js_callback_single_float_parameter(
                        "set_m0", "m0", self.block_id, throttled=False
                    ),
                }
            },
            link_plots=link_plots,
        )

        try:
            from pydatalab.bokeh_plots import DATALAB_BOKEH_THEME
        except ImportError:
            warnings.warn("datalab-server not installed, using default bokeh theme")
            DATALAB_BOKEH_THEME = None

        self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
