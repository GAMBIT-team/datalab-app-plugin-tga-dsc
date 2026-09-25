"""Linked two-panel Bokeh layout for thermal analysis data."""

import hashlib
import re
from collections.abc import Sequence
from typing import Any, TypedDict

import pandas as pd
from bokeh.events import DoubleTap, MouseMove, Tap
from bokeh.layouts import column, gridplot
from bokeh.models import (
    Axis,
    ColumnDataSource,
    CrosshairTool,
    CustomJS,
    DataRange1d,
    GlyphRenderer,
    HoverTool,
    LinearAxis,
    TextInput,
)
from bokeh.plotting import figure

from datalab_app_plugin_tga_dsc.transition_plotting import (
    TEMPERATURE_COLUMN,
    add_transition_markers,
    transition_editor,
    transition_marker_source,
)
from datalab_app_plugin_tga_dsc.transitions import Transition

__all__ = ("AxisMenu", "attach_axis_menus", "create_linked_tga_plots")

TOOLS = "pan, box_zoom, wheel_zoom, reset, save"

AXIS_MENU_MARKER = " ▾"
"""Suffix appended to clickable axis labels (a small down-pointing triangle)."""

MASS_COLOR = "#1b9e77"
HEAT_COLOR = "#d95f02"
SECONDARY_COLOR = "#7570b3"
"""Trace colours; the upper panel's axis labels are tinted to match their trace
whenever the secondary axis is showing."""

DEFAULT_LABEL_COLOR = "#444444"
"""Bokeh's default axis label colour, restored when the secondary axis is off."""

MUTED_LABEL_COLOR = "#999999"
TRANSPARENT = "rgba(0, 0, 0, 0)"

SECONDARY_OFF_OPTION = "(none)"
"""Menu entry that hides the secondary trace."""

SECONDARY_OFF_LABEL = "+ trace"
"""Placeholder shown on the secondary axis while it is off, so that it can still
be clicked to add a trace."""

_AXIS_MENU_LIBRARY = """
  // Page-wide helper for the axis menus, defined once and shared by every plot.
  //
  // `on_move` records which axis gutter the pointer is over and a plain DOM
  // `pointerdown` listener opens the menu from that. Bokeh's own tap event is
  // only a fallback for touch: its gesture waits out the double-tap interval
  // before firing, which makes clicking an axis feel sluggish.
  const menus = (function () {
    if (window.__NAMESPACE__ != null) {
      return window.__NAMESPACE__;
    }

    const MARKER = " \\u25be";
    const MENU_ID = "datalab-axis-menu";
    const CANVAS_CLASS = "bk-canvas-events";
    const OFF_OPTION = "__OFF_OPTION__";
    const OFF_LABEL = "__OFF_LABEL__";
    const DEFAULT_COLOR = "__DEFAULT_COLOR__";
    const MUTED_COLOR = "__MUTED_COLOR__";
    const TRANSPARENT = "__TRANSPARENT__";
    const TAP_FALLBACK_GUARD_MS = 750;

    let menu = null; // the open menu: {el, dismissers}
    let hovered = null; // the axis under the pointer: {target, canvas}
    let canvas = null; // the plot canvas under the pointer, if any
    let pointer = {x: 0, y: 0};
    let opened_at = 0;

    function close() {
      if (menu == null) {
        return;
      }
      for (const [el, type, handler] of menu.dismissers) {
        el.removeEventListener(type, handler, true);
      }
      menu.el.remove();
      menu = null;
    }

    function selected_option(target) {
      const label = (target.axes[0].axis_label || "").replace(MARKER, "");
      return target.secondary && label === OFF_LABEL ? OFF_OPTION : label;
    }

    // Renderers may draw from sources other than the traces' (the transition
    // markers do), and each needs a nudge to redraw with its new field.
    function redraw(renderers) {
      for (const source of new Set(renderers.map((renderer) => renderer.data_source))) {
        source.change.emit();
      }
    }

    function apply(target, option) {
      if (target.secondary) {
        apply_secondary(target, option);
        return;
      }
      for (const renderer of target.renderers) {
        renderer.glyph[target.dimension].field = option;
      }
      for (const axis of target.axes) {
        axis.axis_label = option + MARKER;
      }
      redraw(target.renderers);
    }

    // The secondary trace can also be switched off. Off, its axis keeps a muted
    // placeholder label so there is still something to click, but no ticks or
    // axis line; on, both y-axis labels are tinted to match their traces.
    function apply_secondary(target, option) {
      const off = option === OFF_OPTION;
      const axis = target.axes[0];
      const primary = target.primary_axis;

      for (const renderer of target.renderers) {
        if (!off) {
          renderer.glyph.y.field = option;
        }
        renderer.visible = !off;
      }

      axis.axis_label = (off ? OFF_LABEL : option) + MARKER;
      axis.axis_label_text_color = off ? MUTED_COLOR : target.color;
      axis.major_label_text_color = off ? TRANSPARENT : target.color;
      // Take the themed colours from the primary axis, which is never restyled.
      axis.major_tick_line_color = off ? null : primary.major_tick_line_color;
      axis.axis_line_color = off ? null : primary.axis_line_color;
      primary.axis_label_text_color = off ? DEFAULT_COLOR : target.primary_color;

      if (target.hover != null) {
        target.hover.renderers = off ? target.hover_off : target.hover_on;
      }
      redraw(target.renderers);
    }

    function open(target, at) {
      close();

      const current = selected_option(target);
      const el = document.createElement("div");
      el.id = MENU_ID;
      el.setAttribute("role", "menu");
      el.setAttribute("aria-label", "Axis options");
      el.style.cssText = [
        "position: fixed",
        "z-index: 10050",
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
        "user-select: none",
      ].join("; ");

      for (const option of target.options) {
        const item = document.createElement("div");
        item.textContent = option;
        item.setAttribute("role", "menuitem");
        item.style.cssText = "padding: 0.2em 1.1em; cursor: pointer; white-space: nowrap;";
        const is_current = option === current;
        if (is_current) {
          item.setAttribute("aria-current", "true");
          item.style.fontWeight = "600";
          item.style.background = "#e9ecef";
        }
        item.addEventListener("mouseenter", () => {
          item.style.background = "#dbe7f5";
        });
        item.addEventListener("mouseleave", () => {
          item.style.background = is_current ? "#e9ecef" : "";
        });
        item.addEventListener("click", () => {
          close();
          apply(target, option);
        });
        el.appendChild(item);
      }

      document.body.appendChild(el);

      const bbox = el.getBoundingClientRect();
      const left = Math.min(at.x + 2, window.innerWidth - bbox.width - 8);
      const top = Math.min(at.y + 2, window.innerHeight - bbox.height - 8);
      el.style.left = Math.max(8, left) + "px";
      el.style.top = Math.max(8, top) + "px";

      const dismiss = (ev) => {
        if (ev.type === "pointerdown" && el.contains(ev.target)) {
          return;
        }
        if (ev.type === "keydown" && ev.key !== "Escape") {
          return;
        }
        close();
      };
      menu = {
        el: el,
        dismissers: [
          [document, "pointerdown", dismiss],
          [document, "keydown", dismiss],
          [window, "scroll", dismiss],
          [window, "resize", dismiss],
        ],
      };
      // Defer, so the click that opened the menu does not immediately close it.
      setTimeout(() => {
        if (menu != null && menu.el === el) {
          for (const [target_el, type, handler] of menu.dismissers) {
            target_el.addEventListener(type, handler, true);
          }
        }
      }, 0);
    }

    // Events are delivered for the whole plot, including the axis gutters, with
    // data coordinates extrapolated beyond the frame. A point left of the frame
    // is therefore on the y axis, one right of it on the secondary y axis, and
    // one below it on the x axis. Reversed ranges are compared the same way
    // round, so that the regions stay where they are drawn.
    function hit(plot, x, y) {
      const xr = plot.x_range;
      const yr = plot.y_range;
      if (![x, y, xr.start, xr.end, yr.start, yr.end].every(Number.isFinite)) {
        return null;
      }
      const flipped_x = xr.start > xr.end;
      const left_of = flipped_x ? x > xr.start : x < xr.start;
      const right_of = flipped_x ? x < xr.end : x > xr.end;
      const below = yr.start > yr.end ? y > yr.start : y < yr.start;
      if (below) {
        return left_of || right_of ? null : "x";
      }
      if (left_of) {
        return "y";
      }
      if (right_of) {
        return "y2";
      }
      return null;
    }

    // Returns null for a region this plot has no menu for, such as the
    // right-hand gutter of a plot without a secondary axis.
    function target_for(context, region) {
      const common = {plot_id: context.plot.id, source: context.source};
      if (region === "x") {
        return {...common, ...context.x, dimension: "x"};
      }
      if (region === "y") {
        return {...common, ...context.y, dimension: "y"};
      }
      if (region === "y2" && context.y2 != null) {
        return {...common, ...context.y2, dimension: "y", secondary: true};
      }
      return null;
    }

    // Bokeh resets the cursor on every move it handles, just before running
    // these callbacks, so the hint is reapplied from on_move rather than here.
    function set_cursor(on_axis) {
      if (canvas == null) {
        return;
      }
      if (on_axis) {
        canvas.style.cursor = "pointer";
      } else if (canvas.style.cursor === "pointer") {
        canvas.style.cursor = "";
      }
    }

    function plot_canvas(node) {
      return node instanceof Element && node.classList.contains(CANVAS_CLASS) ? node : null;
    }

    document.addEventListener("pointermove", (ev) => {
      pointer = {x: ev.clientX, y: ev.clientY};
      canvas = plot_canvas(ev.target);
      if (canvas == null) {
        // The pointer has left every plot, so there is no axis to act on.
        hovered = null;
      }
    }, true);

    document.addEventListener("pointerdown", (ev) => {
      pointer = {x: ev.clientX, y: ev.clientY};
      if (menu != null && menu.el.contains(ev.target)) {
        return;
      }
      if (hovered == null) {
        return;
      }
      // `hovered.canvas` is only null for the first move after load, before
      // this listener existed; any plot canvas will do in that case.
      const expected = hovered.canvas;
      if (expected != null ? ev.target !== expected : plot_canvas(ev.target) == null) {
        return;
      }
      opened_at = performance.now();
      open(hovered.target, pointer);
    }, true);

    const api = {
      on_move: function (context, event) {
        const target = target_for(context, hit(context.plot, event.x, event.y));
        if (target == null) {
          if (hovered != null && hovered.target.plot_id === context.plot.id) {
            hovered = null;
          }
          set_cursor(false);
          return;
        }
        hovered = {target: target, canvas: canvas};
        set_cursor(true);
      },
      on_tap: function (context, event) {
        // Touch devices have no hover to track, so Bokeh's slower tap event is
        // the fallback there; it is ignored when `pointerdown` has just opened
        // a menu, which is the case for every pointer device.
        if (performance.now() - opened_at < TAP_FALLBACK_GUARD_MS) {
          return;
        }
        const target = target_for(context, hit(context.plot, event.x, event.y));
        if (target == null) {
          return;
        }
        opened_at = performance.now();
        open(target, pointer);
      },
    };

    window.__NAMESPACE__ = api;
    return api;
  })();
"""

_FILLED_LIBRARY = (
    _AXIS_MENU_LIBRARY.replace("__OFF_OPTION__", SECONDARY_OFF_OPTION)
    .replace("__OFF_LABEL__", SECONDARY_OFF_LABEL)
    .replace("__DEFAULT_COLOR__", DEFAULT_LABEL_COLOR)
    .replace("__MUTED_COLOR__", MUTED_LABEL_COLOR)
    .replace("__TRANSPARENT__", TRANSPARENT)
)

# Name the globals after a digest of the code behind them, so that a page left
# open across an upgrade cannot serve one version's blocks from another's copy.
AXIS_MENU_LIBRARY = _FILLED_LIBRARY.replace(
    "__NAMESPACE__",
    "datalab_axis_menus_" + hashlib.sha1(_FILLED_LIBRARY.encode()).hexdigest()[:8],
)
"""The shared menu code, inlined into the callbacks that may run first."""

if re.search(r"__[A-Z][A-Z_]*__", AXIS_MENU_LIBRARY):
    raise RuntimeError("An axis menu placeholder was left unsubstituted")

AXIS_MENU_CONTEXT = """
  function axis_context() {
    return {
      plot: p,
      source: source,
      x: {options: x_options, renderers: x_renderers, axes: x_axes},
      y: {options: y_options, renderers: y_renderers, axes: [y_axis]},
      y2: y2_axis == null ? null : {
        options: y2_options,
        renderers: y2_renderers,
        axes: [y2_axis],
        color: y2_color,
        primary_axis: y_axis,
        primary_color: y_color,
        hover: hover,
        hover_off: y_renderers,
        hover_on: y_renderers.concat(y2_renderers),
      },
    };
  }
"""

AXIS_MOVE_CALLBACK = (
    AXIS_MENU_LIBRARY + AXIS_MENU_CONTEXT + "\n  menus.on_move(axis_context(), cb_obj);\n"
)
AXIS_TAP_CALLBACK = (
    AXIS_MENU_LIBRARY + AXIS_MENU_CONTEXT + "\n  menus.on_tap(axis_context(), cb_obj);\n"
)


class AxisMenu(TypedDict, total=False):
    """One axis menu: the selectable ``options``, the ``renderers`` whose field
    it sets, the ``axes`` it relabels, and the ``color`` of its trace."""

    options: Sequence[str]
    renderers: list[GlyphRenderer]
    axes: list[Axis]
    color: str


def attach_axis_menus(
    fig,
    source: ColumnDataSource,
    x: AxisMenu,
    y: AxisMenu,
    y2: AxisMenu | None = None,
    hover: HoverTool | None = None,
) -> None:
    """Make ``fig``'s axis labels open a menu of columns to plot.

    Each of ``x``, ``y`` and ``y2`` describes one menu as a dict of the
    selectable ``options``, the ``renderers`` whose field it sets, the ``axes``
    it relabels, and optionally the ``color`` its trace is drawn in. Clicking
    anywhere in an axis gutter opens that axis's menu.

    ``y2`` is a secondary axis drawn on the right, which can also be switched
    off; pass ``hover`` alongside it so that the tooltips follow it on and off.
    """
    args = dict(
        p=fig,
        source=source,
        x_options=list(x["options"]),
        x_renderers=list(x["renderers"]),
        x_axes=list(x["axes"]),
        y_options=list(y["options"]),
        y_renderers=list(y["renderers"]),
        y_axis=y["axes"][0],
        y_color=y.get("color"),
        y2_options=list(y2["options"]) if y2 else None,
        y2_renderers=list(y2["renderers"]) if y2 else None,
        y2_axis=y2["axes"][0] if y2 else None,
        y2_color=y2.get("color") if y2 else None,
        hover=hover,
    )
    fig.js_on_event(MouseMove, CustomJS(args=args, code=AXIS_MOVE_CALLBACK))
    fig.js_on_event(Tap, CustomJS(args=args, code=AXIS_TAP_CALLBACK))


def _style_secondary_axis(axis, primary_axis, active: bool) -> None:
    """Style the secondary axis for its on or off state.

    While off it keeps a muted placeholder label to click, but no ticks or axis
    line. While on it is tinted to match its trace, and the primary axis label
    is tinted too so that the two are told apart.
    """
    if active:
        axis.axis_label_text_color = SECONDARY_COLOR
        axis.major_label_text_color = SECONDARY_COLOR
        primary_axis.axis_label_text_color = MASS_COLOR
    else:
        axis.axis_label_text_color = MUTED_LABEL_COLOR
        axis.major_label_text_color = TRANSPARENT
        axis.major_tick_line_color = None
        axis.axis_line_color = None


def create_linked_tga_plots(
    df: pd.DataFrame,
    x_options: Sequence[str],
    mass_y_options: Sequence[str],
    heat_y_options: Sequence[str],
    x_default: str | None = None,
    mass_y_default: str | None = None,
    heat_y_default: str | None = None,
    secondary_y_default: str | None = None,
    parameters: dict[str, dict[str, Any]] | None = None,
    link_plots: bool = True,
    transitions: Sequence[Transition] | None = None,
    dispatch: str | None = None,
):
    """Build two vertically stacked line panels that share an x-axis.

    The upper panel shows a mass-derived signal and the lower panel a heat-flow
    signal. Axes are chosen by clicking their labels: clicking the x-axis label
    drives both x-axes, and clicking a y-axis label changes that panel only.

    If ``heat_y_options`` is empty, as for a file with no heat flow, the lower
    panel is left out and the upper panel carries the x-axis instead.

    The upper panel also carries a secondary y-axis on the right, for showing a
    second trace alongside the first (mass and DTG, say, or mass and heat flow).
    It offers every mass and heat-flow option, is off unless
    ``secondary_y_default`` names a column, and is switched on and off from its
    own axis label.

    ``transitions`` are marked on the plot, heat-flow peaks on the lower panel
    and everything else on the upper. Given ``dispatch``, JS that sends the
    object ``detail`` as a block event, a table for editing them is added below.
    Both need a temperature column among the x options.
    """
    x_default = x_default or x_options[0]
    mass_y_default = mass_y_default or mass_y_options[0]
    has_heat = bool(heat_y_options)
    # The secondary trace may come from either panel's options.
    secondary_y_options = list(dict.fromkeys([*mass_y_options, *heat_y_options]))
    if secondary_y_default is not None and secondary_y_default not in secondary_y_options:
        raise ValueError(f"{secondary_y_default!r} is not one of the y-axis options")

    plotted = list(dict.fromkeys([*x_options, *mass_y_options, *heat_y_options]))
    missing = [name for name in plotted if name not in df.columns]
    if missing:
        raise ValueError(f"Columns missing from the data: {missing}")

    source = ColumnDataSource(df[plotted])

    # With a lower panel, the upper panel shares its x-axis, so it is left
    # unlabelled and only the lower x-axis carries the label (and its menu).
    x_axis_label = x_default + AXIS_MENU_MARKER
    mass_figure = figure(
        sizing_mode="scale_width",
        aspect_ratio=2.5,
        tools=TOOLS,
        x_axis_label=None if has_heat else x_axis_label,
        y_axis_label=mass_y_default + AXIS_MENU_MARKER,
    )
    mass_line = mass_figure.line(
        x=x_default, y=mass_y_default, source=source, line_width=2, color=MASS_COLOR
    )

    heat_figure = heat_line = None
    if has_heat:
        heat_y_default = heat_y_default or heat_y_options[0]
        heat_figure = figure(
            sizing_mode="scale_width",
            aspect_ratio=2.5,
            tools=TOOLS,
            x_axis_label=x_axis_label,
            y_axis_label=heat_y_default + AXIS_MENU_MARKER,
            x_range=mass_figure.x_range,
        )
        heat_line = heat_figure.line(
            x=x_default, y=heat_y_default, source=source, line_width=2, color=HEAT_COLOR
        )

    # The secondary trace on the upper panel, with its own range and right-hand
    # axis. The range follows this trace alone, rather than everything drawn.
    secondary_active = secondary_y_default is not None
    secondary_y_field = secondary_y_default or mass_y_options[-1]
    mass_figure.extra_y_ranges = {"secondary": DataRange1d()}
    mass_line2 = mass_figure.line(
        x=x_default,
        y=secondary_y_field,
        source=source,
        line_width=2,
        color=SECONDARY_COLOR,
        y_range_name="secondary",
        visible=secondary_active,
    )
    mass_figure.extra_y_ranges["secondary"].renderers = [mass_line2]
    # A plot's default range auto-scales over every renderer, including those
    # drawn against an extra range, so pin it to the primary trace.
    mass_figure.y_range.renderers = [mass_line]
    secondary_axis = LinearAxis(
        y_range_name="secondary",
        axis_label=(secondary_y_field if secondary_active else SECONDARY_OFF_LABEL)
        + AXIS_MENU_MARKER,
    )
    mass_figure.add_layout(secondary_axis, "right")
    _style_secondary_axis(secondary_axis, mass_figure.yaxis[0], active=secondary_active)

    panels = [(mass_figure, [mass_line])]
    if heat_figure is not None:
        panels.append((heat_figure, [heat_line]))

    # Transition markers follow the axis menus like the traces do, so their
    # sources carry every column the menus can pick.
    show_transitions = transitions is not None and TEMPERATURE_COLUMN in x_options
    marker_sources: list[ColumnDataSource] = []
    markers: dict[Any, list[GlyphRenderer]] = {mass_figure: [], heat_figure: []}
    if show_transitions:
        all_transitions = list(transitions or ())
        placed = [(mass_figure, mass_y_default, all_transitions)]
        if heat_figure is not None and heat_y_default is not None:
            on_heat_panel = [t for t in all_transitions if t.signal == "heat flow"]
            placed = [
                (
                    mass_figure,
                    mass_y_default,
                    [t for t in all_transitions if t not in on_heat_panel],
                ),
                (heat_figure, heat_y_default, on_heat_panel),
            ]
        for fig, y_field, panel_transitions in placed:
            if not panel_transitions:
                continue
            marker_source = transition_marker_source(df, panel_transitions, plotted)
            marker_sources.append(marker_source)
            markers[fig] = add_transition_markers(fig, marker_source, x_default, y_field)

    hover_tools = {}
    for fig, renderers in panels:
        hover_tools[fig] = HoverTool(
            renderers=[*renderers, mass_line2]
            if secondary_active and fig is mass_figure
            else renderers,
            tooltips=[("x", "$x{0.00}"), ("y", "$y{0.0000}")],
            mode="vline",
        )
        fig.add_tools(hover_tools[fig])
        fig.js_on_event(DoubleTap, CustomJS(args=dict(p=fig), code="p.reset.emit()"))

    if link_plots and heat_figure is not None:
        crosshair = CrosshairTool(dimensions="height", line_color="grey")
        mass_figure.add_tools(crosshair)
        heat_figure.add_tools(crosshair)

    # Both x-axes are driven together, but only the labelled one is relabelled.
    # Every trace follows it, the secondary one included, or it would be left
    # plotted against whichever column the x-axis held when it was last shown.
    labelled_x_figure = heat_figure if heat_figure is not None else mass_figure
    shared_x: AxisMenu = {
        "options": x_options,
        "renderers": [renderer for _, renderers in panels for renderer in renderers]
        + [mass_line2, *markers[mass_figure], *markers[heat_figure]],
        "axes": [labelled_x_figure.xaxis[0]],
    }
    attach_axis_menus(
        mass_figure,
        source,
        x=shared_x,
        y={
            "options": mass_y_options,
            "renderers": [mass_line, *markers[mass_figure]],
            "axes": [mass_figure.yaxis[0]],
            "color": MASS_COLOR,
        },
        y2={
            "options": [*secondary_y_options, SECONDARY_OFF_OPTION],
            "renderers": [mass_line2],
            "axes": [secondary_axis],
            "color": SECONDARY_COLOR,
        },
        hover=hover_tools[mass_figure],
    )
    if heat_figure is not None:
        attach_axis_menus(
            heat_figure,
            source,
            x=shared_x,
            y={
                "options": heat_y_options,
                "renderers": [heat_line, *markers[heat_figure]],
                "axes": [heat_figure.yaxis[0]],
            },
        )

    widgets = []
    if parameters:
        for parameter in parameters.values():
            widget = TextInput(title=parameter["label"], value=str(parameter["value"]))
            if parameter.get("event"):
                widget.js_on_change("value", CustomJS(code=parameter["event"]))
            widgets.append(widget)

    grid = gridplot([[fig] for fig, _ in panels], merge_tools=True, sizing_mode="scale_width")

    below = []
    if show_transitions and dispatch is not None:
        below.append(
            transition_editor(
                transitions or (),
                source,
                [fig for fig, _ in panels],
                mass_line,
                marker_sources,
                dispatch,
            )
        )

    return column(
        *widgets,
        grid,
        *below,
        sizing_mode="scale_width",
    )
