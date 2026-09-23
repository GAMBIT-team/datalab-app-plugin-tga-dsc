"""Linked two-panel Bokeh layout for thermal analysis data."""

from collections.abc import Sequence
from typing import Any

import pandas as pd
from bokeh.events import DoubleTap, MouseLeave, MouseMove, Tap
from bokeh.layouts import column, gridplot
from bokeh.models import (
    ColumnDataSource,
    CrosshairTool,
    CustomJS,
    HoverTool,
    TextInput,
)
from bokeh.plotting import figure

__all__ = ("create_linked_tga_plots",)

TOOLS = "pan, box_zoom, wheel_zoom, reset, save"

AXIS_MENU_MARKER = " ▾"
"""Suffix appended to clickable axis labels (a small down-pointing triangle)."""

AXIS_MENU_SETUP = """
  // Shared, page-wide state for the axis menus, installed on first use.
  //
  // Bokeh's own tap event is not used to open the menu: its tap gesture waits
  // out the double-tap interval before firing, which makes clicking an axis
  // feel sluggish. Instead the hovered axis is tracked from Bokeh's mouse move
  // events, and a plain DOM `pointerdown` listener opens the menu immediately.
  const state = (function () {
    if (window._datalab_axis_menu != null) {
      return window._datalab_axis_menu;
    }

    const MARKER = " \\u25be";
    const MENU_ID = "datalab-axis-menu";

    function close_menu() {
      const existing = document.getElementById(MENU_ID);
      if (existing == null) {
        return;
      }
      for (const [target, type, handler] of existing._dismissers || []) {
        target.removeEventListener(type, handler, true);
      }
      existing.remove();
    }

    function apply_option(target, option) {
      for (const renderer of target.renderers) {
        renderer.glyph[target.dimension].field = option;
      }
      for (const axis of target.axes) {
        axis.axis_label = option + MARKER;
      }
      target.source.change.emit();
    }

    function open_menu(target, pointer) {
      close_menu();

      const current = (target.axes[0].axis_label || "").replace(MARKER, "");
      const menu = document.createElement("div");
      menu.id = MENU_ID;
      menu.style.cssText = [
        "position: fixed",
        "z-index: 9999",
        "min-width: 9em",
        "padding: 0.25em 0",
        "background: #fff",
        "color: #212529",
        "border: 1px solid rgba(0, 0, 0, 0.2)",
        "border-radius: 0.25rem",
        "box-shadow: 0 0.25rem 0.5rem rgba(0, 0, 0, 0.15)",
        "font-family: inherit",
        "font-size: 0.9rem",
        "line-height: 1.5",
      ].join("; ");

      for (const option of target.options) {
        const item = document.createElement("div");
        item.textContent = option;
        item.style.cssText = "padding: 0.2em 1.1em; cursor: pointer; white-space: nowrap;";
        const selected = option === current;
        if (selected) {
          item.style.fontWeight = "600";
          item.style.background = "#e9ecef";
        }
        item.addEventListener("mouseenter", () => {
          item.style.background = "#dbe7f5";
        });
        item.addEventListener("mouseleave", () => {
          item.style.background = selected ? "#e9ecef" : "";
        });
        item.addEventListener("click", () => {
          close_menu();
          apply_option(target, option);
        });
        menu.appendChild(item);
      }

      document.body.appendChild(menu);

      const bbox = menu.getBoundingClientRect();
      const left = Math.min(pointer.x + 2, window.innerWidth - bbox.width - 8);
      const top = Math.min(pointer.y + 2, window.innerHeight - bbox.height - 8);
      menu.style.left = Math.max(8, left) + "px";
      menu.style.top = Math.max(8, top) + "px";

      const dismiss = (ev) => {
        if (ev.type === "pointerdown" && menu.contains(ev.target)) {
          return;
        }
        if (ev.type === "keydown" && ev.key !== "Escape") {
          return;
        }
        close_menu();
      };
      menu._dismissers = [
        [document, "pointerdown", dismiss],
        [document, "keydown", dismiss],
        [window, "scroll", dismiss],
        [window, "resize", dismiss],
      ];
      // Defer, so the click that opened the menu does not immediately close it.
      setTimeout(() => {
        for (const [el, type, handler] of menu._dismissers) {
          el.addEventListener(type, handler, true);
        }
      }, 0);
    }

    // Taps are delivered for the whole plot, including the axis gutters, with
    // data coordinates extrapolated beyond the frame. A point left of the
    // frame is therefore on the y axis, and one below it on the x axis.
    function hit(plot, x, y) {
      if (!isFinite(x) || !isFinite(y)) {
        return null;
      }
      const on_y_axis = x < plot.x_range.start;
      const on_x_axis = y < plot.y_range.start;
      if (on_y_axis && !on_x_axis) {
        return "y";
      }
      if (on_x_axis && !on_y_axis) {
        return "x";
      }
      return null;
    }

    const shared = {
      target: null,
      pointer: {x: 0, y: 0},
      canvas: null,
      opened_at: 0,
      hit: hit,
      open_menu: open_menu,
      close_menu: close_menu,
      // Bokeh resets the cursor on every move it handles, just before running
      // these callbacks, so the hint is (re)applied from them rather than here.
      set_cursor: function (hovering_axis) {
        const el = shared.canvas;
        if (el == null) {
          return;
        }
        if (hovering_axis) {
          el.style.cursor = "pointer";
        } else if (el.style.cursor === "pointer") {
          el.style.cursor = "";
        }
      },
    };

    document.addEventListener("pointermove", (ev) => {
      shared.pointer = {x: ev.clientX, y: ev.clientY};
      const el = ev.target;
      if (el instanceof Element && el.classList.contains("bk-canvas-events")) {
        shared.canvas = el;
      }
    }, true);

    document.addEventListener("pointerdown", (ev) => {
      shared.pointer = {x: ev.clientX, y: ev.clientY};
      const menu = document.getElementById(MENU_ID);
      if (menu != null && menu.contains(ev.target)) {
        return;
      }
      if (shared.target == null) {
        return;
      }
      shared.opened_at = performance.now();
      open_menu(shared.target, shared.pointer);
    }, true);

    window._datalab_axis_menu = shared;
    return shared;
  })();
"""

AXIS_MENU_TARGET = """
  function make_target(region) {
    if (region === "y") {
      return {
        plot_id: p.id,
        dimension: "y",
        options: y_options,
        renderers: y_renderers,
        axes: [y_axis],
        source: source,
      };
    }
    return {
      plot_id: p.id,
      dimension: "x",
      options: x_options,
      renderers: x_renderers,
      axes: x_axes,
      source: source,
    };
  }
"""

AXIS_HOVER_CALLBACK = (
    AXIS_MENU_SETUP
    + AXIS_MENU_TARGET
    + """
  const region = state.hit(p, cb_obj.x, cb_obj.y);
  if (region == null) {
    if (state.target != null && state.target.plot_id === p.id) {
      state.target = null;
    }
  } else {
    state.target = make_target(region);
  }
  state.set_cursor(region != null);
"""
)

AXIS_LEAVE_CALLBACK = (
    AXIS_MENU_SETUP
    + """
  if (state.target != null && state.target.plot_id === p.id) {
    state.target = null;
  }
  state.set_cursor(false);
"""
)

AXIS_TAP_CALLBACK = (
    AXIS_MENU_SETUP
    + AXIS_MENU_TARGET
    + """
  // Fallback for touch devices, which have no hover to track: Bokeh's tap
  // gesture is slower, but only fires here if `pointerdown` did not already
  // open the menu.
  if (performance.now() - state.opened_at < 750) {
    return;
  }
  const region = state.hit(p, cb_obj.x, cb_obj.y);
  if (region == null) {
    return;
  }
  state.opened_at = performance.now();
  state.open_menu(make_target(region), state.pointer);
"""
)


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
    signal. Axes are chosen by clicking their labels: clicking either x-axis
    label drives both x-axes, and clicking a y-axis label changes that panel
    only.
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
        x_axis_label=x_default + AXIS_MENU_MARKER,
        y_axis_label=mass_y_default + AXIS_MENU_MARKER,
    )
    heat_figure = figure(
        sizing_mode="scale_width",
        aspect_ratio=2.5,
        tools=TOOLS,
        x_axis_label=x_default + AXIS_MENU_MARKER,
        y_axis_label=heat_y_default + AXIS_MENU_MARKER,
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

    x_axes = [mass_figure.xaxis[0], heat_figure.xaxis[0]]
    for fig, renderer, y_options in (
        (mass_figure, mass_line, mass_y_options),
        (heat_figure, heat_line, heat_y_options),
    ):
        menu_args = dict(
            p=fig,
            source=source,
            x_options=list(x_options),
            y_options=list(y_options),
            x_renderers=[mass_line, heat_line],
            x_axes=x_axes,
            y_renderers=[renderer],
            y_axis=fig.yaxis[0],
        )
        fig.js_on_event(MouseMove, CustomJS(args=menu_args, code=AXIS_HOVER_CALLBACK))
        fig.js_on_event(Tap, CustomJS(args=menu_args, code=AXIS_TAP_CALLBACK))
        fig.js_on_event(MouseLeave, CustomJS(args=dict(p=fig), code=AXIS_LEAVE_CALLBACK))

    widgets = []
    if parameters:
        for parameter in parameters.values():
            widget = TextInput(title=parameter["label"], value=str(parameter["value"]))
            if parameter.get("event"):
                widget.js_on_change("value", CustomJS(code=parameter["event"]))
            widgets.append(widget)

    grid = gridplot([[mass_figure], [heat_figure]], merge_tools=True, sizing_mode="scale_width")

    return column(
        *widgets,
        grid,
        sizing_mode="scale_width",
    )
