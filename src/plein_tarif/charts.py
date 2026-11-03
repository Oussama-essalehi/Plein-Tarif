"""Shared chart style for the static figures (Plotly -> PNG).

Palette: categorical slots validated for colour-vision deficiencies (adjacent-pair CVD Delta E >= 9).
Identity is fixed per entity (a fuel always has the same colour), never per rank. Two light slots sit
below 3:1 contrast on white, so charts use direct labels in addition to legends.
"""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio

from plein_tarif.config import get_logger, path

log = get_logger(__name__)
import logging as _logging  # noqa: E402

for _noisy in ("kaleido", "choreographer"):   # PNG export libraries log every browser start/stop
    _logging.getLogger(_noisy).setLevel(_logging.WARNING)

BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = (
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
INK, INK_2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1", "#fcfcfb"
SHADE = "rgba(42,120,214,0.10)"          # discount periods / annotations

FUEL_COLORS = {"Gazole": ORANGE, "E10": AQUA, "SP98": BLUE, "SP95": VIOLET, "E85": GREEN, "GPLc": MAGENTA}
FUEL_LABELS = {"Gazole": "Gazole", "E10": "SP95-E10", "SP98": "SP98", "SP95": "SP95", "E85": "E85", "GPLc": "GPLc"}
CATEGORY_COLORS = {"supermarket": BLUE, "oil_company": ORANGE, "independent_other": AQUA, "unmatched": MUTED}
RISE, FALL = ORANGE, BLUE                # rockets (rises) vs feathers (falls)

pio.templates["plein_tarif"] = go.layout.Template(layout=dict(
    font=dict(family="Inter, Segoe UI, Arial, sans-serif", size=14, color=INK),
    paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
    title=dict(font=dict(size=20, color=INK), x=0.01, xanchor="left", y=0.97),
    xaxis=dict(showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID, tickfont=dict(color=INK_2),
               title=dict(font=dict(color=INK_2))),
    yaxis=dict(gridcolor=GRID, zeroline=False, linecolor=GRID, tickfont=dict(color=INK_2),
               title=dict(font=dict(color=INK_2))),
    legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, font=dict(color=INK_2),
                bgcolor="rgba(0,0,0,0)"),
    margin=dict(l=70, r=40, t=110, b=70),
    colorway=[BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED],
    hovermode="x unified",
))
pio.templates.default = "plein_tarif"


def title(main: str, sub: str | None = None) -> dict:
    """Title with an optional grey subtitle line."""
    text = f"<b>{main}</b>" + (f"<br><span style='font-size:14px;color:{INK_2}'>{sub}</span>" if sub else "")
    return dict(text=text)


def source_note(fig: go.Figure, text: str = "Source : prix-carburants.gouv.fr (Licence Ouverte), calculs Plein Tarif",
                y: float = -0.16) -> None:
    fig.add_annotation(text=text, xref="paper", yref="paper", x=0, y=y, showarrow=False,
                       xanchor="left", font=dict(size=11, color=MUTED))


def save(fig: go.Figure, name: str, width: int = 1200, height: int = 650) -> Path:
    """Write reports/figures/<name>.png (2x scale) and return its path."""
    out = path("figures") / f"{name}.png"
    fig.write_image(out, width=width, height=height, scale=2)
    log.info("figure %s", out.name)
    return out
