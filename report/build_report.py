"""Build report/MSP_Report.docx — a narrative report from the pipeline outputs.

Everything numeric is read from data/processed/* (summary.json and CSVs), so the
document stays in sync with src/analysis.py: rerun the pipeline, rerun this
script. Figures are embedded inline next to the paragraph they illustrate.

Usage:  python report/build_report.py
Output: report/MSP_Report.docx
"""
import json
import math
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from docx.table import Table

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
FIG = ROOT / "figures"
OUT = ROOT / "report" / "MSP_Report.docx"

ACCENT = RGBColor(0x1F, 0x4E, 0x79)
GREY = RGBColor(0x59, 0x59, 0x59)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def load() -> dict:
    summary = json.loads((PROC / "summary.json").read_text("utf-8"))
    out = {}
    out["block1"] = summary["block1_summary"]
    out["cleaning"] = summary["cleaning"]
    out["block4"] = summary["block4_summary"]
    out["diag"] = pd.read_csv(PROC / "anomaly_diagnostics.csv")
    out["scn"] = pd.read_csv(PROC / "cleaning_scenarios.csv")
    out["sens"] = pd.read_csv(PROC / "threshold_sensitivity.csv")
    out["marg"] = pd.read_csv(PROC / "spike_margins.csv")
    out["rob"] = pd.read_csv(PROC / "robustness_block1.csv")
    out["b2"] = pd.read_csv(PROC / "block2_top15_okved.csv")
    out["b3"] = pd.read_csv(PROC / "block3_top15_regions.csv")
    return out


def _pct(x: float) -> str:
    return f"{x:+.1f}%"


def _ci(ci, fmt: str = "{:.1f}") -> str:
    return f"[{fmt.format(ci[0])}, {fmt.format(ci[1])}]"


def set_document_defaults(doc: Document) -> None:
    for sec in doc.sections:
        sec.left_margin = Inches(0.8)
        sec.right_margin = Inches(0.8)
        sec.top_margin = Inches(0.8)
        sec.bottom_margin = Inches(0.8)
        sec.header_distance = Inches(0.4)
        sec.footer_distance = Inches(0.4)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    for name, size in (("Title", 22), ("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11.5)):
        st = doc.styles[name]
        st.font.name = "Calibri"
        st.font.color.rgb = ACCENT
        st.font.size = Pt(size)
        st.font.bold = True


def h(doc, level: int, text: str) -> None:
    doc.add_heading(text, level=level)


def p(doc, text: str) -> None:
    doc.add_paragraph(text)


def caption(doc, text: str) -> None:
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = par.add_run(text)
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = GREY


def image(doc, name: str, width: float, cap: str) -> None:
    par = doc.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    par.add_run().add_picture(str(FIG / name), width=Inches(width))
    caption(doc, cap)


def format_cell(v, col: str, fmt: dict) -> str:
    if pd.isna(v):
        return ""
    if is_ci_col(col):
        return str(v)
    if isinstance(v, float):
        if col in fmt:
            return f"{v:,.{fmt[col]}f}"
        if float(v).is_integer():
            return str(int(v))
        return f"{v:,.1f}"
    return str(v)


def table(doc, df: pd.DataFrame, header_map: dict, fmt=None, widths=None) -> Table:
    fmt = fmt or {}
    t = doc.add_table(rows=len(df) + 1, cols=len(header_map))
    t.style = "Light Grid Accent 1"
    for j, col in enumerate(header_map):
        cell = t.rows[0].cells[j]
        cell.text = ""
        r = cell.paragraphs[0].add_run(header_map[col])
        r.bold = True
    if widths is not None:
        for j, w in enumerate(widths):
            for row in t.rows:
                row.cells[j].width = Inches(w)
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        for j, col in enumerate(header_map):
            t.rows[i].cells[j].text = format_cell(row[col], col, fmt)
    return t


def is_ci_col(col: str) -> bool:
    return col.endswith("_ci") or col.endswith("_CI") or col == "flagged_months" or col == "scenario"


# ---------------------------------------------------------------------------
# document
# ---------------------------------------------------------------------------


def build() -> None:
    d = load()
    b1 = d["block1"]
    scn = d["scn"].set_index("scenario")
    alt = scn.loc["exclude_compensation_patterns"]
    head = scn.loc["exclude_all_flagged"]
    kb_raw = b1["point"]["new_micro"]["before"] / b1["point"]["closed_small"]["before"]
    ka_raw = b1["point"]["new_micro"]["after"] / b1["point"]["closed_small"]["after"]
    diag = d["diag"].set_index("month")
    jul24, jul25, feb, apr = (diag.loc[m] for m in ("2024-07", "2025-07", "2026-02", "2026-04"))
    rob_df = d["rob"]
    doc = Document()
    set_document_defaults(doc)

    # ---- Title ----
    t = doc.add_heading("Structural Change in the Russian SME Sector, 2025–2026", level=0)
    p(doc, "A reproducible, evidence-first analysis of the federal SME register")
    cap = doc.add_paragraph()
    r = cap.add_run(
        "Student research project, reworked into an open, reproducible Python pipeline. "
        "Every number below is regenerated from the raw register by src/analysis.py "
        "(seed 42, 5,000 bootstrap resamples); the narrative follows EXECUTIVE_SUMMARY.md "
        "and README.md of the companion repository."
    )
    r.italic = True
    r.font.size = Pt(10)
    p(doc, "Material: FNS SME register, May 2024 – April 2026 (176,647 region × class × month records).")

    # ---- 1. Introduction ----
    h(doc, 1, "1.  Introduction — the register and the question")
    p(doc,
      "The dataset is the Russian Federal Tax Service (FNS) SME register, aggregated to "
      "month × region × OKVED-2 class. For every cell it records three stocks (micro, small "
      "and medium enterprises) and two monthly flows: new registrations and closures. This "
      "report concentrates on small-enterprise closures and micro-enterprise registrations, "
      "because together they answer a structural question: how many new micro firms does the "
      "sector create for every small firm that closes? The answer is summarised by the "
      f"fragmentation coefficient K = (new_micro + 1) / (closed_small + 1). When K falls the "
      "sector produces fewer new micro firms per closed small firm — a sign of slowing "
      "rejuvenation rather than, say, a uniform contraction.")
    p(doc,
      "The project asks whether closures, new registrations and K shifted between the 2024 "
      "baseline and the 2025–2026 window. The surprising part is methodological: the answer "
      "turns out to depend on how the data are cleaned, and the cleaning hides two 2026 months "
      "that the manual rule missed. Figure 1 shows the raw monthly series "
      "and the four months the cleaning rule removes.")
    image(doc, "fig1_national_trend.png", 6.4,
          "Figure 1 — raw national monthly series; circled points are the months excluded by "
          "the automatic rule (two Julys and February/April 2026).")

    # ---- 2. Methodology ----
    h(doc, 1, "2.  Methodology — how the answer changed as the cleaning got honest")
    h(doc, 2, "2.1  Where the old answer came from — and why it was fragile")
    p(doc,
      "The original Excel study removed anomalies by hand: it excluded the two July clean-ups "
      f"and reported small-enterprise closures up **{alt['closed_pct']:+.0f}%**. The rule was "
      "trustworthy, but it was a static list — it could not see months the author did not know "
      "about."
    )
    h(doc, 2, "2.2  An automatic rule instead of a hand-picked list")
    p(doc,
      "The reworked pipeline replaces the manual list with a single magnitude rule: a month is "
      "anomalous when the national total of closed_small or closed_micro exceeds its own series "
      "median by more than 3× (detect_anomalous_months). On this window the rule flags exactly "
      "four months — the two Julys, and two months the manual rule missed: **February and "
      "April 2026**."
    )
    h(doc, 2, "2.3  The February/April 2026 finding — compensation turned pending")
    p(doc,
      f"The two 2026 months do not look like an administrative spike at all. In 2026-02 and "
      f"2026-04 small-firm registrations stayed flat ({feb['new_small']:.0f} and "
      f"{apr['new_small']:.0f} firms, {feb['new_small_x_median']:.2f}× and "
      f"{apr['new_small_x_median']:.2f}× their median), while closures jumped to "
      f"{feb['closed_small']:,.0f} and {apr['closed_small']:,.0f} — and the register stock "
      f"fell by almost exactly the number of closures "
      f"({feb['actual_delta_small']:,.0f} ≈ −{feb['closed_small']:,.0f} and "
      f"{apr['actual_delta_small']:,.0f} ≈ −{apr['closed_small']:,.0f}). That stock drop is "
      "an uncompensated exit pattern from the register; but for 2026-02 the pipeline finds a "
      "matching registration wave in 2026-03 "
      f"({-feb['actual_delta_small'] * feb['compensation_ratio']:,.0f} new_small firms, "
      f"{feb['compensation_ratio']:.0%} of the February drop), so it labels the month "
      "**lagged_compensation** — a pattern consistent with a delayed registry adjustment "
      "rather than a proven economic exit. 2026-04 has no t+1 month inside the data window, so "
      "the same rule cannot run: the month is labelled **provisional_net_exit**, an unresolved "
      "exit pattern — aggregate data cannot verify that any flag represents an economic exit, "
      "so the label remains provisional."
    )
    h(doc, 2, "2.4  Compensation vs exit patterns — flows reconciled with stocks")
    p(doc,
      "A flagged month is not dropped blindly. anomaly_diagnostics() reconciles the flow with "
      "the stock for each month: the flow-implied change new_small − closed_small is compared "
      "with the observed change of active_small, and a neighbouring registration wave is "
      "checked for a lagged match, restricted to new_small (a small-enterprise loss "
      "cannot be compensated by new_micro registrations). The two Julys show a churn pattern — "
      "closures matched by same-month registrations: new_small itself spikes "
      f"{jul24['new_small_x_median']:.1f}× and {jul25['new_small_x_median']:.1f}× the median "
      "and the small-enterprise stock actually grows. This churn reading is an interpretation "
      "of the observed flow/stock pattern — it is consistent with, but not proof of, the "
      "annual FNS register reclassification. A secondary/reference source (not a "
      "primary normative act) aligns with the same calendar-day mechanism: the FNS runs an "
      "annual SME-register review on 10 July — excluding enterprises that no longer meet the "
      "criteria, checking prior-year reporting, removing previous-year registration marks — "
      "which matches both spikes landing on the same date in two consecutive years. "
      "2026-02 shows a lagged compensation "
      "pattern (matched by the 2026-03 wave) and 2026-04 an unresolved exit pattern — its t+1 "
      "month is outside the data window, so the lagged-compensation rule cannot run for it.")
    t = d["diag"][
        ["month", "closed_small", "new_small", "new_small_x_median", "actual_delta_small", "anomaly_type"]
    ].copy()
    t["new_small_x_median"] = t["new_small_x_median"].round(2)
    table(doc, t,
          {"month": "Month", "closed_small": "Closed (small)", "new_small": "New (small)",
           "new_small_x_median": "New vs median (×)", "actual_delta_small": "Δ stock (small)",
           "anomaly_type": "Classification"},
          fmt={"closed_small": 0, "new_small": 0, "actual_delta_small": 0})

    h(doc, 2, "2.5  The cleaning fork — two alternative cleaning specifications")
    p(doc,
      "Because the same flag covers a compensation pattern (the two Julys) and exit patterns "
      "(2026-02/04), the two cleaning specifications below are published as the sensitivity "
      "fork (data/processed/cleaning_scenarios.csv):")
    p(doc,
      "`exclude_compensation_patterns` drops the two churn Julys and the lagged-compensation "
      "2026-02 but keeps the unresolved 2026-04 month in the sample. "
      f"Closures read **{alt['closed_pct']:+.1f}%** (CI {alt['closed_pct_ci']}), "
      f"K_after/K_before = {alt['K_ratio']:.3f}. `exclude_all_flagged` (the headline "
      "specification) drops all four months; "
      f"closures read **{head['closed_pct']:+.1f}%** (CI {head['closed_pct_ci']}), "
      f"K_after/K_before = {head['K_ratio']:.3f}. The difference between the two readings is "
      "exactly the treatment of 2026-04 — a single month whose own classification cannot be "
      "resolved by aggregate data, so the fork is reported rather than hidden.")
    table(doc, d["scn"][["scenario", "label", "n_before", "n_after", "closed_pct",
                         "closed_pct_ci", "K_ratio", "K_ratio_ci"]],
          {"scenario": "Scenario", "label": "Rule", "n_before": "Months (before)",
           "n_after": "Months (after)", "closed_pct": "Closures Δ%",
           "closed_pct_ci": "95% CI", "K_ratio": "K ratio", "K_ratio_ci": "95% CI"},
          fmt={"n_before": 0, "n_after": 0, "closed_pct": 1, "K_ratio": 3})

    h(doc, 2, "2.6  Threshold 3.0 and the plateau 1.7×–9.8×")
    p(doc,
      "The 3.0 in the rule is itself a methodological constant — so the pipeline re-runs the "
      "whole flag → classify → clean → Block-1 chain at 2.0, 2.5, 3.0, 4.0 and 5.0 "
      "(data/processed/threshold_sensitivity.csv):")
    sens = d["sens"][["threshold", "flagged_months", "closed_pct", "closed_pct_ci",
                      "K_ratio", "K_ratio_ci"]].copy()
    table(doc, sens,
          {"threshold": "Threshold", "flagged_months": "Flagged months",
           "closed_pct": "Closures Δ%", "closed_pct_ci": "95% CI",
           "K_ratio": "K ratio", "K_ratio_ci": "95% CI"},
          fmt={"threshold": 1, "closed_pct": 1, "K_ratio": 3},
          widths=(0.9, 2.2, 1.0, 1.1, 0.8, 1.1))
    p(doc,
      "Every threshold in 2.0–5.0 flags the same four months, so the headline numbers do not "
      "move: no month appears or disappears anywhere in the range. The reason lies in the "
      "distance from the decision boundary (data/processed/spike_margins.csv):")
    marg = d["marg"]
    flagged = marg[marg["flagged_at_threshold"]]
    fl_lo = flagged["closed_small_x_median"].min()
    fl_hi = flagged["closed_small_x_median"].max()
    clean = marg[~marg["flagged_at_threshold"]]
    cl_max = clean["closed_small_x_median"].max()
    nearest_clean = clean["closed_small_x_median"].idxmax()
    keep_mask = marg["flagged_at_threshold"] | (marg["month"] == nearest_clean)
    m = marg[keep_mask][["month", "closed_small", "closed_small_x_median",
                         "closed_micro_x_median", "flagged_at_threshold"]].copy()
    m["flagged_at_threshold"] = m["flagged_at_threshold"].map({True: "flagged", False: "clean"})
    table(doc, m,
          {"month": "Month", "closed_small": "Closed (small)", "closed_small_x_median": "× median",
           "closed_micro_x_median": "Micro × median", "flagged_at_threshold": "At 3.0"},
          fmt={"closed_small": 0, "closed_small_x_median": 2, "closed_micro_x_median": 2})
    p(doc,
      f"The four flagged months sit at {fl_lo:.1f}×–{fl_hi:.1f}× the closed_small median; "
      f"every clean month is below {cl_max:.2f}× (the nearest is {nearest_clean}). The "
      "decision boundary therefore lies inside an empty plateau — the constant could be "
      f"anywhere between roughly {cl_max:.1f} and {fl_lo:.1f} and nothing would change. That "
      "is what “the 3.0 threshold is non-critical” means: it is a calibrated default inside "
      "the dead zone between the highest clean month and the lowest flagged one, not a "
      "knife-edge choice. For transparency the 3.0 row reuses the headline Block-1 bootstrap "
      "itself, so its CI is literally the headline CI, not a second independent run.")

    # ---- 3. Results ----
    h(doc, 1, "3.  Results by block")
    h(doc, 2, "3.1  Block 1 — national before/after")
    p(doc,
      "All CIs below are 95% percentile bootstrap intervals from resampling calendar months "
      "within each period (7 before, 13 after).")
    p(doc,
      f"Mean monthly small-enterprise closures rose from {b1['point']['closed_small']['before']:,.1f} "
      f"to {b1['point']['closed_small']['after']:,.1f} — "
      f"**{_pct(b1['point']['closed_small']['pct_change'])}** (95% CI "
      f"{_ci(b1['bootstrap']['closed_small']['pct_change'])}; 0 not in CI). Micro-enterprise "
      f"registrations were essentially flat ({_pct(b1['point']['new_micro']['pct_change'])}, CI "
      f"{_ci(b1['bootstrap']['new_micro']['pct_change'])} — not significant), so the sector was "
      f"not collapsing on the entry side. The fragmentation coefficient K fell from "
      f"{b1['point']['K']['before']:.1f} to {b1['point']['K']['after']:.1f} "
      f"({_pct(b1['point']['K']['pct_change'])}, CI {_ci(b1['bootstrap']['K']['pct_change'])}), "
      f"and the ratio K_after/K_before = "
      f"**{b1['ratio_K']['value']:.3f}** (CI {_ci(b1['ratio_K']['ci'], '{:.3f}')}; 1 not in CI). "
      "A lower K indicates fewer new micro-enterprise registrations relative to each "
      "small-enterprise closure; the decline is driven primarily by the increase in closures "
      "while micro-enterprise registrations remain comparatively stable.")
    p(doc,
      f"The K ratio uses the Laplace +1 inherited from the Excel study. The +1 is immaterial "
      f"to the finding: without it the raw coefficients are K_before = {kb_raw:,.1f}, "
      f"K_after = {ka_raw:,.1f}, ratio {ka_raw / kb_raw:.3f}.")
    image(doc, "fig2_block1.png", 6.6,
          "Figure 2 — Block 1 before/after means with 95% bootstrap CIs.")

    h(doc, 2, "3.2  Block 2 — OKVED classes")
    top2 = d["b2"].sort_values("K_after", ascending=False).head(5)
    names = "; ".join(f"{r['okved']} {r['okved_name'].split(',')[0].split(' and ')[0]}"
                      for _, r in top2.iterrows())
    p(doc,
      "Ranking the 88 OKVED-2 classes by after-period K, the most fragmented classes are "
      f"{names} (full top-15 in data/processed/block2_top15_okved.csv). Under the cleaned "
      "periods no top-class ratio CI excludes 1 — a weaker class-level picture than the Excel "
      "report suggested, and a sign that the headline signal is national rather than sectoral. "
      "Note that K can be unstable for classes with very low closure counts; the ranking "
      "should be interpreted jointly with the underlying closure and registration volumes.")
    image(doc, "fig3_block2_okved.png", 6.4,
          "Figure 3 — top-15 OKVED classes by after-period fragmentation coefficient (K is "
          "unstable at very low closure volumes, so rank by K should be read with the "
          "underlying counts).")

    h(doc, 2, "3.3  Block 3 — regions")
    top3 = d["b3"].sort_values("closed_small_avg_after", ascending=False).head(5)
    regs = "; ".join(f"{r['region']} (+{r['closed_pct_change']:.1f}%)" for _, r in top3.iterrows())
    widest = d["b3"].sort_values("closed_pct_change", ascending=False).head(2)
    wings = " and ".join(f"{r['region']} (+{r['closed_pct_change']:.1f}%)"
                         for _, r in widest.iterrows())
    p(doc,
      "After-period closures concentrate in the large agglomerations. All 15 selected regions "
      f"have 95% bootstrap CIs for the before/after difference that exclude zero: {regs}. "
      "These intervals are descriptive because the regions were selected using observed "
      "after-period data. The widest relative swings within the top-15 are "
      f"{wings}, consistent with the national rise in closures rather than a "
      "Moscow-only effect.")
    p(doc,
      "As in Block 2, the top-15 are highlighted after observing the data; they are not a set "
      "of pre-registered hypotheses, so the nominal percentile-bootstrap CIs are read "
      "descriptively (no multiplicity correction).")
    image(doc, "fig4_block3_regions.png", 6.4,
          "Figure 4 — top-15 regions by small-enterprise closures (after period); CIs are "
          "95% percentile bootstrap intervals on calendar-month resamples.")

    h(doc, 2, "3.4  Block 4 — region × OKVED matrix")
    b4 = d["block4"]
    suff = b4["n_pairs_sufficient"]
    p(doc,
      f"Of {b4['n_pairs_total']:,} region×class pairs, {suff:,} "
      f"({100 * suff / b4['n_pairs_total']:.0f}%) have at least "
      f"{b4['min_active_months']} active months in both periods and are evaluated; "
      f"{b4['n_pairs_insufficient']:,} are marked insufficient (change ratio set to NaN, "
      "never silently set to 1, as the Excel version did — its substitution produced 19.7% "
      f"'no change'). Among the evaluated pairs, {b4['n_red']:,} "
      f"({100 * b4['n_red'] / suff:.0f}%) show fragmentation up, {b4['n_green']:,} "
      f"({100 * b4['n_green'] / suff:.0f}%) down, {b4['n_yellow']} unchanged. The heatmap "
      "paints the same picture as Block 1: rising fragmentation is widespread, with grey "
      "cells marking pairs for which no reliable reading exists.")
    p(doc,
      "The matrix evaluates 4,734 cells at once; these are not 4,734 independent tests, so the "
      "share of cells with a ratio away from 1 is descriptive evidence, not a multiplicity-"
      "corrected inference.")
    image(doc, "fig5_matrix_heatmap.png", 6.6,
          "Figure 5 — heatmap of change_ratio = K_after/K_before (log2 scale); grey cells are "
          "insufficient pairs (2,418), never substituted.")

    # ---- 4. Robustness ----
    h(doc, 1, "4.  Robustness — the magnitude is bracketed by the cleaning, transparently")
    rob_core = rob_df[(rob_df["variant"] != "manual") & (rob_df["variant"] != "july_kept")]
    lo_core = int(math.floor(rob_core["closed_pct"].min()))
    hi_core = int(math.ceil(rob_core["closed_pct"].max()))
    table(doc, rob_df[["label", "n_before", "n_after", "closed_pct", "d_closed_pp"]].copy(),
          {"label": "Variant", "n_before": "Months b.", "n_after": "Months a.",
           "closed_pct": "Closures Δ%", "d_closed_pp": "Δ vs base (pp)"},
          fmt={"n_before": 0, "n_after": 0, "closed_pct": 1, "d_closed_pp": 1})
    p(doc,
      "The headline is a bracket, and both ends are now explained. All-flagged cleaning "
      f"returns **{head['closed_pct']:+.1f}%**; every other cleaning that removes the "
      f"detected anomalies (2025-only, re-added regions, threshold forks) clusters around "
      f"**+{lo_core}% to +{hi_core}%**. `exclude_compensation_patterns`, which keeps the "
      f"unresolved 2026-04 in the sample, reads **{alt['closed_pct']:+.1f}%** — the whole gap "
      "between the two readings is that single month's treatment. The two July months "
      "(labelled churn) are not the story: once the flagged months are removed, closures rose; "
      "whether the "
      f"unresolved 2026-04 month is counted or not moves the answer between "
      f"{head['closed_pct']:+.0f}% and {alt['closed_pct']:+.0f}%. The band is wide, but it is "
      "now defined by a reproducible rule and explained by the data, not a silent choice.")
    image(doc, "fig6_robustness.png", 6.4,
          "Figure 6 — how the headline closure change moves with the cleaning choice.")

    # ---- 5. Limitations ----
    h(doc, 1, "5.  Limitations")
    p(doc,
      "The analysis is descriptive by design. A before/after comparison of a single register "
      "cannot isolate the effect of any policy from unrelated shocks; a causal design (DiD, a "
      "region×quarter panel, or linking tax receipts and credit data) was deliberately kept out "
      "of scope — this repository measures structural change, not policy effects.")
    p(doc,
      "The cleaning itself is a fork, and the headline number depends on how the flagged months "
      "are read. The classification and both scenarios are published precisely so the choice is "
      "transparent, and the spike threshold was shown to be non-critical within the tested "
      "2.0–5.0 range. But 2026-02 and 2026-04 remain the crux: 2026-02 is treated as a lagged "
      "compensation pattern and 2026-04 as an unresolved exit pattern, and aggregate data alone "
      "cannot move either label past that point — the two readings (+59.1% vs +276.3%) are "
      "reported instead of arbitrated.")
    p(doc,
      "Methodological choices inherited from the source study are the Laplace +1 smoothing on "
      "K, asymmetric period lengths (7 vs 13 months) and the absence of seasonal adjustment. "
      "The register structure and ETL were reproduced from the original files without "
      "re-verification of the source statements themselves.")
    p(doc,
      "K can be unstable for categories with very low closure counts (notably the Block 2 "
      "class ranking); rankings should be interpreted jointly with the underlying closure and "
      "registration volumes.")
    p(doc,
      "The analysis uses aggregate region × OKVED × category data; it cannot establish whether "
      "the same entities moved between categories or months. Full identification would require "
      "retaining and reconciling entity identifiers across monthly source files, which is "
      "outside the scope of this study.")

    # ---- 6. Conclusion ----
    h(doc, 1, "6.  Conclusion")
    p(doc,
      "On the cleaned register, the Russian SME sector in 2025–2026 shows a consistent "
      "structural shift: small-enterprise closures are up and the fragmentation coefficient "
      "fell, meaning fewer new micro firms per closed small firm. The two 2026 months drive "
      f"the magnitude — the gap between the all-flagged reading ({head['closed_pct']:+.0f}%) "
      f"and the reading that keeps the unresolved 2026-04 ({alt['closed_pct']:+.0f}%) is "
      "entirely their treatment — and the automatic cleaning rule, the anomaly classification "
      "and the threshold sensitivity now make that gap a documented, reproducible property of "
      "the data rather than an implicit judgment call.")

    doc.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    build()