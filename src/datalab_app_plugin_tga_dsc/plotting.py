"""Linked two-panel Bokeh layout for thermal analysis data."""

from typing import Any, Sequence

import pandas as pd
from bokeh.events import DoubleTap
from bokeh.layouts import column, gridplot
from bokeh.models import (
    ColumnDataSource,
    CrosshairTool,
    CustomJS,
    HoverTool,
    Select,
    TextInput,
)
from bokeh.plotting import figure

__all__ = ("create_linked_tga_plots",)

TOOLS = "pan, box_zoom, wheel_zoom, reset, save"

SELECT_X_CALLBACK = """
  var column = cb_obj.value;
  for (var i = 0; i < renderers.length; i++) {
    renderers[i].glyph.x.field = column;
  }
  for (var j = 0; j < axes.length; j++) {
    axes[j].axis_label = column;
  }
  source.change.emit();
"""

SELECT_Y_CALLBACK = """
  var column = cb_obj.value;
  for (var i = 0; i < renderers.length; i++) {
    renderers[i].glyph.y.field = column;
  }
  axis.axis_label = column;
  source.change.emit();
"""


def create_linked_tga_plots(
    df: pd.DataFrame,
    x_options: Sequence[str],
    mass_y_options: Sequence[str],
    heat_y_options: Sequence[str],
    x_default: str | None = None,
    mass_y_default: str | None = None,
    heat_y_default: str | None = None,
    parameters: dict[str, dict[str, Any]] | None = None,
    link_plots: bool = True,
):
    """Build two vertically stacked line panels that share an x-axis.

    The upper panel shows a mass-derived signal and the lower panel a heat-flow
    signal. A single selector drives both x-axes; each panel has its own y-axis
    selector.
    """
    x_default = x_default or x_options[0]
    mass_y_default = mass_y_default or mass_y_options[0]
    heat_y_default = heat_y_default or heat_y_options[0]

    plotted = list(dict.fromkeys([*x_options, *mass_y_options, *heat_y_options]))
    missing = [name for name in plotted if name not in df.columns]
    if missing:
        raise ValueError(f"Columns missing from the data: {missing}")

    source = ColumnDataSource(df[plotted])

    mass_figure = figure(
        sizing_mode="scale_width",
        aspect_ratio=2.5,
        tools=TOOLS,
        x_axis_label=x_default,
        y_axis_label=mass_y_default,
    )
    heat_figure = figure(
        sizing_mode="scale_width",
        aspect_ratio=2.5,
        tools=TOOLS,
        x_axis_label=x_default,
        y_axis_label=heat_y_default,
        x_range=mass_figure.x_range,
    )

    mass_line = mass_figure.line(
        x=x_default, y=mass_y_default, source=source, line_width=2, color="#1b9e77"
    )
    heat_line = heat_figure.line(
        x=x_default, y=heat_y_default, source=source, line_width=2, color="#d95f02"
    )

    for fig, renderer in ((mass_figure, mass_line), (heat_figure, heat_line)):
        fig.add_tools(
            HoverTool(
                renderers=[renderer],
                tooltips=[("x", "$x{0.00}"), ("y", "$y{0.0000}")],
                mode="vline",
            )
        )
        fig.js_on_event(DoubleTap, CustomJS(args=dict(p=fig), code="p.reset.emit()"))

    if link_plots:
        crosshair = CrosshairTool(dimensions="height", line_color="grey")
        mass_figure.add_tools(crosshair)
        heat_figure.add_tools(crosshair)

    widgets = []
    if parameters:
        for parameter in parameters.values():
            widget = TextInput(title=parameter["label"], value=str(parameter["value"]))
            if parameter.get("event"):
                widget.js_on_change("value", CustomJS(code=parameter["event"]))
            widgets.append(widget)

    x_select = Select(title="x axis", value=x_default, options=list(x_options))
    x_select.js_on_change(
        "value",
        CustomJS(
            args=dict(
                renderers=[mass_line, heat_line],
                axes=[mass_figure.xaxis[0], heat_figure.xaxis[0]],
                source=source,
            ),
            code=SELECT_X_CALLBACK,
        ),
    )

    mass_y_select = Select(title="mass axis", value=mass_y_default, options=list(mass_y_options))
    mass_y_select.js_on_change(
        "value",
        CustomJS(
            args=dict(renderers=[mass_line], axis=mass_figure.yaxis[0], source=source),
            code=SELECT_Y_CALLBACK,
        ),
    )

    heat_y_select = Select(
        title="heat flow axis", value=heat_y_default, options=list(heat_y_options)
    )
    heat_y_select.js_on_change(
        "value",
        CustomJS(
            args=dict(renderers=[heat_line], axis=heat_figure.yaxis[0], source=source),
            code=SELECT_Y_CALLBACK,
        ),
    )

    grid = gridplot([[mass_figure], [heat_figure]], merge_tools=True, sizing_mode="scale_width")

    return column(
        *widgets,
        x_select,
        mass_y_select,
        heat_y_select,
        grid,
        sizing_mode="scale_width",
    )
