"""Reusable Plotly chart components for the dashboard."""

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from typing import List


def equity_curve_chart(
    dates: List,
    values: List,
    initial_capital: float,
    total_deployed: float = None,
    injections: List[tuple] = None,
) -> go.Figure:
    """Line chart of portfolio value over time.

    Draws:
      - Gray dashed line at starting capital
      - Blue dashed line at total deployed (starting + injections) when different
      - Orange vertical markers at each injection date
    """
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=values, mode="lines",
        name="Portfolio", line=dict(color="#00cc88", width=2)
    ))
    fig.add_hline(
        y=initial_capital, line_dash="dash",
        line_color="gray", annotation_text="Starting Capital",
        annotation_position="bottom right",
    )
    if total_deployed and total_deployed > initial_capital:
        fig.add_hline(
            y=total_deployed, line_dash="dot",
            line_color="#4da6ff", annotation_text="Total Deployed",
            annotation_position="top right",
        )
    for inj_date, inj_amount in (injections or []):
        x_str = str(inj_date)
        fig.add_shape(
            type="line",
            x0=x_str, x1=x_str, y0=0, y1=1,
            xref="x", yref="paper",
            line=dict(color="orange", width=1, dash="dash"),
        )
        fig.add_annotation(
            x=x_str, y=1, yref="paper",
            text=f"+₹{inj_amount:,.0f}",
            showarrow=False,
            font=dict(color="orange", size=11),
            xanchor="left", yanchor="top",
        )
    fig.update_layout(
        title="Portfolio Equity Curve",
        xaxis_title="Date", yaxis_title="Value (₹)",
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def pnl_bar_chart(dates: List, pnl_values: List) -> go.Figure:
    """Daily P&L bar chart with green/red colouring."""
    colors = ["#00cc88" if v >= 0 else "#ff4444" for v in pnl_values]
    fig = go.Figure(go.Bar(x=dates, y=pnl_values, marker_color=colors, name="Daily P&L"))
    fig.update_layout(
        title="Daily P&L (₹)",
        xaxis_title="Date", yaxis_title="P&L (₹)",
        template="plotly_dark", height=280,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig


def sector_allocation_pie(positions_data: List[dict]) -> go.Figure:
    """Pie chart of sector allocation by current value."""
    if not positions_data:
        fig = go.Figure()
        fig.update_layout(title="No open positions", template="plotly_dark")
        return fig

    df = pd.DataFrame(positions_data)
    df["value"] = df["current_price"] * df["shares"]
    sector_values = df.groupby("sector")["value"].sum().reset_index()
    fig = px.pie(
        sector_values, values="value", names="sector",
        title="Sector Allocation",
        template="plotly_dark",
        color_discrete_sequence=px.colors.qualitative.Set3,
    )
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=40, b=20))
    return fig


def trade_history_chart(trades_data: List[dict]) -> go.Figure:
    """Scatter plot of trade P&L — green = win, red = loss."""
    if not trades_data:
        fig = go.Figure()
        fig.update_layout(title="No trades yet", template="plotly_dark")
        return fig

    df = pd.DataFrame(trades_data)
    # Accept both snake_case (ORM) and display-name (dict) column formats
    pnl_col    = "Net P&L"    if "Net P&L"    in df.columns else "net_pnl"
    date_col   = "Exit Date"  if "Exit Date"  in df.columns else "exit_date"
    symbol_col = "Symbol"     if "Symbol"     in df.columns else "symbol"

    df["_color"] = df[pnl_col].apply(lambda x: "#00cc88" if x >= 0 else "#ff4444")
    fig = go.Figure(go.Scatter(
        x=df[date_col], y=df[pnl_col],
        mode="markers",
        marker=dict(color=df["_color"], size=10, line=dict(width=1, color="white")),
        text=df[symbol_col],
        hovertemplate="<b>%{text}</b><br>P&L: ₹%{y:.2f}<extra></extra>",
        name="Trades",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        title="Trade P&L History",
        xaxis_title="Exit Date", yaxis_title="Net P&L (₹)",
        template="plotly_dark", height=320,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    return fig
