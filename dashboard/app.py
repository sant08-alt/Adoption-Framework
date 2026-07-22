"""Adoption analytics dashboard (specs/04_dashboard_spec.md).

Reads the three mart tables from BigQuery; falls back to CSV snapshots in
dashboard/snapshots/ (written by run_pipeline.py --export-snapshots) when
BigQuery is unavailable, with a visible banner.

Run: streamlit run dashboard/app.py
"""

import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
MARTS = ["mart_customer_health", "mart_sku_adoption", "mart_feature_adoption",
         "mart_customer_health_mtd"]
MART_DATE_COLS = {
    "mart_customer_health": ["month_start"],
    "mart_sku_adoption": ["month_start"],
    "mart_feature_adoption": ["month_start"],
    "mart_customer_health_mtd": ["as_of_date"],
}

TIER_COLORS = {
    "Healthy": "#2e7d32",
    "Watch": "#f9a825",
    "At Risk": "#e65100",
    "Shelfware": "#b71c1c",
}
TIER_ORDER = ["Healthy", "Watch", "At Risk", "Shelfware"]

st.set_page_config(page_title="Product Adoption Analytics", layout="wide")


@st.cache_data(ttl=600, show_spinner="Loading marts...")
def load_marts():
    load_dotenv(ROOT / ".env")
    project = os.getenv("GCP_PROJECT_ID")
    dataset = os.getenv("BQ_DATASET", "adoption_analytics")
    if project and project != "your-gcp-project-id":
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=project)
            marts = {
                m: client.query(f"SELECT * FROM `{project}.{dataset}.{m}`").to_dataframe()
                for m in MARTS
            }
            return marts, "bigquery"
        except Exception:  # noqa: BLE001 - fall through to snapshots
            pass
    if all((SNAPSHOT_DIR / f"{m}.csv").exists() for m in MARTS):
        marts = {m: pd.read_csv(SNAPSHOT_DIR / f"{m}.csv",
                                parse_dates=MART_DATE_COLS.get(m, []))
                 for m in MARTS}
        return marts, "snapshot"
    return None, "none"


def latest_month(df):
    return df["month_start"].max()


KPI_CSS = """
<style>
.kpi{border:1px solid rgba(128,128,128,0.25);border-radius:10px;
     padding:12px 16px;min-height:104px;}
.kpi-label{font-size:0.80rem;opacity:0.70;margin-bottom:6px;}
.kpi-value{font-size:1.85rem;font-weight:700;line-height:1.1;}
.kpi-sub{font-size:0.70rem;opacity:0.55;margin-top:6px;}
a.kpi-link{text-decoration:none;color:inherit;display:block;}
a.kpi-link[href]{cursor:pointer;}
a.kpi-link[href]:hover .kpi{border-color:rgba(128,128,128,0.65);}
</style>
"""


def _period_label(ts, granularity):
    ts = pd.Timestamp(ts)
    if granularity == "Month":
        return ts.strftime("%Y-%m")
    if granularity == "Quarter":
        return f"{ts.year}-Q{(ts.month - 1) // 3 + 1}"
    return str(ts.year)


def _card(col, label, value, sub="", accent=None, anchor=None):
    # Every card uses the same <a> wrapper so the row aligns; a card without an
    # anchor simply omits href (rendered as a non-clickable box).
    color = f"color:{accent};" if accent else ""
    href = f'href="#{anchor}"' if anchor else ""
    inner = (f'<div class="kpi"><div class="kpi-label">{label}</div>'
             f'<div class="kpi-value" style="{color}">{value}</div>'
             f'<div class="kpi-sub">{sub}</div></div>')
    col.markdown(f'<a class="kpi-link" {href}>{inner}</a>', unsafe_allow_html=True)


def portfolio_view(health, sku, feature):
    st.title("Portfolio Adoption Health")
    st.markdown(KPI_CSS, unsafe_allow_html=True)

    # --- Filters: date range (granularity + multiple periods) + dimensions
    months = sorted(pd.to_datetime(health["month_start"]).dt.normalize().unique())
    current_year = max(months).year
    fc1, fc2 = st.columns([1, 2])
    granularity = fc1.radio("Date range", ["Month", "Quarter", "Year"], horizontal=True)
    labels = sorted({_period_label(m, granularity) for m in months})
    # Default to every period of the current year (e.g. all months of 2026).
    default_labels = sorted({_period_label(m, granularity) for m in months
                             if pd.Timestamp(m).year == current_year})
    sel_periods = fc2.multiselect("Periods (pick one or more)", labels, default=default_labels)
    if not sel_periods:
        sel_periods = default_labels

    dc1, dc2, dc3 = st.columns(3)
    regions = dc1.multiselect("Region", sorted(health["region"].unique()))
    segments = dc2.multiselect("Customer segment", sorted(health["customer_segment"].unique()))
    platforms = dc3.multiselect("Product platform", sorted(sku["product_platform"].unique()))

    if regions:
        health = health[health["region"].isin(regions)]
    if segments:
        health = health[health["customer_segment"].isin(segments)]
    if platforms:
        sku = sku[sku["product_platform"].isin(platforms)]
        feature = feature[feature["product_platform"].isin(platforms)]
        health = health[health["cust_id"].isin(set(sku["cust_id"].unique()))]

    period_months = [m for m in months if _period_label(m, granularity) in sel_periods]
    as_of = max(period_months)
    period_health = health[health["month_start"].isin(period_months)]
    latest = health[health["month_start"] == as_of]
    latest_sku = sku[sku["month_start"] == as_of]

    if latest.empty:
        st.info("No customers match the current filters for this period.")
        return

    # --- Snapshot: key numbers as of the selected period -----------------
    st.caption(f"**Snapshot** — as of {as_of:%b %Y}")
    plat = (latest_sku.groupby("product_platform")
            .agg(util=("utilization", "mean"), depth=("feature_depth", "mean")))
    plat["score"] = (plat["util"] + plat["depth"]) / 2
    plat = plat.sort_values("score", ascending=False)

    # Adoption = customer has used at least one entitled product (cumulative > 0).
    adoption_rate = (latest["cumulative_consumed"] > 0).mean()

    cards = [("Entitled customers", f"{latest['cust_id'].nunique():,}", "")]
    # Only surface adoption rate when it is informative (i.e. not always 100%).
    if adoption_rate < 0.999:
        cards.append(("Overall adoption rate", f"{adoption_rate:.0%}",
                      "customers using ≥1 entitled product"))
    if plat.empty:
        cards.append(("Healthiest platform", "–", ""))
    else:
        cards.append(("Healthiest platform", plat.index[0],
                      f"{plat['score'].iloc[0]:.0%} avg adoption"))

    for col, (label, value, sub) in zip(st.columns(len(cards)), cards):
        _card(col, label, value, sub)

    # --- Adoption health: all tiers as %, click to jump to that table ----
    st.caption("**Adoption health** — click a percentage to see those customers")
    a1, a2, a3, a4, a5 = st.columns(5)
    _card(a1, "Avg CVRS", f"{latest['cvrs'].mean():.0f}")
    _card(a2, "% Healthy", f"{(latest['health_tier'] == 'Healthy').mean():.0%}",
          "view customers ↓", TIER_COLORS["Healthy"], "tbl-healthy")
    _card(a3, "% Shelfware", f"{(latest['health_tier'] == 'Shelfware').mean():.0%}",
          "view customers ↓", TIER_COLORS["Shelfware"], "tbl-shelfware")
    _card(a4, "% At Risk", f"{(latest['health_tier'] == 'At Risk').mean():.0%}",
          "view customers ↓", TIER_COLORS["At Risk"], "tbl-atrisk")
    _card(a5, "% Expansion", f"{latest['expansion_flag'].mean():.0%}",
          "view customers ↓", "#0B8F72", "tbl-expansion")

    # --- Revenue impact overlay (assumed pricing; never a CVRS input) ----
    st.caption("**Revenue impact** (assumed list pricing — not part of the score)")
    at_risk = latest["health_tier"].isin(["At Risk", "Shelfware"])
    committed_arr = latest["committed_arr"].sum()
    arr_at_risk = latest.loc[at_risk, "committed_arr"].sum()
    expansion_pipeline = latest.loc[latest["expansion_flag"], "overage_arr_run_rate"].sum()
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Committed ARR", f"${committed_arr / 1e6:.1f}M")
    r2.metric("ARR at risk", f"${arr_at_risk / 1e6:.1f}M",
              f"{arr_at_risk / committed_arr:.0%} of book" if committed_arr else None,
              delta_color="inverse")
    r3.metric("Expansion pipeline", f"${expansion_pipeline / 1e6:.1f}M",
              help="Annualized run-rate of current billable overage on Expansion Signal accounts")
    r4.metric("Avg ARR / customer", f"${committed_arr / max(len(latest), 1) / 1e6:.2f}M")

    left, right = st.columns([2, 1])
    with left:
        trend = (period_health.groupby(["month_start", "health_tier"])["cust_id"].nunique()
                 .reset_index(name="customers"))
        # Plot against an explicit month label so tick names are exact and ordered,
        # instead of letting plotly auto-place datetime ticks.
        trend["month_label"] = pd.to_datetime(trend["month_start"]).dt.strftime("%b %Y")
        month_order = (trend.sort_values("month_start")["month_label"].drop_duplicates().tolist())
        fig = px.bar(trend, x="month_label", y="customers", color="health_tier",
                     color_discrete_map=TIER_COLORS,
                     category_orders={"health_tier": TIER_ORDER, "month_label": month_order},
                     title="Customers by health tier over time")
        fig.update_layout(xaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
    with right:
        tier_counts = latest["health_tier"].value_counts().reset_index()
        fig = px.pie(tier_counts, names="health_tier", values="count", hole=0.5,
                     color="health_tier", color_discrete_map=TIER_COLORS,
                     title=f"Health tier mix — {as_of:%b %Y}")
        st.plotly_chart(fig, use_container_width=True)

    # --- Customer detail: anchored tables the percentages jump to --------
    st.header("Customer detail", anchor="customer-detail")
    st.caption(f"As of {as_of:%b %Y}. Use the percentages above to jump to a group.")

    def tier_table(anchor, tier, ascending):
        rows = latest[latest["health_tier"] == tier]
        st.subheader(f"{tier} customers ({len(rows)})", anchor=anchor)
        if rows.empty:
            st.info("No customers in this group for the current filters.")
            return
        show = (rows.sort_values("cvrs", ascending=ascending)
                [["cust_name", "region", "customer_segment", "cvrs",
                  "utilization", "committed_arr"]].copy())
        show["utilization"] = show["utilization"].map("{:.0%}".format)
        show["committed_arr"] = show["committed_arr"].map("${:,.0f}".format)
        st.dataframe(show, use_container_width=True, hide_index=True)

    tier_table("tbl-healthy", "Healthy", ascending=False)
    tier_table("tbl-shelfware", "Shelfware", ascending=True)
    tier_table("tbl-atrisk", "At Risk", ascending=True)

    st.subheader("Expansion signals (sustained overage — hand to sales)",
                 anchor="tbl-expansion")
    flagged = set(latest.loc[latest["expansion_flag"], "cust_id"])
    if not flagged:
        st.info("No accounts in sustained overage for the current filters.")
    else:
        # "Months in overage" is a cumulative measure through the as-of month,
        # so count all months <= as_of at 120%+ rather than only this period.
        hist = health[(health["cust_id"].isin(flagged)) & (health["month_start"] <= as_of)]
        over_months = hist[hist["raw_utilization"] >= 1.2]
        summary = (over_months.groupby(["cust_id", "cust_name"])
                   .agg(months_in_overage=("month_start", "nunique"),
                        peak_raw_utilization=("raw_utilization", "max"))
                   .reset_index())
        pipeline = latest.loc[latest["expansion_flag"], ["cust_id", "overage_arr_run_rate"]]
        summary = (summary.merge(pipeline, on="cust_id", how="left")
                   .sort_values(["months_in_overage", "peak_raw_utilization"],
                                ascending=[False, False]))
        summary["peak_raw_utilization"] = summary["peak_raw_utilization"].map("{:.0%}".format)
        summary["overage_arr_run_rate"] = summary["overage_arr_run_rate"].fillna(0).map("${:,.0f}".format)
        st.dataframe(summary, use_container_width=True, hide_index=True)


def customer_view(health, sku):
    st.title("Customer Drill-Down")
    names = health.sort_values("cust_name")["cust_name"].unique()
    chosen = st.selectbox("Customer", names)
    ch = health[health["cust_name"] == chosen].sort_values("month_start")
    latest = ch.iloc[-1]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("CVRS (latest)", f"{latest['cvrs']:.0f}")
    k2.metric("Health tier", latest["health_tier"])
    k3.metric("Raw utilization", f"{latest['raw_utilization']:.0%}" if pd.notna(latest["raw_utilization"]) else "–")
    k4.metric("Time to first value",
              f"{int(latest['ttfv_days'])} days" if pd.notna(latest["ttfv_days"]) else "Never deployed",
              help="Days from contract start to first use of the fastest-adopted product")
    k5.metric("Committed ARR", f"${latest['committed_arr'] / 1e6:.2f}M",
              help="Assumed list pricing — not part of CVRS")

    left, right = st.columns(2)
    with left:
        fig = px.line(ch, x="month_start", y="cvrs", markers=True, range_y=[0, 100],
                      title="CVRS over time")
        fig.add_hline(y=70, line_dash="dot", line_color="#2e7d32", annotation_text="Healthy")
        fig.add_hline(y=40, line_dash="dot", line_color="#e65100", annotation_text="At Risk")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        comp = ch.melt(id_vars="month_start",
                       value_vars=["utilization", "feature_depth", "consistency", "breadth"],
                       var_name="component", value_name="score")
        fig = px.line(comp, x="month_start", y="score", color="component", range_y=[0, 1.05],
                      title="Score components (why is my score X?)")
        st.plotly_chart(fig, use_container_width=True)

    fig = go.Figure()
    fig.add_bar(x=ch["month_start"], y=ch["consumed_units"], name="Consumed")
    fig.add_scatter(x=ch["month_start"], y=ch["entitled_units"], name="Entitled",
                    mode="lines", line={"shape": "hv", "width": 3})
    fig.update_layout(title="Consumption vs entitlement (expansions appear as steps)")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("SKU detail (latest month)")
    cs = sku[(sku["cust_name"] == chosen) & (sku["month_start"] == latest_month(sku))]
    cols = ["product_name", "product_platform", "deployment_status",
            "first_adoption_date", "days_to_first_value",
            "active_entitled_units", "consumed_units", "utilization",
            "active_features", "eligible_features", "feature_depth"]
    st.dataframe(cs[cols].sort_values("days_to_first_value", ascending=False, na_position="last"),
                 use_container_width=True, hide_index=True)


def product_view(sku, feature):
    st.title("Product & Feature Adoption")
    latest_sku = sku[sku["month_start"] == latest_month(sku)]

    ranking = (latest_sku.groupby(["product_platform"])
               .agg(avg_utilization=("utilization", "mean"),
                    avg_feature_depth=("feature_depth", "mean"),
                    customers=("cust_id", "nunique"))
               .reset_index())
    fig = px.bar(ranking, x="product_platform", y=["avg_utilization", "avg_feature_depth"],
                 barmode="group", range_y=[0, 1], title="Adoption by platform (latest month)")
    st.plotly_chart(fig, use_container_width=True)

    latest_feat = feature[feature["month_start"] == latest_month(feature)]
    st.subheader("Feature adoption extremes (latest month, features with 3+ eligible customers)")
    eligible_enough = latest_feat[latest_feat["eligible_customers"] >= 3]
    cols = ["feature_name", "product_name", "product_platform",
            "eligible_customers", "active_customers", "adoption_rate", "median_days_to_adopt"]
    left, right = st.columns(2)
    with left:
        st.caption("Most adopted")
        st.dataframe(eligible_enough.nlargest(15, "adoption_rate")[cols],
                     use_container_width=True, hide_index=True)
    with right:
        st.caption("Least adopted — activation candidates")
        st.dataframe(eligible_enough.nsmallest(15, "adoption_rate")[cols],
                     use_container_width=True, hide_index=True)

    st.subheader("How long features take to turn on")
    st.caption("Median days from purchase to first use, by platform — **longer bars = more activation friction.**")
    tta = feature.dropna(subset=["median_days_to_adopt"]).drop_duplicates("feature_id").copy()
    by_platform = (tta.groupby("product_platform")["median_days_to_adopt"].median()
                   .round(0).reset_index(name="days"))
    fig = px.bar(by_platform, x="days", y="product_platform", orientation="h",
                 text="days")
    fig.update_traces(marker_color="#1C7293", texttemplate="%{text:.0f} days",
                      textposition="outside", cliponaxis=False, hovertemplate=None,
                      hoverinfo="skip")
    fig.update_layout(showlegend=False, yaxis_title=None,
                      xaxis_title="Median days from purchase to first use",
                      yaxis={"categoryorder": "total ascending"},
                      margin={"l": 10, "r": 40})
    st.plotly_chart(fig, use_container_width=True)


def mtd_view(mtd):
    st.title("Live · Month-to-Date")
    as_of = pd.to_datetime(mtd["as_of_date"].iloc[0])
    pct = int(mtd["pct_month_elapsed"].iloc[0])
    st.info(
        f"**Projected CVRS — as of {as_of:%b %d, %Y} · {pct}% of month elapsed.** "
        "Pace-adjusted and provisional; the monthly CVRS remains the system of record. "
        "**Not used for tier assignment or compensation.**"
    )

    decel = mtd[mtd["momentum"] < -0.30]
    accel = mtd[mtd["momentum"] > 0.30]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Projected avg CVRS", f"{mtd['projected_cvrs'].mean():.0f}")
    k2.metric("Decelerating this month", len(decel),
              help="Pacing >30% below the account's own trailing daily rate")
    k3.metric("Accelerating", len(accel))
    k4.metric("Projected < 40", int((mtd["projected_cvrs"] < 40).sum()),
              help="On track to finish the month in At-Risk territory")

    st.subheader("Decelerating accounts — the early-warning call list")
    st.caption("Sorted by how far this month's pace has fallen below the account's own baseline. "
               "These look fine in last month's final score but are cratering **now**.")
    call = mtd[mtd["momentum"].notna()].sort_values("momentum").head(15).copy()
    call["delta_vs_last"] = (call["projected_cvrs"] - call["last_final_cvrs"]).round(1)
    show = call[["cust_name", "region", "customer_segment", "momentum",
                 "mtd_utilization", "projected_cvrs", "last_final_cvrs",
                 "delta_vs_last", "last_tier"]].copy()
    show["momentum"] = show["momentum"].map("{:+.0%}".format)
    show["mtd_utilization"] = show["mtd_utilization"].map("{:.0%}".format)
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.subheader("Movers: projected vs last month's final CVRS")
    st.caption("Points below the line are trending down this month; above, up.")
    fig = px.scatter(mtd, x="last_final_cvrs", y="projected_cvrs", color="momentum",
                     color_continuous_scale="RdYlGn", range_color=[-0.8, 0.8],
                     hover_name="cust_name", range_x=[0, 100], range_y=[0, 100])
    fig.add_shape(type="line", x0=0, y0=0, x1=100, y1=100,
                  line={"dash": "dot", "color": "#888", "width": 1})
    fig.update_layout(xaxis_title="Last month final CVRS",
                      yaxis_title="Projected CVRS (this month)",
                      coloraxis_colorbar={"title": "Momentum"})
    st.plotly_chart(fig, use_container_width=True)


def main():
    marts, source = load_marts()
    if marts is None:
        st.error(
            "No data available. Either configure BigQuery (.env + "
            "`gcloud auth application-default login`, then run the pipeline) or "
            "export snapshots: `python pipeline_and_tests/run_pipeline.py --export-snapshots`."
        )
        st.stop()
    if source == "snapshot":
        st.warning("Snapshot mode: reading local CSV exports, not live BigQuery.")

    health = marts["mart_customer_health"]
    sku = marts["mart_sku_adoption"]
    feature = marts["mart_feature_adoption"]
    for df in (health, sku, feature):
        df["month_start"] = pd.to_datetime(df["month_start"])

    view = st.sidebar.radio(
        "View", ["Portfolio", "Live · Month-to-Date", "Customer Drill-Down", "Product & Feature"])
    st.sidebar.caption(f"Data source: {'BigQuery (live)' if source == 'bigquery' else 'CSV snapshot'}")
    if view == "Portfolio":
        portfolio_view(health, sku, feature)
    elif view == "Live · Month-to-Date":
        mtd_view(marts["mart_customer_health_mtd"])
    elif view == "Customer Drill-Down":
        customer_view(health, sku)
    else:
        product_view(sku, feature)


main()
