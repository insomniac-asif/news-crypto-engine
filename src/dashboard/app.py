"""Streamlit dashboard for the Crypto News Research Engine.

Actionable, asset-level detail: what happened, to which assets, when,
and what the trade would have been.

Usage:
    streamlit run src/dashboard/app.py
"""

import io
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
from src.analysis.event_impact import EventImpactAnalyzer
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
    if not Path(db_path).is_absolute():
        db_path = str(project_root / db_path)
    db = Database(db_path)
    return db, config


def _csv_download_button(df: pd.DataFrame, filename: str, label: str = "Export CSV") -> None:
    """Render a CSV download button for a DataFrame."""
    csv = df.to_csv(index=False)
    st.download_button(label, csv, file_name=filename, mime="text/csv")


def main() -> None:
    """Main dashboard entry point."""
    st.set_page_config(
        page_title="Crypto News Research Engine",
        page_icon="$",
        layout="wide",
    )

    db, config = get_db_and_config()
    stats = db.get_stats()
    all_assets = db.get_distinct_assets()
    asset_symbols = config.get("assets", {}).get("symbols", [])
    # Merge tracked + discovered assets
    asset_options = sorted(set(all_assets) | set(asset_symbols))

    # ── Sidebar ───────────────────────────────────────────────────
    st.sidebar.title("Crypto News Engine")

    # Data freshness
    st.sidebar.markdown("**Data Status**")
    last_ingest = db.get_last_ingestion_time()
    st.sidebar.text(f"  Last ingestion: {last_ingest[:16] if last_ingest else 'Never'}")
    st.sidebar.text(f"  Total articles: {stats.get('articles', 0):,}")
    st.sidebar.text(f"  Total events:   {stats.get('events', 0):,}")
    st.sidebar.text(f"  Signals:        {stats.get('signals', 0):,}")
    st.sidebar.text(f"  Price candles:  {stats.get('prices', 0):,}")
    st.sidebar.markdown("---")

    # Global filters
    st.sidebar.markdown("**Global Filters**")
    g_date_range = st.sidebar.date_input("Date range", value=[], key="g_dates")
    g_assets = st.sidebar.multiselect("Assets", ["All"] + asset_options, default=["All"], key="g_assets")
    g_min_severity = st.sidebar.slider("Min severity", 1, 5, 1, key="g_sev")
    st.sidebar.markdown("---")

    # Resolve global filters
    g_since = None
    g_until = None
    if g_date_range and len(g_date_range) == 2:
        g_since = f"{g_date_range[0]}T00:00:00Z"
        g_until = f"{g_date_range[1]}T23:59:59Z"
    elif g_date_range and len(g_date_range) == 1:
        g_since = f"{g_date_range[0]}T00:00:00Z"

    g_asset_filter = None
    if g_assets and "All" not in g_assets and len(g_assets) == 1:
        g_asset_filter = g_assets[0]

    page = st.sidebar.radio(
        "View",
        [
            "Overview",
            "Impact Analysis",
            "Event Feed",
            "Signal Log",
            "Asset Monitor",
            "Narrative Tracker",
            "Backtest Results",
        ],
        index=0,
    )

    ctx = {
        "since": g_since,
        "until": g_until,
        "asset": g_asset_filter,
        "assets": [a for a in g_assets if a != "All"] if "All" not in g_assets else None,
        "min_severity": g_min_severity,
        "all_assets": asset_options,
    }

    if page == "Overview":
        render_overview(db, config, ctx)
    elif page == "Impact Analysis":
        render_impact_analysis(db, config, ctx)
    elif page == "Event Feed":
        render_event_feed(db, ctx)
    elif page == "Signal Log":
        render_signal_log(db, config, ctx)
    elif page == "Asset Monitor":
        render_asset_monitor(db, config, ctx)
    elif page == "Narrative Tracker":
        render_narrative_tracker(db, config)
    elif page == "Backtest Results":
        render_backtest_results(db, config, ctx)


# ── Overview ──────────────────────────────────────────────────────────


def render_overview(db: Database, config: dict, ctx: dict) -> None:
    """High-level system overview."""
    st.title("System Overview")

    stats = db.get_stats()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Events", f"{stats.get('events', 0):,}")

    tracker = NarrativeTracker(db, config)
    snapshots = tracker.update_narratives(days=30)
    m2.metric("Active Narratives", len(snapshots))

    backtester = Backtester(db, config)
    bt_result = backtester.run(exit_hours=24)
    m3.metric("Strategy Win Rate", f"{bt_result.win_rate:.1%}")
    m4.metric("Total Return", f"{bt_result.total_return_pct:+.2f}%")

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Breaking News (High Severity)")
        events = db.get_events(min_severity=4, limit=5)
        if events:
            for e in events:
                with st.expander(f"{e['detected_at'][:16]} | {e['category']} (Sev: {e['severity']})"):
                    st.write(e["summary"])
                    st.caption(f"Assets: {', '.join(e['assets_affected'])}")
        else:
            st.info("No high-severity events detected recently.")

    with col2:
        st.subheader("Top Narrative Momentum")
        if snapshots:
            top_3 = sorted(snapshots, key=lambda x: x.momentum, reverse=True)[:3]
            for s in top_3:
                st.write(f"**{s.name}**")
                st.progress(min(1.0, s.momentum))
                st.caption(f"Events: {s.event_count} | Recent: {s.recent_count}")
        else:
            st.info("No active narratives tracked.")


# ── Impact Analysis ───────────────────────────────────────────────────


def render_impact_analysis(db: Database, config: dict, ctx: dict) -> None:
    """Impact analysis with asset-level breakdown."""
    st.title("Event Impact Analysis")
    st.markdown(
        "Do specific news event types reliably move crypto prices? "
        "Asset-level breakdown shows which assets respond to which events."
    )

    analyzer = EventImpactAnalyzer(db, config)

    # Controls
    col1, col2, col3 = st.columns(3)
    with col1:
        min_severity = st.slider("Minimum severity", 1, 5, ctx["min_severity"])
    with col2:
        sig_level = st.selectbox(
            "Significance level",
            [0.01, 0.05, 0.10],
            index=1,
            format_func=lambda x: f"p < {x}",
        )
    with col3:
        asset_filter = st.selectbox(
            "Filter by asset",
            ["All"] + ctx["all_assets"],
            index=0,
            key="impact_asset",
        )
    analyzer.significance_level = sig_level

    results = analyzer.analyze_by_category(min_severity=min_severity)

    if not results:
        st.warning("No impact data available. Ingest articles, process them, and collect price data first.")
        st.code(
            "python scripts/ingest.py\npython scripts/process.py\npython scripts/ingest.py --prices-only",
            language="bash",
        )
        return

    # ── Category-level charts ─────────────────────────────────────
    st.subheader("Average Price Move by Event Category")

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
                colors.append("#94a3b8")
            elif row["Avg Move (%)"] > 0:
                colors.append("#10b981")
            else:
                colors.append("#ef4444")

        ax.barh(range(len(wdf)), wdf["Avg Move (%)"], color=colors, edgecolor="white", height=0.6)

        for i, (_, row) in enumerate(wdf.iterrows()):
            sig_marker = " *" if row["Significant"] else ""
            label = f'n={row["Sample Size"]}{sig_marker}'
            x_pos = row["Avg Move (%)"]
            ha = "left" if x_pos >= 0 else "right"
            offset = 0.05 if x_pos >= 0 else -0.05
            ax.text(x_pos + offset, i, label, va="center", ha=ha, fontsize=9, color="#1e293b")

        ax.set_yticks(range(len(wdf)))
        ax.set_yticklabels(wdf["Category"], fontsize=11)
        ax.set_xlabel("Average Price Move (%)", fontsize=11)
        ax.axvline(x=0, color="black", linewidth=0.8, linestyle="-")
        ax.grid(axis="x", alpha=0.3)
        ax.set_title(f"Avg Price Move {window} After Event Detection", fontsize=13, fontweight="bold")

        from matplotlib.patches import Patch

        legend_elements = [
            Patch(facecolor="#10b981", label="Significant positive"),
            Patch(facecolor="#ef4444", label="Significant negative"),
            Patch(facecolor="#94a3b8", label="Not significant"),
        ]
        ax.legend(handles=legend_elements, loc="lower right", fontsize=9)
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # ── Summary Table ─────────────────────────────────────────────
    st.subheader("Detailed Results")
    display_df = df.copy()
    display_df["Avg Move (%)"] = display_df["Avg Move (%)"].apply(lambda x: f"{x:+.2f}%")
    display_df["Median Move (%)"] = display_df["Median Move (%)"].apply(lambda x: f"{x:+.2f}%")
    display_df["Win Rate"] = display_df["Win Rate"].apply(lambda x: f"{x:.1%}")
    display_df["p-value"] = display_df["p-value"].apply(lambda x: f"{x:.4f}")
    display_df["Significant"] = display_df["Significant"].apply(lambda x: "Yes *" if x else "No")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── Asset-Level Breakdown ─────────────────────────────────────
    st.subheader("Asset-Level Impact Breakdown")
    st.markdown("Per-event detail showing which specific assets moved and by how much.")

    all_moves = analyzer.compute_event_moves()
    if asset_filter != "All":
        all_moves = [m for m in all_moves if m["asset"] == asset_filter]
    if ctx["since"]:
        all_moves = [m for m in all_moves if m["detected_at"] >= ctx["since"]]
    if ctx["until"]:
        all_moves = [m for m in all_moves if m["detected_at"] <= ctx["until"]]

    if all_moves:
        asset_rows = []
        for m in all_moves:
            asset_rows.append({
                "Timestamp": m["detected_at"][:16].replace("T", " "),
                "Category": m["category"],
                "Asset": m["asset"],
                "Price@Event": f"${m['base_price']:,.2f}" if m["base_price"] >= 1 else f"${m['base_price']:.4f}",
                "1h Move": f"{m['moves'].get('1h', 0) or 0:+.2f}%",
                "4h Move": f"{m['moves'].get('4h', 0) or 0:+.2f}%",
                "24h Move": f"{m['moves'].get('24h', 0) or 0:+.2f}%",
                "Severity": m["severity"],
            })

        asset_df = pd.DataFrame(asset_rows)
        st.dataframe(asset_df, use_container_width=True, hide_index=True, height=400)
        _csv_download_button(asset_df, "asset_impact_breakdown.csv")

        # ── Per-Asset Stats ───────────────────────────────────────
        st.subheader("Per-Asset Stats by Event Type")
        st.markdown("Average move by event type for each asset. "
                     "e.g. 'When a REGULATORY event hits ETH, avg 4h move = -2.1% (n=12)'")

        from collections import defaultdict

        asset_cat_moves: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )
        for m in all_moves:
            for wk, mv in m["moves"].items():
                if mv is not None:
                    asset_cat_moves[m["asset"]][m["category"]][wk].append(mv)

        stat_rows = []
        for asset in sorted(asset_cat_moves.keys()):
            for cat in sorted(asset_cat_moves[asset].keys()):
                for window in ["1h", "4h", "24h"]:
                    moves_list = asset_cat_moves[asset][cat].get(window, [])
                    if moves_list:
                        avg = np.mean(moves_list)
                        stat_rows.append({
                            "Asset": asset,
                            "Event Type": cat,
                            "Window": window,
                            "Avg Move": f"{avg:+.2f}%",
                            "N": len(moves_list),
                        })

        if stat_rows:
            stat_df = pd.DataFrame(stat_rows)
            st.dataframe(stat_df, use_container_width=True, hide_index=True, height=400)
    else:
        st.info("No asset-level move data available with current filters.")

    # ── Key Findings ──────────────────────────────────────────────
    significant = [r for r in results if r.significant]
    if significant:
        st.subheader("Key Findings")
        for r in sorted(significant, key=lambda x: abs(x.avg_move_pct), reverse=True):
            direction = "+" if r.avg_move_pct > 0 else ""
            st.markdown(
                f"- **{r.category}** (severity >= {r.min_severity}): "
                f"avg {r.window_hours}h move **{direction}{r.avg_move_pct:.1f}%**, "
                f"n={r.sample_size}, p={r.p_value:.4f}"
            )

    # ── Experiment Highlights ─────────────────────────────────────
    st.subheader("Research Findings (from Experiment Suite)")

    st.markdown("""
**Narrative Accumulation Effect** -- Events covered by multiple outlets produce
72% larger 1h moves (p=0.030) and 15% larger 24h moves (p=0.038) than
single-source events. Multi-article coverage is itself a signal.

**Contrarian Sentiment Signal** -- SENTIMENT-classified articles (hype, FUD,
meme coins) predict negative 4h returns (-0.27% vs +0.05% baseline, p=0.0004,
n=135). Retail excitement precedes short-term pullbacks.

**Signal Decay is Gradual** -- Median 75% of the 24h price impact occurs
*after* the first hour. With 15-minute execution latency, most of the move
is still capturable.

**VADER Sentiment Alone Fails** -- Raw sentiment score shows no correlation
with returns (r=0.054, p=0.39). Event *classification* outperforms raw
sentiment as a signal.

**Low Signal-to-Noise** -- Only 2 of 21 category-window combinations reach
statistical significance. Most event types do not reliably move prices,
consistent with semi-efficient market expectations.
""")

    st.caption(
        "Results from `python scripts/experiment.py --run all`. "
        "Mann-Whitney U test vs random baseline. Fixed seed=42 for reproducibility."
    )


# ── Event Feed ────────────────────────────────────────────────────────


def render_event_feed(db: Database, ctx: dict) -> None:
    """Full-detail event feed with article info and price snapshots."""
    st.title("Event Feed")
    st.markdown("Every classified event with full context: source, affected assets, sentiment, and price moves.")

    # Filters
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        categories = [
            "All", "REGULATORY", "EXCHANGE", "PROTOCOL", "MACRO",
            "ADOPTION", "SENTIMENT", "SECURITY", "MARKET_STRUCTURE",
        ]
        category = st.selectbox("Category", categories)
    with col2:
        min_sev = st.slider("Min severity", 1, 5, ctx["min_severity"], key="feed_sev")
    with col3:
        asset_filter = st.selectbox("Asset", ["All"] + ctx["all_assets"], key="feed_asset")
    with col4:
        limit = st.number_input("Show", min_value=10, max_value=500, value=50, step=10)

    cat_filter = None if category == "All" else category
    a_filter = None if asset_filter == "All" else asset_filter

    events = db.get_events_with_articles(
        category=cat_filter,
        min_severity=min_sev,
        asset=a_filter,
        since=ctx["since"],
        until=ctx["until"],
        limit=limit,
    )

    if not events:
        st.info("No events found with current filters.")
        return

    # Render each event as an expandable card
    for e in events:
        assets = e["assets_affected"]
        asset_tags = " ".join([f"`{a}`" for a in assets]) if assets else "---"
        severity_bar = "!" * e["severity"]

        header = f"{e['detected_at'][:16].replace('T', ' ')} | **{e['category']}** {severity_bar} | {asset_tags}"
        with st.expander(header, expanded=False):
            # Headline + source
            st.markdown(f"**{e.get('headline', '')}**")
            st.caption(f"Source: {e.get('source', 'Unknown')} | Published: {e.get('published_at', '')[:16]}")

            # Article URL
            url = e.get("url", "")
            if url:
                st.markdown(f"[Read article]({url})")

            # Summary
            if e.get("summary"):
                st.write(e["summary"])

            # Category + Severity
            st.markdown(f"**Category:** {e['category']} | **Severity:** {e['severity']}/5")

            # Price snapshots for each affected asset
            if assets:
                st.markdown("**Price Moves:**")
                price_rows = []
                for asset in assets:
                    price_at_event = db.get_price_at(asset, e["detected_at"])
                    if not price_at_event:
                        continue

                    base = price_at_event["close"]
                    row = {"Asset": asset, "Price@Event": base}

                    for label, hours in [("1h", 1), ("4h", 4), ("24h", 24)]:
                        from datetime import datetime, timedelta, timezone

                        ts = e["detected_at"].replace("Z", "+00:00")
                        try:
                            dt = datetime.fromisoformat(ts)
                        except ValueError:
                            dt = datetime.strptime(e["detected_at"], "%Y-%m-%dT%H:%M:%S")
                            dt = dt.replace(tzinfo=timezone.utc)
                        future_ts = (dt + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
                        future_price = db.get_price_at(asset, future_ts)
                        if future_price and future_price["timestamp"] != price_at_event["timestamp"]:
                            pct = ((future_price["close"] - base) / base) * 100
                            row[f"{label} Move"] = f"{pct:+.2f}%"
                        else:
                            row[f"{label} Move"] = "---"

                    price_rows.append(row)

                if price_rows:
                    pdf = pd.DataFrame(price_rows)
                    pdf["Price@Event"] = pdf["Price@Event"].apply(
                        lambda x: f"${x:,.2f}" if x >= 1 else f"${x:.4f}"
                    )
                    st.dataframe(pdf, use_container_width=True, hide_index=True)

    # Category distribution
    st.subheader("Category Distribution")
    cat_counts = pd.Series([e["category"] for e in events]).value_counts()
    fig, ax = plt.subplots(figsize=(8, 4))
    cat_counts.plot.barh(ax=ax, color="#6366f1")
    ax.set_xlabel("Count")
    ax.set_title("Events by Category")
    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


# ── Signal Log ────────────────────────────────────────────────────────


def render_signal_log(db: Database, config: dict, ctx: dict) -> None:
    """Dedicated signal log view — every signal as a trade log."""
    st.title("Signal Log")
    st.markdown("Every generated signal with P&L tracking at multiple horizons.")

    # Filters
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        asset_filter = st.selectbox("Asset", ["All"] + ctx["all_assets"], key="sig_asset")
    with col2:
        dir_filter = st.selectbox("Direction", ["All", "long", "short", "neutral"], key="sig_dir")
    with col3:
        categories = [
            "All", "REGULATORY", "EXCHANGE", "PROTOCOL", "MACRO",
            "ADOPTION", "SENTIMENT", "SECURITY", "MARKET_STRUCTURE",
        ]
        cat_filter = st.selectbox("Category", categories, key="sig_cat")
    with col4:
        min_conf = st.slider("Min confidence", 0.0, 1.0, 0.0, 0.05, key="sig_conf")
    with col5:
        sig_limit = st.number_input("Limit", 50, 1000, 200, 50, key="sig_limit")

    a_filter = None if asset_filter == "All" else asset_filter
    d_filter = None if dir_filter == "All" else dir_filter
    c_filter = None if cat_filter == "All" else cat_filter

    signals = db.get_signals_with_events(
        asset=a_filter,
        direction=d_filter,
        category=c_filter,
        min_confidence=min_conf,
        since=ctx["since"],
        until=ctx["until"],
        limit=sig_limit,
    )

    if not signals:
        st.info("No signals found. Run signal generation first: `python scripts/analyze.py`")
        return

    # Build the trade log table
    log_rows = []
    for i, s in enumerate(signals, 1):
        entry_price = s.get("price_at_signal")
        p1h = s.get("price_1h_later")
        p4h = s.get("price_4h_later")
        p24h = s.get("price_24h_later")

        def _pnl(entry, exit_price, direction):
            if entry is None or exit_price is None or entry <= 0:
                return None
            if direction == "long":
                return ((exit_price - entry) / entry) * 100
            elif direction == "short":
                return ((entry - exit_price) / entry) * 100
            return 0.0

        pnl_1h = _pnl(entry_price, p1h, s["direction"])
        pnl_4h = _pnl(entry_price, p4h, s["direction"])
        pnl_24h = _pnl(entry_price, p24h, s["direction"])

        # Determine best exit window
        pnls = {"1h": pnl_1h, "4h": pnl_4h, "24h": pnl_24h}
        valid_pnls = {k: v for k, v in pnls.items() if v is not None}
        best_exit = max(valid_pnls, key=valid_pnls.get) if valid_pnls else "---"
        best_pnl = max(valid_pnls.values()) if valid_pnls else None

        log_rows.append({
            "#": i,
            "Timestamp": s["entry_time"][:16].replace("T", " "),
            "Category": s.get("category", ""),
            "Asset": s["asset"],
            "Direction": s["direction"],
            "Confidence": f"{s['confidence']:.2f}",
            "Entry Price": f"${entry_price:,.2f}" if entry_price and entry_price >= 1
                           else f"${entry_price:.4f}" if entry_price else "---",
            "1h P&L": f"{pnl_1h:+.2f}%" if pnl_1h is not None else "---",
            "4h P&L": f"{pnl_4h:+.2f}%" if pnl_4h is not None else "---",
            "24h P&L": f"{pnl_24h:+.2f}%" if pnl_24h is not None else "---",
            "Best Exit": best_exit,
            "Reasoning": (s.get("summary") or "")[:80],
            "_profitable": best_pnl is not None and best_pnl > 0,
            "_pnl_1h": pnl_1h,
            "_pnl_4h": pnl_4h,
            "_pnl_24h": pnl_24h,
        })

    log_df = pd.DataFrame(log_rows)

    # Summary stats
    st.subheader("Summary")
    total = len(log_df)
    profitable = log_df["_profitable"].sum()
    win_rate = profitable / total if total > 0 else 0

    long_signals = log_df[log_df["Direction"] == "long"]
    short_signals = log_df[log_df["Direction"] == "short"]

    long_wins = long_signals["_profitable"].sum() if len(long_signals) > 0 else 0
    short_wins = short_signals["_profitable"].sum() if len(short_signals) > 0 else 0
    long_wr = long_wins / len(long_signals) if len(long_signals) > 0 else 0
    short_wr = short_wins / len(short_signals) if len(short_signals) > 0 else 0

    # Average return (use 4h as default)
    valid_4h = log_df["_pnl_4h"].dropna()
    avg_return = valid_4h.mean() if len(valid_4h) > 0 else 0

    # Best / worst asset
    asset_perf = {}
    for _, row in log_df.iterrows():
        a = row["Asset"]
        if a not in asset_perf:
            asset_perf[a] = []
        if row["_pnl_4h"] is not None:
            asset_perf[a].append(row["_pnl_4h"])
    asset_avgs = {a: np.mean(v) for a, v in asset_perf.items() if v}
    best_asset = max(asset_avgs, key=asset_avgs.get) if asset_avgs else "---"
    worst_asset = min(asset_avgs, key=asset_avgs.get) if asset_avgs else "---"

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Total Signals", total)
    m2.metric("Win Rate (best exit)", f"{win_rate:.1%}")
    m3.metric("Long Win Rate", f"{long_wr:.1%}" if long_signals is not None else "---")
    m4.metric("Short Win Rate", f"{short_wr:.1%}" if short_signals is not None else "---")
    m5.metric("Avg 4h Return", f"{avg_return:+.2f}%")
    m6.metric("Best / Worst Asset", f"{best_asset} / {worst_asset}")

    # Display table (without internal columns)
    display_cols = [c for c in log_df.columns if not c.startswith("_")]
    display_log = log_df[display_cols]
    st.dataframe(display_log, use_container_width=True, hide_index=True, height=500)

    _csv_download_button(display_log, "signal_log.csv")


# ── Asset Monitor ─────────────────────────────────────────────────────


def render_asset_monitor(db: Database, config: dict, ctx: dict) -> None:
    """Per-asset detail page with price chart, event markers, and stats."""
    st.title("Asset Monitor")

    selected_asset = st.selectbox(
        "Select Asset",
        ctx["all_assets"] if ctx["all_assets"] else ["BTC", "ETH", "SOL"],
        key="monitor_asset",
    )

    if not selected_asset:
        st.info("No assets tracked yet.")
        return

    # ── Price Chart with Event Markers ────────────────────────────
    st.subheader(f"{selected_asset} Price Chart with Event Markers")

    prices = db.get_prices(selected_asset, start=ctx["since"], end=ctx["until"])

    if prices:
        price_df = pd.DataFrame(prices)
        price_df["timestamp"] = pd.to_datetime(price_df["timestamp"])
        price_df = price_df.sort_values("timestamp")

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(price_df["timestamp"], price_df["close"], color="#6366f1", linewidth=1.2, label="Close")
        ax.fill_between(price_df["timestamp"], price_df["low"], price_df["high"], alpha=0.1, color="#6366f1")
        ax.set_ylabel(f"{selected_asset} Price (USD)")
        ax.set_title(f"{selected_asset} Price with Event Markers")
        ax.grid(alpha=0.3)

        # Overlay event markers
        events = db.get_events_with_articles(asset=selected_asset, since=ctx["since"], until=ctx["until"], limit=500)

        category_colors = {
            "REGULATORY": "#ef4444",
            "EXCHANGE": "#f59e0b",
            "PROTOCOL": "#3b82f6",
            "MACRO": "#8b5cf6",
            "ADOPTION": "#10b981",
            "SENTIMENT": "#6b7280",
            "SECURITY": "#dc2626",
            "MARKET_STRUCTURE": "#ec4899",
        }

        plotted_cats = set()
        for e in events:
            evt_time = pd.to_datetime(e["detected_at"].replace("Z", "+00:00"), utc=True)
            # Convert to tz-naive if price timestamps are tz-naive
            if price_df["timestamp"].dt.tz is None:
                evt_time = evt_time.tz_localize(None)
            cat = e["category"]
            color = category_colors.get(cat, "#6b7280")
            label = cat if cat not in plotted_cats else None
            plotted_cats.add(cat)
            ax.axvline(x=evt_time, color=color, alpha=0.5, linewidth=0.8, linestyle="--", label=label)

        if plotted_cats:
            ax.legend(loc="upper left", fontsize=8, ncol=2)

        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)
    else:
        st.info(f"No price data for {selected_asset}. Run: `python scripts/ingest.py --prices-only`")

    # ── Events for this asset ─────────────────────────────────────
    st.subheader(f"Events Affecting {selected_asset}")

    events = db.get_events_with_articles(asset=selected_asset, since=ctx["since"], until=ctx["until"], limit=200)
    if events:
        evt_rows = []
        for e in events:
            evt_rows.append({
                "Time": e["detected_at"][:16].replace("T", " "),
                "Category": e["category"],
                "Severity": e["severity"],
                "Headline": e.get("headline", "")[:80],
                "Source": e.get("source", ""),
            })
        evt_df = pd.DataFrame(evt_rows)
        st.dataframe(evt_df, use_container_width=True, hide_index=True, height=300)
    else:
        st.info(f"No events found for {selected_asset}.")

    # ── Asset-specific stats ──────────────────────────────────────
    st.subheader(f"{selected_asset} Sensitivity by Event Type")
    st.markdown("Which event types move this asset most?")

    analyzer = EventImpactAnalyzer(db, config)
    all_moves = analyzer.compute_event_moves()
    asset_moves = [m for m in all_moves if m["asset"] == selected_asset]

    if asset_moves:
        from collections import defaultdict

        cat_window_moves: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for m in asset_moves:
            for wk, mv in m["moves"].items():
                if mv is not None:
                    cat_window_moves[m["category"]][wk].append(mv)

        sens_rows = []
        for cat in sorted(cat_window_moves.keys()):
            for window in ["1h", "4h", "24h"]:
                mv_list = cat_window_moves[cat].get(window, [])
                if mv_list:
                    sens_rows.append({
                        "Event Type": cat,
                        "Window": window,
                        "Avg Move": f"{np.mean(mv_list):+.2f}%",
                        "Median Move": f"{np.median(mv_list):+.2f}%",
                        "Std Dev": f"{np.std(mv_list):.2f}%",
                        "N": len(mv_list),
                    })

        if sens_rows:
            sens_df = pd.DataFrame(sens_rows)
            st.dataframe(sens_df, use_container_width=True, hide_index=True)

        # Sensitivity chart (4h window)
        cat_4h = {}
        for cat, windows in cat_window_moves.items():
            if "4h" in windows:
                cat_4h[cat] = abs(np.mean(windows["4h"]))

        if cat_4h:
            st.markdown(f"**{selected_asset} Sensitivity Score (avg absolute 4h move)**")
            fig, ax = plt.subplots(figsize=(8, max(3, len(cat_4h) * 0.5)))
            cats = sorted(cat_4h.keys(), key=lambda c: cat_4h[c])
            vals = [cat_4h[c] for c in cats]
            ax.barh(cats, vals, color="#6366f1", height=0.6)
            ax.set_xlabel("Avg |4h Move| (%)")
            ax.set_title(f"{selected_asset} Sensitivity by Event Type")
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
    else:
        st.info(f"No price move data for {selected_asset} yet.")

    # ── Active narratives involving this asset ────────────────────
    st.subheader(f"Active Narratives Involving {selected_asset}")
    tracker = NarrativeTracker(db, config)
    snapshots = tracker.update_narratives(days=90)
    relevant = [s for s in snapshots if selected_asset in s.top_assets]
    if relevant:
        for s in relevant:
            st.markdown(f"- **{s.name}** (momentum: {s.momentum:.0%}, events: {s.event_count})")
    else:
        st.info(f"No active narratives involving {selected_asset}.")


# ── Narrative Tracker ─────────────────────────────────────────────────


def render_narrative_tracker(db: Database, config: dict) -> None:
    """Narrative tracking view."""
    st.title("Narrative Tracker")
    st.markdown("Active narratives and their momentum across crypto news.")

    tracker = NarrativeTracker(db, config)

    days = st.slider("Lookback (days)", 7, 180, 90)
    snapshots = tracker.update_narratives(days=days)

    if not snapshots:
        st.info("No narratives detected. Ingest and process more articles.")
        return

    rows = []
    for s in snapshots:
        rows.append({
            "Narrative": s.name,
            "Total Events": s.event_count,
            "Recent (7d)": s.recent_count,
            "Momentum": f"{s.momentum:.0%}",
            "Top Assets": ", ".join(s.top_assets[:3]) if s.top_assets else "---",
            "First Seen": s.first_seen[:10] if s.first_seen else "---",
            "Last Seen": s.last_seen[:10] if s.last_seen else "---",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    if len(snapshots) > 0:
        st.subheader("Narrative Momentum")
        active = [s for s in snapshots if s.event_count >= 2]
        if active:
            fig, ax = plt.subplots(figsize=(10, max(3, len(active) * 0.5)))
            names = [s.name for s in active]
            momentum = [s.momentum for s in active]
            counts = [s.event_count for s in active]

            ax.barh(names, momentum, color="#8b5cf6", height=0.6)
            for i, (m, c) in enumerate(zip(momentum, counts)):
                ax.text(m + 0.01, i, f"n={c}", va="center", fontsize=9)

            ax.set_xlabel("Momentum (recent / total)")
            ax.set_title("Narrative Momentum (higher = gaining traction)")
            ax.set_xlim(0, 1.1)
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)


# ── Backtest Results ──────────────────────────────────────────────────


def render_backtest_results(db: Database, config: dict, ctx: dict) -> None:
    """Backtest results with full trade log and expanded detail."""
    st.title("Backtest Results")
    st.markdown("Simulated trading performance with full trade-level detail.")

    backtester = Backtester(db, config)

    # Controls
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        categories = [
            "All", "REGULATORY", "EXCHANGE", "PROTOCOL", "MACRO",
            "ADOPTION", "SENTIMENT", "SECURITY", "MARKET_STRUCTURE",
        ]
        category = st.selectbox("Category filter", categories, key="bt_cat")
    with col2:
        min_sev = st.slider("Min severity", 1, 5, ctx["min_severity"], key="bt_sev")
    with col3:
        exit_hours = st.selectbox("Exit window", [1, 4, 24, 48], index=2)
    with col4:
        asset_filter = st.selectbox("Asset", ["All"] + ctx["all_assets"], key="bt_asset")

    cat_filter = None if category == "All" else category
    a_filter = None if asset_filter == "All" else asset_filter

    result = backtester.run(
        category=cat_filter,
        min_severity=min_sev,
        exit_hours=exit_hours,
        asset=a_filter,
    )

    if result.total_trades == 0:
        st.warning("No trades generated with current filters.")
        return

    # Headline metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Trades", result.total_trades)
    m2.metric("Win Rate", f"{result.win_rate:.1%}")
    m3.metric("Total Return", f"{result.total_return_pct:+.2f}%")
    m4.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
    m5.metric("Max Drawdown", f"{result.max_drawdown_pct:.2f}%")

    # Strategy vs Buy & Hold
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

    # ── Equity Curve with Trade Markers ───────────────────────────
    if result.trades:
        st.subheader("Equity Curve")
        pnls = [t.pnl_pct * backtester.position_size for t in result.trades]
        cum_pnl = np.cumsum(pnls)

        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(cum_pnl, color="#6366f1", linewidth=1.5)
        ax.fill_between(range(len(cum_pnl)), cum_pnl, alpha=0.15, color="#6366f1")
        ax.axhline(y=0, color="black", linewidth=0.8, linestyle="--")

        # Trade markers colored by win/loss
        for i, pnl in enumerate(pnls):
            color = "#10b981" if pnl > 0 else "#ef4444"
            ax.scatter(i, cum_pnl[i], color=color, s=15, zorder=5, alpha=0.7)

        ax.set_xlabel("Trade #")
        ax.set_ylabel("Cumulative Return (%)")
        ax.set_title("Equity Curve with Trade Markers")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # ── Full Trade Log ────────────────────────────────────────────
    if result.trades:
        st.subheader("Full Trade Log")

        trade_rows = []
        for i, t in enumerate(result.trades, 1):
            trade_rows.append({
                "#": i,
                "Entry Time": t.entry_time[:16].replace("T", " ") if t.entry_time else "",
                "Exit Time": t.exit_time[:16].replace("T", " ") if t.exit_time else "",
                "Asset": t.asset,
                "Direction": t.direction,
                "Entry Price": f"${t.entry_price:,.2f}" if t.entry_price >= 1 else f"${t.entry_price:.4f}",
                "Exit Price": f"${t.exit_price:,.2f}" if t.exit_price and t.exit_price >= 1
                              else f"${t.exit_price:.4f}" if t.exit_price else "---",
                "P&L %": f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else "---",
                "P&L $": f"${t.pnl_pct * t.entry_price * backtester.position_size / 100:+.2f}"
                         if t.pnl_pct is not None else "---",
                "Category": t.category,
                "Severity": t.severity,
            })

        trade_df = pd.DataFrame(trade_rows)
        st.dataframe(trade_df, use_container_width=True, hide_index=True, height=400)
        _csv_download_button(trade_df, "backtest_trades.csv")

        # ── Per-Asset Performance ─────────────────────────────────
        st.subheader("Per-Asset Performance")
        from collections import defaultdict

        asset_trades: dict[str, list[float]] = defaultdict(list)
        for t in result.trades:
            if t.pnl_pct is not None:
                asset_trades[t.asset].append(t.pnl_pct)

        asset_perf_rows = []
        for a in sorted(asset_trades.keys()):
            pnls_arr = np.array(asset_trades[a])
            asset_perf_rows.append({
                "Asset": a,
                "Trades": len(pnls_arr),
                "Win Rate": f"{float(np.sum(pnls_arr > 0) / len(pnls_arr)):.1%}",
                "Avg Return": f"{float(np.mean(pnls_arr)):+.2f}%",
                "Total Return": f"{float(np.sum(pnls_arr) * backtester.position_size):+.2f}%",
                "Best Trade": f"{float(np.max(pnls_arr)):+.2f}%",
                "Worst Trade": f"{float(np.min(pnls_arr)):+.2f}%",
            })

        asset_perf_df = pd.DataFrame(asset_perf_rows)
        st.dataframe(asset_perf_df, use_container_width=True, hide_index=True)

        # ── Best & Worst Trades ───────────────────────────────────
        sorted_trades = sorted(result.trades, key=lambda t: t.pnl_pct or 0)
        worst_5 = sorted_trades[:5]
        best_5 = sorted_trades[-5:][::-1]

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Best Trades")
            for t in best_5:
                with st.expander(f"{t.asset} {t.direction} {t.pnl_pct:+.2f}%"):
                    st.write(f"**Entry:** {t.entry_time[:16]} @ ${t.entry_price:,.2f}")
                    st.write(f"**Exit:** {t.exit_time[:16] if t.exit_time else '---'} @ ${t.exit_price:,.2f}" if t.exit_price else "")
                    st.write(f"**Category:** {t.category} | **Severity:** {t.severity}")

        with col2:
            st.subheader("Worst Trades")
            for t in worst_5:
                with st.expander(f"{t.asset} {t.direction} {t.pnl_pct:+.2f}%"):
                    st.write(f"**Entry:** {t.entry_time[:16]} @ ${t.entry_price:,.2f}")
                    st.write(f"**Exit:** {t.exit_time[:16] if t.exit_time else '---'} @ ${t.exit_price:,.2f}" if t.exit_price else "")
                    st.write(f"**Category:** {t.category} | **Severity:** {t.severity}")

    # Detailed metrics
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
