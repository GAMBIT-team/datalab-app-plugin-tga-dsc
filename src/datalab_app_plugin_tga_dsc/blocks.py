"""The datalab block for simultaneous TGA/DSC measurements."""

import warnings
from pathlib import Path

import bokeh.embed
from pydatalab.blocks.base import DataBlock, event, generate_js_callback_single_float_parameter

from datalab_app_plugin_tga_dsc._version import __version__
from datalab_app_plugin_tga_dsc.parsers import parse_thermal_file
from datalab_app_plugin_tga_dsc.plotting import create_linked_tga_plots
from datalab_app_plugin_tga_dsc.transitions import (
    CURIE,
    Transition,
    apply_edits,
    mentions_curie,
    merge_detected,
)
from datalab_app_plugin_tga_dsc.transitions import (
    detect_transitions as find_transitions,
)
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

    Transitions (mass steps and heat-flow peaks) are found on heating when a
    file is first plotted, and can be edited in a table below the plot: moved,
    deleted, added by clicking the plot, and given a kind and a comment. They
    are stored under ``computed["transitions"]``, with the temperature of any
    marked as a Curie point also under ``computed["curie_temperature"]``. If the
    sample name, method or comment mentions a Curie point, the largest mass step
    is marked as one automatically.
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

    @event()
    def set_transitions(self, transitions: list[dict]):
        """Replace the transitions with an edited list.

        Each entry gives a ``temperature`` (°C), ``kind`` and ``comment``, and
        the ``id`` of the transition it edits; entries without one are added,
        and transitions left out are deleted.
        """
        self._store_transitions(apply_edits(self._transitions(), transitions))

    @event()
    def detect_transitions(self):
        """Find transitions again, keeping any holding something entered by hand."""
        self._redetect = True

    def _transitions(self) -> list[Transition]:
        computed = self.data.get("computed") or {}
        return [Transition.from_dict(t) for t in computed.get("transitions", [])]

    def _store_transitions(self, transitions: list[Transition], detected_for: str | None = None):
        computed = dict(self.data.get("computed") or {})
        computed["transitions"] = [t.to_dict() for t in transitions]
        curie = [t.temperature for t in transitions if t.kind == CURIE]
        if curie:
            computed["curie_temperature"] = curie[0]
        else:
            computed.pop("curie_temperature", None)
        if detected_for is not None:
            computed["transitions_detected_for"] = detected_for
        self.data["computed"] = computed

    def _update_transitions(self, df, metadata, detected_for: str):
        """Find transitions in a file plotted for the first time, or on request."""
        computed = self.data.get("computed") or {}
        redetect = getattr(self, "_redetect", False)
        if computed.get("transitions_detected_for") == detected_for and not redetect:
            return
        detected = find_transitions(
            df, mentions_curie(metadata.sample_name, metadata.method, metadata.comment)
        )
        existing = self._transitions() if redetect else []
        self._store_transitions(merge_detected(existing, detected), detected_for=detected_for)
        self._redetect = False

    def _plot_function(self, file_path=None, link_plots=True):
        return self.generate_insitu_tga_plot(file_path=file_path, link_plots=link_plots)

    def process_and_store_data(self, file_path: str | Path):
        """Parse the export, derive plotting columns, and subsample its rows."""
        df, metadata = parse_thermal_file(file_path)
        self.data["metadata"] = metadata.to_dict()
        # Found at full resolution, before the rows are subsampled.
        self._update_transitions(
            df, metadata, detected_for=str(self.data.get("file_id") or Path(file_path).name)
        )

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
            transitions=self._transitions(),
            dispatch=(
                "document.dispatchEvent(new CustomEvent('block-event', "
                f"{{detail: Object.assign({{block_id: '{self.block_id}'}}, detail), bubbles: true}}));"
            ),
        )

        try:
            from pydatalab.bokeh_plots import DATALAB_BOKEH_THEME
        except ImportError:
            warnings.warn("datalab-server not installed, using default bokeh theme")
            DATALAB_BOKEH_THEME = None

        self.data["bokeh_plot_data"] = bokeh.embed.json_item(layout, theme=DATALAB_BOKEH_THEME)
