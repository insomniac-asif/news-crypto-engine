"""Streamlit dashboard for the Crypto News Research Engine.

Default landing page: Impact Analysis — the core research finding.
Shows avg price move by event category with sample sizes and significance.

Usage:
    streamlit run src/dashboard/app.py
"""

import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import streamlit as st

# Ensure project root is on path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from src.analysis.backtester import Backtester
from src.analysis.event_impact import EventImpactAnalyzer, ImpactResult
from src.analysis.narrative_tracker import NarrativeTracker
from src.analysis.signal_generator import SignalGenerator
from src.config import load_config
from src.storage.database import Database

matplotlib.use("Agg")


# ── App Setup ─────────────────────────────────────────────────────────


@st.cache_resource
def get_db_and_config() -> tuple:
    """Load config and database (cached across reruns)."""
    config = load_config(str(project_root / "config.yaml"))
    db_path = config.get("database", {}).get("path", "data/news_crypto.db")
    # Resolve relative path from project root
    if not Path(db_path).is_absolute():
        db_path = str(project_root / db_path)
    db = Database(db_path)
    return db, config


def main() -> None:
    """Main dashboard entry point."""
    st.set_page_config(
        page_title="Crypto News Research Engine",
        page_icon="$",
        layout="wide",
    )

    db, config = get_db_and_config()
    stats = db.get_stats()

    # Sidebar navigation
    st.sidebar.title("Crypto News Engine")
    st.sidebar.markdown("---")

    # Show DB stats in sidebar
    st.sidebar.markdown("**Database**")
    for table, count in stats.items():
        st.sidebar.text(f"  {table}: {count:,}")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "View",
        ["Impact Analysis", "Event Feed", "Narrative Tracker", "Backtest Results"],
        index=0,
    )

    if page == "Impact Analysis":
        render_impact_analysis(db, config)
    elif page == "Event Feed":
        render_event_feed(db)
    elif page == "Narrative Tracker":
        render_narrative_tracker(db, config)
    elif page == "Backtest Results":
        render_backtest_results(db, config)


# ── Impact Analysis (Default Landing Page) ────────────────────────────


def render_impact_analysis(db: Database, config: dict) -> None:
    """Render the impact analysis view — the core research output."""
    st.title("Event Impact Analysis")
    st.markdown(
        "Do specific news event types reliably move crypto prices? "
        "This analysis measures the average price change after each "
        "event category at different time horizons."
    )

    analyzer = EventImpactAnalyzer(db, config)

    # Controls
    col1, col2 = st.columns(2)
    with col1:
        min_severity = st.slider("Minimum severity", 1, 5, 1)
    with col2:
        sig_level = st.selectbox(
            "Significance level",
            [0.01, 0.05, 0.10],
            index=1,
            format_func=lambda x: f"p < {x}",
        )
    analyzer.significance_level = sig_level

    results = analyzer.analyze_by_category(min_severity=min_severity)

    if not results:
        st.warning(
            "No impact data available. Ingest articles, process them, "
            "and collect price data first."
        )
        st.code(
            "python scripts/ingest.py\n"
            "python scripts/process.py\n"
            "python scripts/ingest.py --prices-only",
            language="bash",
        )
        return

    # ── Main Chart: Avg Price Move by Category ────────────────────────

    st.subheader("Average Price Move by Event Category")

    # Build DataFrame from results
    rows = []
    for r in results:
        rows.append({
            "Category": r.category,
            "Window": f"{r.window_hours}h",
            "Avg Move (%)": r.avg_move_pct,
            "Median Move (%)": r.median_move_pct,
            "Win Rate": r.win_rate,
            "Sample Size": r.sample_size,
            "p-value": r.p_value,
            "Significant": r.significant,
            "Std Dev": r.std_dev,
        })

    df = pd.DataFrame(rows)

    # Create tabs for each time window
    windows = sorted(df["Window"].unique(), key=lambda w: int(w.replace("h", "")))

    for window in windows:
        wdf = df[df["Window"] == window].sort_values("Avg Move (%)")

        if wdf.empty:
            continue

        st.markdown(f"#### {window} Window")

        fig, ax = plt.subplots(figsize=(10, max(4, len(wdf) * 0.6)))

        colors = []
        for _, row in wdf.iterrows():
            if not row["Significant"]:
                colors.append("#999999")  # gray = not significant
            elif row["Avg Move (%)"] > 0:
                colors.append("#22c55e")  # green = positive
            else:
                colors.append("#ef4444")  # red = negative

        bars = ax.barh(
            range(len(wdf)),
            wdf["Avg Move (%)"],
            color=colors,
            edgecolor="white",
            height=0.6,
        )

        # Add labels with sample size and significance
        for i, (_, row) in enumerate(wdf.iterrows()):
            sig_marker = " ***" if row["Significant"] else ""
            label = f'n={row["Sample Size"]}{sig_marker}'
            x_pos = row["Avg Move (%)"]
            ha = "left" if x_pos >= 0 else "right"
            offset = 0.05 if x_pos >= 0 else -0.05
            ax.text(
                x_pos + offset, i, label,
                va="center", ha=ha, fontsize=9, color="#333",
            )

        ax.set_yticks(range(len(wdf)))
        ax.set_yticklabels(wdf["Category"], fontsize=11)
        ax.set_xlabel("Average Price Move (%)", fontsize=11)
        ax.axvline(x=0, color="black", linewidth=0.8, linestyle="-")
        ax.grid(axis="x", alpha=0.3)
        ax.set_title(
            f"Avg Price Move {window} After Event Detection",
            fontsize=13, fontweight="bold",
        )

        # Legend
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor="#22c55e", label="Significant positive"),
            Patch(facecolor="#ef4444", label="Significant negative"),
            Patch(facecolor="#999999", label="Not significant"),
        ]
        ax.legend(handles=legend_elements, loc="lower right", fontsize=9)

        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # ── Summary Table ─────────────────────────────────────────────────

    st.subheader("Detailed Results")

    display_df = df.copy()
    display_df["Avg Move (%)"] = display_df["Avg Move (%)"].apply(lambda x: f"{x:+.2f}%")
    display_df["Median Move (%)"] = display_df["Median Move (%)"].apply(lambda x: f"{x:+.2f}%")
    display_df["Win Rate"] = display_df["Win Rate"].apply(lambda x: f"{x:.1%}")
    display_df["p-value"] = display_df["p-value"].apply(lambda x: f"{x:.4f}")
    display_df["Significant"] = display_df["Significant"].apply(
        lambda x: "Yes ***" if x else "No"
    )

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )

    # ── Key Findings ──────────────────────────────────────────────────

    significant = [r for r in results if r.significant]
    if significant:
        st.subheader("Key Findings")
        for r in sorted(significant, key=lambda x: abs(x.avg_move_pct), reverse=True):
            direction = "+" if r.avg_move_pct > 0 else ""
            emoji_dir = "up" if r.avg_move_pct > 0 else "down"
            st.markdown(
                f"- **{r.category}** (severity >= {r.min_severity}): "
                f"avg {r.window_hours}h move **{direction}{r.avg_move_pct:.1f}%**, "
                f"n={r.sample_size}, p={r.p_value:.4f}"
            )
    else:
        st.info(
            "No statistically significant findings yet. "
            "Collect more data to increase sample sizes."
        )


# ── Event Feed ────────────────────────────────────────────────────────


def render_event_feed(db: Database) -> None:
    """Render the recent events feed."""
    st.title("Event Feed")
    st.markdown("Recently classified events with severity and affected assets.")

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        categories = [
            "All", "REGULATORY", "EXCHANGE", "PROTOCOL", "MACRO",
            "ADOPTION", "SENTIMENT", "SECURITY", "MARKET_STRUCTURE",
        ]
        category = st.selectbox("Category", categories)
    with col2:
        min_sev = st.slider("Min severity", 1, 5, 1, key="feed_sev")
    with col3:
        limit = st.number_input("Show", min_value=10, max_value=500, value=50, step=10)

    cat_filter = None if category == "All" else category
    events = db.get_events(category=cat_filter, min_severity=min_sev, limit=limit)

    if not events:
        st.info("No events found. Run ingestion and processing first.")
        return

    # Build display table
    rows = []
    for e in events:
        assets = ", ".join(e["assets_affected"]) if e["assets_affected"] else "—"
        rows.append({
            "Time": e["detected_at"][:16].replace("T", " "),
            "Category": e["category"],
            "Severity": "!" * e["severity"],
            "Assets": assets,
            "Summary": (e.get("summary") or "")[:120],
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Category distribution
    st.subheader("Category Distribution")
    cat_counts = df["Category"].value_counts()
    fig, ax = plt.subplots(figsize=(8, 4))
    cat_counts.plot.barh(ax=ax, color="#6366f1")
    ax.set_xlabel("Count")
    ax.set_title("Events by Category")
    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


# ── Narrative Tracker ─────────────────────────────────────────────────


def render_narrative_tracker(db: Database, config: dict) -> None:
    """Render the narrative tracking view."""
    st.title("Narrative Tracker")
    st.markdown("Active narratives and their momentum across crypto news.")

    tracker = NarrativeTracker(db, config)

    days = st.slider("Lookback (days)", 7, 180, 90)
    snapshots = tracker.update_narratives(days=days)

    if not snapshots:
        st.info("No narratives detected. Ingest and process more articles.")
        return

    # Summary table
    rows = []
    for s in snapshots:
        rows.append({
            "Narrative": s.name,
            "Total Events": s.event_count,
            "Recent (7d)": s.recent_count,
            "Momentum": f"{s.momentum:.0%}",
            "Top Assets": ", ".join(s.top_assets[:3]) if s.top_assets else "—",
            "First Seen": s.first_seen[:10] if s.first_seen else "—",
            "Last Seen": s.last_seen[:10] if s.last_seen else "—",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Momentum chart
    if len(snapshots) > 0:
        st.subheader("Narrative Momentum")
        active = [s for s in snapshots if s.event_count >= 2]
        if active:
            fig, ax = plt.subplots(figsize=(10, max(3, len(active) * 0.5)))
            names = [s.name for s in active]
            momentum = [s.momentum for s in active]
            counts = [s.event_count for s in active]

            bars = ax.barh(names, momentum, color="#8b5cf6", height=0.6)
            for i, (m, c) in enumerate(zip(momentum, counts)):
                ax.text(m + 0.01, i, f"n={c}", va="center", fontsize=9)

            ax.set_xlabel("Momentum (recent / total)")
            ax.set_title("Narrative Momentum (higher = gaining traction)")
            ax.set_xlim(0, 1.1)
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)


# ── Backtest Results ──────────────────────────────────────────────────


def render_backtest_results(db: Database, config: dict) -> None:
    """Render the backtest results view."""
    st.title("Backtest Results")
    st.markdown("Simulated trading performance based on event signals.")

    backtester = Backtester(db, config)

    # Controls
    col1, col2, col3 = st.columns(3)
    with col1:
        categories = [
            "All", "REGULATORY", "EXCHANGE", "PROTOCOL", "MACRO",
            "ADOPTION", "SENTIMENT", "SECURITY", "MARKET_STRUCTURE",
        ]
        category = st.selectbox("Category filter", categories, key="bt_cat")
    with col2:
        min_sev = st.slider("Min severity", 1, 5, 1, key="bt_sev")
    with col3:
        exit_hours = st.selectbox("Exit window", [1, 4, 24, 48], index=2)

    cat_filter = None if category == "All" else category
    result = backtester.run(
        category=cat_filter,
        min_severity=min_sev,
        exit_hours=exit_hours,
    )

    if result.total_trades == 0:
        st.warning("No trades generated with current filters. Adjust filters or collect more data.")
        return

    # Headline metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Trades", result.total_trades)
    m2.metric("Win Rate", f"{result.win_rate:.1%}")
    m3.metric("Total Return", f"{result.total_return_pct:+.2f}%")
    m4.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
    m5.metric("Max Drawdown", f"{result.max_drawdown_pct:.2f}%")

    # Performance comparison
    st.subheader("Strategy vs Buy & Hold")
    comp_df = pd.DataFrame({
        "Metric": ["Strategy Return", "Buy & Hold Return", "Excess Return"],
        "Value": [
            f"{result.total_return_pct:+.2f}%",
            f"{result.buy_hold_return_pct:+.2f}%",
            f"{result.excess_return_pct:+.2f}%",
        ],
    })
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    # Per-category breakdown
    if result.by_category:
        st.subheader("Performance by Event Category")
        cat_rows = []
        for cat, metrics in sorted(result.by_category.items()):
            cat_rows.append({
                "Category": cat,
                "Trades": int(metrics["trades"]),
                "Avg Return": f"{metrics['avg_return']:+.2f}%",
                "Win Rate": f"{metrics['win_rate']:.1%}",
                "Total Return": f"{metrics['total_return']:+.2f}%",
            })
        st.dataframe(pd.DataFrame(cat_rows), use_container_width=True, hide_index=True)

    # Equity curve
    if result.trades:
        st.subheader("Cumulative P&L")
        pnls = [t.pnl_pct * backtester.position_size for t in result.trades]
        cum_pnl = np.cumsum(pnls)

        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(cum_pnl, color="#6366f1", linewidth=1.5)
        ax.fill_between(range(len(cum_pnl)), cum_pnl, alpha=0.15, color="#6366f1")
        ax.axhline(y=0, color="black", linewidth=0.8, linestyle="--")
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Cumulative Return (%)")
        ax.set_title("Equity Curve")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # Additional metrics
    st.subheader("Detailed Metrics")
    detail_df = pd.DataFrame({
        "Metric": [
            "Total Trades", "Winning Trades", "Losing Trades",
            "Win Rate", "Avg Return/Trade", "Median Return/Trade",
            "Profit Factor", "Sharpe Ratio",
            "Max Drawdown", "Spread Cost", "Slippage Cost",
        ],
        "Value": [
            str(result.total_trades),
            str(result.winning_trades),
            str(result.losing_trades),
            f"{result.win_rate:.1%}",
            f"{result.avg_return_pct:+.2f}%",
            f"{result.median_return_pct:+.2f}%",
            f"{result.profit_factor:.2f}",
            f"{result.sharpe_ratio:.2f}",
            f"{result.max_drawdown_pct:.2f}%",
            f"{backtester.spread_pct:.2f}%",
            f"{backtester.slippage_pct:.2f}%",
        ],
    })
    st.dataframe(detail_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
