"""Descriptive analysis of the Russian SME register (May 2024 - April 2026).

This script ports the original Excel-based analysis (MSP_Fragmentation_Analysis
.xlsx) to a reproducible pandas pipeline. It is explicitly a DESCRIPTIVE study
of structural changes in the SME register --- not a causal study. No causal
estimator (difference-in-differences, region x quarter panel, 1-NOM tax
records, CBR key-rate series) is implemented or intended. Tax receipts
(form 1-NOM) and credit (CBR key-rate) data are deliberately not linked.
Where the causal question is relevant, it is reported as a limitation in
the text.

Cleaning decisions:
  * regions with FNS codes 00, 90, 93, 94, 95 are excluded (in the raw CSV the
    service code 0 is stored as "00"); on the available window this removes the
    FNS service row and four regions with incomplete data;
  * months are NOT excluded by a hand-picked list: detect_anomalous_months()
    flags a calendar month when its national closed_small or closed_micro total
    exceeds the respective series median by more than `threshold` times
    (default 3.0). On the available window this flags 2024-07, 2025-07,
    2026-02 and 2026-04;
  * every flagged month is additionally classified by flow/stock reconciliation
    (anomaly_diagnostics): "churn" (same-month compensation pattern - closures
    matched by same-month registrations, e.g. the two Julys), "lagged_compensation"
    (closures followed by a neighbouring-month registration spike close in
    magnitude to the stock loss — 2026-02 matches the 2026-03 new_small wave;
    consistent with a delayed registry adjustment, but not proof of one), and
    "provisional_net_exit" (uncompensated exit pattern on the available window
    - active stock falls by roughly the closures, e.g. 2026-04). Aggregate
    data alone cannot distinguish an economic exit from an administrative
    registry operation (reclassification, category/OKVED/region change), so
    provisional_net_exit is always a pattern label, not a verified exit; and
    2026-04 raises an explicit warning because its t+1 month is outside the
    data window, so the lagged-compensation check cannot be run at all.
    Months that fit neither pattern are NOT dropped and raise a warning;
  * "before" = calendar year 2024 (May-Dec present, 7 months after cleaning);
  * "after"  = Jan 2025 - Apr 2026 (13 months after cleaning).

Statistics
  * mean monthly values per period for closed_small and new_micro;
  * Laplacian (smoothed) fragmentation coefficient
        K = (new_micro_avg + 1) / (closed_small_avg + 1);
  * 95% bootstrap confidence intervals (resampling calendar months within each
    period, 5000 iterations by default) for each statistic, for the period
    difference, and for the ratio K_after / K_before.

Run: python src/analysis.py [--seed 42] [--iters 5000]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make `python src/analysis.py` work: add the repo root to sys.path so that
# `from src.region_lookup import ...` resolves when run as a plain script.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.region_lookup import okved_name_en, region_name_en

# ---------------------------------------------------------------------------
# Paths and cleaning decisions (ported 1:1 from the project notes)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
RAW_CSV = ROOT / "data" / "raw" / "msp_final.csv"
PROCESSED_DIR = ROOT / "data" / "processed"
FIGURES_DIR = ROOT / "figures"

# Regions excluded because of incorrect/incomplete data (see 3.2 in notes).
# The raw register stores region "0" zfill(2)-ed as "00".
EXCLUDED_REGIONS = {"00", "90", "93", "94", "95"}

# A month is treated as an administrative artefact when its national
# closed_small or closed_micro total exceeds the series median by more than
# this multiple. Used by detect_anomalous_months() to replace the manual month
# list of the Excel study (which knew only about the two Julys).
MONTH_SPIKE_THRESHOLD = 3.0

# Flow/stock reconciliation thresholds used by anomaly_diagnostics() to tell
# "compensation" (churn) apart from "uncompensated exit pattern" (provisional_net_exit):
#   * NEW_SPIKE_THRESHOLD - new_small is itself an anomaly when it exceeds
#     this multiple of its series median;
#   * compensation/-exit tolerances - how close actual_delta_small must be to
#     "fully compensated by registrations" (>= -tol * closed_small) or to a
#     real stock drop (~= -closed_small, +/- tol) for a confident label.
NEW_SPIKE_THRESHOLD = 2.0
CHURN_COMPENSATION_TOLERANCE = 0.2
NET_EXIT_TOLERANCE = 0.2

# Minimum number of months (within a period) with observed activity that a
# (region, okved) pair must have to be treated as "sufficient" in Block 4.
MIN_ACTIVE_MONTHS = 3

# Metrics under study (columns of msp_final.csv).
METRICS = ("closed_small", "new_micro")


# ---------------------------------------------------------------------------
# Data loading and cleaning
# ---------------------------------------------------------------------------


def load_register(path: Path = RAW_CSV) -> pd.DataFrame:
    """Load the raw SME register keeping month/region/okved as strings.

    The raw CSV stores month as "YYYY-MM" and region codes with leading zeros
    ("00", "01", ...). We must read region and okved as strings so codes are
    never coerced to integers (which would drop leading zeros).
    """
    df = pd.read_csv(path, dtype={"month": str, "region": str, "okved": str})
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
    # All flow/stock columns are numeric downstream (detect_anomalous_months,
    # anomaly_diagnostics and the Block aggregations all sum them); coerce once
    # here so a stray non-numeric cell cannot crash groupby().sum() later.
    # Coercion is kept permissive (errors="coerce") on purpose, but it is
    # diagnostic: any cell that turns NaN *because of* coercion (a value that
    # was not already NaN) is reported as a warning, not silently swallowed.
    numeric_cols = ["closed_small", "closed_micro", "new_small", "new_micro",
                    "active_small", "active_micro"]
    for col in numeric_cols:
        was_na = df[col].isna()
        df[col] = pd.to_numeric(df[col], errors="coerce")
        new_na = df[col].isna() & ~was_na
        if new_na.any():
            bad_months = df.loc[new_na, "month"].dt.strftime("%Y-%m").unique()
            bad_rows = int(new_na.sum())
            print(f"  [WARNING] load_register: column '{col}' — {bad_rows} non-numeric "
                  f"value(s) coerced to NaN (errors='coerce'); affected months: "
                  f"{', '.join(sorted(str(m) for m in bad_months))}")
    return df


def detect_anomalous_months(df: pd.DataFrame, threshold: float = MONTH_SPIKE_THRESHOLD) -> list[str]:
    """Detect calendar months with an administrative spike in the raw register.

    The raw register stores one row per (month, region, OKVED). We sum each
    metric over the WHOLE register --- all regions, all classes, no months or
    regions excluded --- to get one national value per metric and month. A month
    is flagged as anomalous when either its national closed_small total or its
    national closed_micro total exceeds the respective series median by more
    than `threshold` times.

    This is the automatic rule that replaces the hardcoded month list of the
    Excel study: the two Julys (annual register clean-up) and the 2026-02 /
    2026-04 waves are all found by the same single rule.

    Returns the sorted list of "YYYY-MM" strings to be logged/excluded.
    """
    key = df["month"].dt.strftime("%Y-%m")
    totals = df.groupby(key)[["closed_small", "closed_micro"]].sum()
    median = totals.median()
    exceeds = totals > median * threshold
    flagged = exceeds.any(axis=1)
    return sorted(flagged.index[flagged.values].tolist())


def anomaly_diagnostics(
    df: pd.DataFrame,
    flagged_months=None,
    new_spike_threshold: float = NEW_SPIKE_THRESHOLD,
    compensation_tolerance: float = CHURN_COMPENSATION_TOLERANCE,
    net_exit_tolerance: float = NET_EXIT_TOLERANCE,
) -> tuple[pd.DataFrame, list[str]]:
    """Classify flagged months as compensation patterns vs provisional exit patterns.

    The magnitude filter (detect_anomalous_months) only tells us that a month
    has an abnormal *flow*. The same signal can mean two very different things:

      * compensation (churn) - the registry's category stock is reshuffled
        (e.g. the annual July methodology change): closures are matched by
        same-month registrations, so the active stock does not actually fall;
      * provisional_net_exit - the flows match an uncompensated exit pattern:
        registrations stay normal and the active stock drops by (roughly) the
        number of closures. The label is deliberately provisional: aggregate
        flows alone cannot distinguish an economic exit from an administrative
        registry operation (reclassification, category/OKVED/region change),
        because the code does not track individual firms between months.

    For every flagged calendar month we compute, over the whole raw register:

      implied_delta_small  = new_small - closed_small        (micro likewise)
      net_effect_ratio     = implied_delta / (-closed_small) (micro likewise)
      actual_delta_small   = active_small(month) - active_small(prev month)

    Classification (on small enterprises, per the study's register of flows):
      * churn              new_small is its own spike (> new_spike_threshold x
                           median of new_small) AND
                           actual_delta_small >= -compensation_tolerance * closed_small
                           (closures fully matched by registrations, stock flat
                           or up);
      * lagged_compensation  same-month compensation is absent, but a
                           *neighbouring* month (t+1 first, t-1 as a defensive
                           check) shows a registration spike close in magnitude
                           to the stock loss (within compensation_tolerance,
                           20%): the closures reappear one month later, so the
                           loss is not necessarily a permanent exit. 2026-02 is
                           matched by the 2026-03 new_small wave (4,907 vs a
                           -5,253 stock loss), which is consistent with a
                           delayed registry adjustment; the compensation
                           month/metric/ratio are recorded;
      * provisional_net_exit  new_small is NOT a spike AND
                           actual_delta_small ~= -closed_small
                           (within +/- net_exit_tolerance). The label reflects
                           the aggregate flows AND the *available* window: it
                           does not track individual firms, so an administrative
                           reclassification would also look like this in a single
                           month. It is a pattern label, not a verified exit;
      * unclassified      neither pattern matches clearly -> the month is NOT
                           dropped automatically; a warning with the figures is
                           returned instead.

    A flagged month whose t+1 month falls outside the data window cannot be
    checked by the lagged-compensation rule (there is no data to look at); this
    is raised as an explicit warning rather than silently skipped. On this window
    that is 2026-04 (no 2026-05 in the register).

    Returns (diagnostics, warnings). diagnostics is one row per flagged month
    (sorted), columns: month, flows, implied/actual deltas, net-effect ratios,
    multiples-of-median, the anomaly_type and (for lagged_compensation) the
    compensation month/metric/ratio. warnings lists any month left
    unclassified together with the figures that failed the check, and any
    flagged month for which the lagged-compensation rule could not run.
    """
    if flagged_months is None:
        flagged_months = detect_anomalous_months(df)
    flagged = set(flagged_months)

    key = df["month"].dt.strftime("%Y-%m")
    flow_stock = ["closed_small", "new_small", "active_small",
                  "closed_micro", "new_micro", "active_micro"]
    totals = df.groupby(key)[flow_stock].sum().sort_index()
    median = totals.median()
    index = totals.index

    rows, warnings = [], []
    prev = None
    for i, month in enumerate(index):
        cur = totals.loc[month]
        closed_s, closed_m = cur["closed_small"], cur["closed_micro"]
        row = {
            "month": month,
            "closed_small": float(closed_s),
            "new_small": float(cur["new_small"]),
            "closed_micro": float(closed_m),
            "new_micro": float(cur["new_micro"]),
            "implied_delta_small": float(cur["new_small"] - closed_s),
            "implied_delta_micro": float(cur["new_micro"] - closed_m),
            "net_effect_ratio_small": (float(cur["new_small"] - closed_s) / -closed_s)
                                      if closed_s else np.nan,
            "net_effect_ratio_micro": (float(cur["new_micro"] - closed_m) / -closed_m)
                                      if closed_m else np.nan,
            "new_small_x_median": float(cur["new_small"]) / float(median["new_small"])
                                  if median["new_small"] else np.nan,
            "new_micro_x_median": float(cur["new_micro"]) / float(median["new_micro"])
                                  if median["new_micro"] else np.nan,
        }
        if prev is not None:
            row["actual_delta_small"] = float(cur["active_small"] - prev["active_small"])
            row["actual_delta_micro"] = float(cur["active_micro"] - prev["active_micro"])
        else:
            row["actual_delta_small"] = np.nan
            row["actual_delta_micro"] = np.nan
        prev = cur

        if month in flagged:
            neighbors = {}
            if i > 0:
                neighbors["t_minus_1"] = totals.loc[index[i - 1]]
            has_next = i + 1 < len(index)
            if has_next:
                neighbors["t_plus_1"] = totals.loc[index[i + 1]]
            anomaly_type, extra = _classify_anomaly_month(
                row, new_spike_threshold, compensation_tolerance,
                net_exit_tolerance, median, neighbors, warnings)
            row["anomaly_type"] = anomaly_type
            row.update(extra)
            row["active_small"] = float(cur["active_small"])
            row["active_micro"] = float(cur["active_micro"])
            if not has_next and anomaly_type != "churn":
                warnings.append(
                    f"{month}: cannot be classified by the lagged-compensation "
                    f"rule — no data for its t+1 month (outside the data "
                    f"window); classified as {anomaly_type} provisionally.")
            rows.append(row)

    diagnostics = pd.DataFrame(rows).sort_values("month").reset_index(drop=True)
    return diagnostics, warnings


def _find_lagged_compensation(loss: float, neighbors: dict, median,
                              new_spike_threshold: float, tolerance: float) -> dict | None:
    """Look for a neighbouring registration spike that matches a stock loss.

    `loss` is the observed active_small decline of the flagged month (> 0).
    A neighbouring month (t+1 first, then t-1 as a defensive check) counts as a
    lagged compensation when its new_small (or new_micro) is itself a spike
    (> new_spike_threshold x its median) AND its magnitude matches the loss
    within `tolerance` (20%), i.e. the closed volume reappears in the register
    around the same size.

    Returns a dict with compensation_month / compensation_metric /
    compensation_ratio, or None.
    """
    for label in ("t_plus_1", "t_minus_1"):
        if label not in neighbors:
            continue
        nm = neighbors[label]
        candidates = []
        if median["new_small"] and nm["new_small"] > new_spike_threshold * median["new_small"]:
            candidates.append(("new_small", nm["new_small"]))
        if median["new_micro"] and nm["new_micro"] > new_spike_threshold * median["new_micro"]:
            candidates.append(("new_micro", nm["new_micro"]))
        for metric, value in candidates:
            ratio = value / loss
            if (1 - tolerance) <= ratio <= (1 + tolerance):
                return {"compensation_month": nm.name,
                        "compensation_metric": metric,
                        "compensation_ratio": float(ratio)}
    return None


def _classify_anomaly_month(row: dict, new_spike_threshold: float,
                            compensation_tolerance: float,
                            net_exit_tolerance: float,
                            median, neighbors: dict,
                            warnings: list[str]) -> tuple[str, dict]:
    """Label one flagged month as churn / lagged_compensation / provisional_net_exit /
    unclassified.

    Returns (anomaly_type, extra). For lagged_compensation, `extra` carries the
    compensation month / metric / ratio found by the lagged-compensation check.
    """
    closed = row["closed_small"]
    new_spike = row["new_small_x_median"] > new_spike_threshold
    actual = row["actual_delta_small"]
    if np.isnan(actual) or closed == 0:
        warnings.append(
            f"{row['month']}: cannot classify (actual_delta_small={actual}, "
            f"closed_small={closed}); review manually.")
        return "unclassified", {}

    compensated = actual >= -compensation_tolerance * closed
    exit_match = (-(1 + net_exit_tolerance) * closed
                  <= actual <= -(1 - net_exit_tolerance) * closed)

    if new_spike and compensated:
        return "churn", {}

    loss = -actual
    if loss > 0:
        delayed = _find_lagged_compensation(loss, neighbors, median,
                                            new_spike_threshold, compensation_tolerance)
        if delayed is not None:
            return "lagged_compensation", delayed

    if (not new_spike) and exit_match:
        return "provisional_net_exit", {}

    warnings.append(
        f"{row['month']}: UNCLASSIFIED. closed_small={closed:.0f}, "
        f"new_small={row['new_small']:.0f} ({row['new_small_x_median']:.2f}x median), "
        f"actual_delta_small={actual:.0f} (implied {row['implied_delta_small']:.0f}, "
        f"net ratio {row['net_effect_ratio_small']:.3f}). Review manually.")
    return "unclassified", {}


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return float("nan")
    return float(a @ b / (norm_a * norm_b))


def profile_similarity(raw: pd.DataFrame, compensation: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional composition similarity of a lagged-compensation match.

    For every diagnostics row labelled lagged_compensation, the flagged month's
    closed_small totals by region and by 2-digit OKVED are compared with the
    compensation month's registration totals (compensation_metric) in the same
    dimensions, using cosine similarity and Spearman rank correlation. High
    values mean the compensating wave arrives in the same regions/classes as the
    losses — additional evidence consistent with a lagged registry adjustment.
    It does not establish that the same entities were involved: the profiles are
    aggregated, not entity-linked.

    Returns one row per compensation match: month, compensation_month,
    compensation_metric, region_cosine, region_spearman, okved_cosine,
    okved_spearman.
    """
    cols = ["month", "compensation_month", "compensation_metric",
            "region_cosine", "region_spearman", "okved_cosine", "okved_spearman"]
    if not len(compensation):
        return pd.DataFrame(columns=cols)
    key = raw["month"].dt.strftime("%Y-%m")
    rows = []
    for _, r in compensation.iterrows():
        if pd.isna(r["compensation_month"]):
            continue
        loss_month, comp_month, metric = r["month"], r["compensation_month"], r["compensation_metric"]
        a_reg = raw[key.eq(loss_month)].groupby("region")["closed_small"].sum()
        b_reg = raw[key.eq(comp_month)].groupby("region")[metric].sum()
        a_ok = raw[key.eq(loss_month)].groupby(raw["okved"].str[:2])["closed_small"].sum()
        b_ok = raw[key.eq(comp_month)].groupby(raw["okved"].str[:2])[metric].sum()
        m_reg = pd.concat([a_reg.rename("a"), b_reg.rename("b")], axis=1).fillna(0)
        m_ok = pd.concat([a_ok.rename("a"), b_ok.rename("b")], axis=1).fillna(0)
        rows.append({
            "month": loss_month,
            "compensation_month": comp_month,
            "compensation_metric": metric,
            "region_cosine": _cosine_sim(m_reg["a"].to_numpy(), m_reg["b"].to_numpy()),
            "region_spearman": m_reg["a"].corr(m_reg["b"], method="spearman"),
            "okved_cosine": _cosine_sim(m_ok["a"].to_numpy(), m_ok["b"].to_numpy()),
            "okved_spearman": m_ok["a"].corr(m_ok["b"], method="spearman"),
        })
    return pd.DataFrame(rows, columns=cols).reset_index(drop=True)


def assign_period(df: pd.DataFrame, excluded_months=()) -> pd.Series:
    """Assign period labels; months in `excluded_months` are dropped.

    before: calendar year 2024 (May-Dec present in the window)
    after:  January 2025 .. April 2026 inclusive
    exclude: rows outside the window or in a month listed in `excluded_months`
    (iterable of "YYYY-MM" strings).

    Month exclusion is deliberately NOT hardcoded: the caller passes the months
    flagged by detect_anomalous_months() (or an empty collection to keep every
    month inside the window).
    """
    key = df["month"].dt.strftime("%Y-%m")
    in_window = (df["month"].dt.year == 2024) | (
        (df["month"].dt.year >= 2025)
        & (df["month"] <= pd.Timestamp("2026-04-30")))
    keep = in_window & ~key.isin(excluded_months)
    labels = np.where(df["month"].dt.year == 2024, "before", "after")
    return np.where(keep, labels, "exclude").astype(str)


def clean_register(
    df: pd.DataFrame,
    excluded_regions: set[str] = EXCLUDED_REGIONS,
    excluded_months=None,
) -> pd.DataFrame:
    """Apply the cleaning rules and keep only before/after rows.

    Returns a DataFrame with a `period` column. Rows in excluded regions or in
    excluded months are dropped (assigned to `exclude` and filtered out).
    `excluded_months` accepts any iterable of "YYYY-MM" strings; when None the
    months flagged by detect_anomalous_months() are used automatically.
    """
    if excluded_months is None:
        excluded_months = detect_anomalous_months(df)
    excluded_months = set(excluded_months)
    out = df.copy()
    out["period"] = assign_period(out, excluded_months=excluded_months)
    out = out[~out["region"].isin(excluded_regions)]
    return out[out["period"].isin(["before", "after"])].reset_index(drop=True)


def period_shape(df: pd.DataFrame) -> dict:
    """Return the actual months present in each period (for reporting)."""
    shape = {}
    for period in ("before", "after"):
        months = sorted(df.loc[df["period"] == period, "month"].dt.strftime("%Y-%m").unique())
        shape[period] = {"n_months": len(months), "months": months}
    return shape


def monthly_totals(df: pd.DataFrame, group_col: str | list[str] | None = None) -> pd.DataFrame:
    """Per-month aggregates of the metrics, optionally by grouping column(s).

    Returns a DataFrame with columns [group_col(s), month, period, closed_small,
    new_micro], one row per (group, month) actually present in `df`:
    groupby().sum() aggregates the rows that exist and does not invent a zero
    row for a calendar month absent from the cleaned data. In this dataset the
    national, per-OKVED and per-region coverage is complete over the cleaned
    window (the only absent months are the four excluded anomalies), so no
    (group, month) row is ever skipped and the result coincides with a
    zero-filled pivot + divide-by-period-length.
    """
    keys = ["month", "period"]
    if group_col is not None:
        cols = [group_col] if isinstance(group_col, str) else group_col
        keys = cols + keys
    agg = df.groupby(keys)[list(METRICS)].sum().reset_index()
    return agg.sort_values(list(keys)).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Statistics: means, Laplacian coefficient, bootstrap confidence intervals
# ---------------------------------------------------------------------------


def laplace_k(new_micro_avg: float, closed_small_avg: float) -> float:
    """Smoothed fragmentation coefficient, K = (new+1)/(closed+1).

    The +1 (Laplace) smoothing is carried over from the Excel analysis; it
    makes the ratio well defined when closed_small_avg == 0.
    """
    return (new_micro_avg + 1.0) / (closed_small_avg + 1.0)


def _point_stats(means_before: np.ndarray, means_after: np.ndarray) -> dict:
    """Derive point statistics from the two period mean vectors.

    means_before / means_after are arrays of shape (2,) holding
    (closed_small_avg, new_micro_avg).
    """
    closed_b, new_b = float(means_before[0]), float(means_before[1])
    closed_a, new_a = float(means_after[0]), float(means_after[1])
    k_b = laplace_k(new_b, closed_b)
    k_a = laplace_k(new_a, closed_a)
    stats = {
        "closed_small": {"before": closed_b, "after": closed_a},
        "new_micro": {"before": new_b, "after": new_a},
        "K": {"before": k_b, "after": k_a},
    }
    for s in stats.values():
        b, a = s["before"], s["after"]
        s["diff"] = a - b
        s["pct_change"] = (a / b - 1.0) * 100.0 if b else np.nan
    return stats


def bootstrap_period_stats(
    months_before: np.ndarray,
    months_after: np.ndarray,
    n_iter: int = 5000,
    seed: int = 42,
) -> dict:
    """Bootstrap the statistics, resampling calendar months within a period.

    months_before / months_after: arrays of shape (n_b, 2) / (n_a, 2), rows are
    months, columns are (closed_small, new_micro). Each bootstrap iteration
    resamples months with replacement within each period independently, then
    recomputes the means and the Laplacian K, the period difference and the
    ratio K_after / K_before. The result stores 2.5/97.5 percentiles.

    Returns a dict with:
      * "bootstrap": {name: {"before": (lo, hi), "after": (lo, hi), "diff": ..., "pct_change": ...}}
      * "ratio_K": {"value": float, "ci": (lo, hi), "includes_one": bool}
      * "n_months": {"before": int, "after": int}
    """
    b = np.asarray(months_before, dtype=float)
    a = np.asarray(months_after, dtype=float)
    if len(b) == 0 or len(a) == 0:
        raise ValueError("bootstrap requires at least one month in each period")
    rng = np.random.default_rng(seed)

    # Resample calendar months WITHIN each period, independently.
    idx_b = rng.integers(0, len(b), size=(n_iter, len(b)))
    idx_a = rng.integers(0, len(a), size=(n_iter, len(a)))
    # Per-iteration mean vectors; columns are (closed_small, new_micro).
    b_mean = b[idx_b].mean(axis=1)
    a_mean = a[idx_a].mean(axis=1)

    k_b = (b_mean[:, 1] + 1.0) / (b_mean[:, 0] + 1.0)
    k_a = (a_mean[:, 1] + 1.0) / (a_mean[:, 0] + 1.0)

    ci_cols = {"closed_small": 0, "new_micro": 1}
    with np.errstate(divide="ignore", invalid="ignore"):
        diff = {
            "closed_small": a_mean[:, 0] - b_mean[:, 0],
            "new_micro": a_mean[:, 1] - b_mean[:, 1],
            "K": k_a - k_b,
        }
        pct = {}
        for name in ("closed_small", "new_micro"):
            j = ci_cols[name]
            bcol = np.where(b_mean[:, j] == 0.0, np.nan, b_mean[:, j])
            pct[name] = (a_mean[:, j] / bcol - 1.0) * 100.0
        pct["K"] = (k_a / k_b - 1.0) * 100.0
        ratio_k = k_a / k_b

    lo, hi = 2.5, 97.5
    def _ci(vals: np.ndarray) -> tuple[float, float]:
        return tuple(np.percentile(vals, [lo, hi]))

    boot = {name: {
        "before": _ci(b_mean[:, j]),
        "after": _ci(a_mean[:, j]),
        "diff": _ci(diff[name]),
        "pct_change": _ci(pct[name]),
    } for name, j in ci_cols.items()}
    boot["K"] = {
        "before": _ci(k_b),
        "after": _ci(k_a),
        "diff": _ci(diff["K"]),
        "pct_change": _ci(pct["K"]),
    }

    point = _point_stats(b.mean(axis=0), a.mean(axis=0))
    # Point estimate of the ratio uses the point statistics (not the mean of
    # per-iteration ratios, which can be biased when K_before is tiny).
    ratio_point = point["K"]["after"] / point["K"]["before"]
    ratio_ci = _ci(ratio_k)

    return {
        "point": point,
        "bootstrap": boot,
        "ratio_K": {
            "value": ratio_point,
            "ci": ratio_ci,
            "includes_one": ratio_ci[0] <= 1.0 <= ratio_ci[1],
        },
        "n_months": {"before": len(b), "after": len(a)},
    }


def _ci_str(ci, fmt: str = "{:.1f}") -> str:
    lo, hi = ci
    return f"[{fmt.format(lo)}, {fmt.format(hi)}]"


def has_zero_in_ci(ci) -> bool:
    """True when the difference CI includes zero (no detectable change)."""
    return bool(ci[0] <= 0.0 <= ci[1])


# ---------------------------------------------------------------------------
# Block 1 - national trend (before/after)
# ---------------------------------------------------------------------------


def block1_national(df: pd.DataFrame, n_iter: int = 5000, seed: int = 42) -> tuple[pd.DataFrame, dict]:
    """National Block 1: closed_small / new_micro / K with bootstrap CIs."""
    months = {}
    monthly = monthly_totals(df)
    for period in ("before", "after"):
        sub = monthly[monthly["period"] == period]
        months[period] = sub[list(METRICS)].to_numpy()
    res = bootstrap_period_stats(months["before"], months["after"], n_iter=n_iter, seed=seed)
    table = _block1_table(res)
    return table, {"national": res}


def _block1_table(res: dict) -> pd.DataFrame:
    """Turn one bootstrap result into a readable per-metric table."""
    rows = []
    for name, meta in [("closed_small", "Small-enterprise closures (avg/month)"),
                       ("new_micro", "New micro-enterprise registrations (avg/month)"),
                       ("K", "Fragmentation coefficient (Laplace)")]:
        p = res["point"][name]
        b = res["bootstrap"][name]
        rows.append({
            "metric": name,
            "label": meta,
            "before": p["before"],
            "after": p["after"],
            "before_ci": _ci_str(b["before"]),
            "after_ci": _ci_str(b["after"]),
            "diff": p["diff"],
            "pct_change": p["pct_change"],
            "pct_ci": _ci_str(b["pct_change"]),
            "diff_ci": _ci_str(b["diff"]),
            "zero_in_diff_ci": has_zero_in_ci(b["diff"]),
        })
    r = res["ratio_K"]
    rows.append({
        "metric": "K_ratio",
        "label": "Ratio K_after / K_before",
        "before": res["point"]["K"]["before"],
        "after": res["point"]["K"]["after"],
        "before_ci": "—",
        "after_ci": "—",
        "diff": res["point"]["K"]["diff"],
        "pct_change": (r["value"] - 1.0) * 100.0,
        "pct_ci": _ci_str(r["ci"]),
        "diff_ci": "—",
        "zero_in_diff_ci": r["includes_one"],
    })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Block 2 - fragmentation coefficient by OKVED (with CI), top-15
# ---------------------------------------------------------------------------


def block2_okved(df: pd.DataFrame, n_iter: int = 5000, seed: int = 42,
                 top_n: int = 15) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rank OKVED classes by after-period K and add bootstrap CIs.

    Faithful to Excel: only OKVED classes with closed_small_avg_after > 0 are
    ranked (classes with zero closures cannot have a meaningful denominator).
    """
    monthly = monthly_totals(df, group_col="okved")
    rows = []
    for i, (okved, grp) in enumerate(monthly.groupby("okved")):
        months = {p: grp.loc[grp["period"] == p, list(METRICS)].to_numpy() for p in ("before", "after")}
        res = bootstrap_period_stats(months["before"], months["after"], n_iter=n_iter, seed=seed + i)
        p = res["point"]
        ok = {
            "okved": okved,
            "okved_name": okved_name_en(okved),
            "closed_small_avg_before": p["closed_small"]["before"],
            "closed_small_avg_after": p["closed_small"]["after"],
            "new_micro_avg_before": p["new_micro"]["before"],
            "new_micro_avg_after": p["new_micro"]["after"],
            "K_before": p["K"]["before"],
            "K_after": p["K"]["after"],
            "K_after_ci": _ci_str(res["bootstrap"]["K"]["after"]),
            "K_before_ci": _ci_str(res["bootstrap"]["K"]["before"]),
            "K_ratio": res["ratio_K"]["value"],
            "K_ratio_ci": _ci_str(res["ratio_K"]["ci"]),
            "ratio_includes_one": res["ratio_K"]["includes_one"],
        }
        rows.append(ok)

    table = pd.DataFrame(rows).sort_values("K_after", ascending=False).reset_index(drop=True)
    # Excel excluded OKVED classes with closed_small == 0 in the after period.
    table = table[table["closed_small_avg_after"] > 0]
    return table.head(top_n), table

# ---------------------------------------------------------------------------
# Block 3 - regions ranked by small-enterprise closures (with CI), top-15
# ---------------------------------------------------------------------------


def block3_regions(df: pd.DataFrame, n_iter: int = 5000, seed: int = 42,
                   top_n: int = 15) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rank regions by after-period closed_small_avg and add bootstrap CIs.

    Faithful to Excel: regions are ranked by mean monthly small-enterprise
    closures in the `after` period; the share column reproduces the Excel
    "share of all liquidations".
    """
    monthly = monthly_totals(df, group_col="region")
    total_after_closed = monthly.loc[monthly["period"] == "after", "closed_small"].sum()
    n_after = monthly.loc[monthly["period"] == "after", "month"].nunique()

    rows = []
    for i, (region, grp) in enumerate(monthly.groupby("region")):
        months = {p: grp.loc[grp["period"] == p, list(METRICS)].to_numpy() for p in ("before", "after")}
        res = bootstrap_period_stats(months["before"], months["after"], n_iter=n_iter, seed=seed + i)
        p = res["point"]
        ci = res["bootstrap"]["closed_small"]
        rows.append({
            "region_code": region,
            "region": region_name_en(region),
            "closed_small_avg_before": p["closed_small"]["before"],
            "closed_small_avg_after": p["closed_small"]["after"],
            "closed_diff": p["closed_small"]["diff"],
            "closed_pct_change": p["closed_small"]["pct_change"],
            "closed_after_ci": _ci_str(ci["after"]),
            "closed_before_ci": _ci_str(ci["before"]),
            "closed_diff_ci": _ci_str(ci["diff"]),
            "zero_in_diff_ci": has_zero_in_ci(ci["diff"]),
            "new_micro_avg_after": p["new_micro"]["after"],
            "K_after": p["K"]["after"],
            "K_after_ci": _ci_str(res["bootstrap"]["K"]["after"]),
            "share_of_closures_pct": 100.0 * p["closed_small"]["after"] * n_after / total_after_closed,
        })

    table = pd.DataFrame(rows).sort_values("closed_small_avg_after", ascending=False).reset_index(drop=True)
    return table.head(top_n), table


# ---------------------------------------------------------------------------
# Block 4 - region x OKVED matrix (change of the fragmentation coefficient)
# ---------------------------------------------------------------------------


def block4_matrix(df: pd.DataFrame, min_active_months: int = MIN_ACTIVE_MONTHS) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Recompute K_before, K_after and change_ratio for each (region, okved).

    Unlike the Excel version (which silently substituted 1 for missing cells),
    a pair is marked as INSUFFICIENT when, in either period, it has fewer than
    `min_active_months` months with observed activity (new_micro + closed_small
    > 0). Insufficient pairs get change_ratio = NaN and are excluded from the
    matrix statistics (never silently set to 1).

    Monthly averages divide sums by the observed period length in the cleaned
    data, matching the Excel "sum / period months" formula.

    Returns (long_table, pivot_matrix, summary). The pivot holds change_ratio
    with NaN for insufficient pairs.
    """
    n_months = df.groupby("period")["month"].nunique().to_dict()
    agg = df.groupby(["region", "okved", "month", "period"])[list(METRICS)].sum().reset_index()
    agg["active"] = (agg["new_micro"] + agg["closed_small"]) > 0

    rows = []
    for (region, okved), grp in agg.groupby(["region", "okved"]):
        per_period = {}
        sufficient = True
        for period in ("before", "after"):
            sub = grp[grp["period"] == period]
            n_active = int(sub["active"].sum())
            closed_avg = float(sub["closed_small"].sum()) / n_months[period]
            new_avg = float(sub["new_micro"].sum()) / n_months[period]
            per_period[period] = (closed_avg, new_avg, n_active)
            if n_active < min_active_months:
                sufficient = False

        k_before = laplace_k(per_period["before"][1], per_period["before"][0])
        k_after = laplace_k(per_period["after"][1], per_period["after"][0])
        change = k_after / k_before if sufficient else np.nan

        rows.append({
            "region_code": region,
            "region": region_name_en(region),
            "okved": okved,
            "okved_name": okved_name_en(okved),
            "n_active_before": per_period["before"][2],
            "n_active_after": per_period["after"][2],
            "K_before": k_before,
            "K_after": k_after,
            "change_ratio": change,
            "insufficient": not sufficient,
        })

    long = pd.DataFrame(rows)
    sufficient = long[~long["insufficient"]].copy()
    insufficient = long[long["insufficient"]].copy()

    # Full matrix: insufficient pairs keep change_ratio = NaN, so they render as
    # grey cells in the heatmap and the NaN count matches summary counts.
    pivot = long.pivot(index="region_code", columns="okved", values="change_ratio")
    # Short region names are provided for row labels; keep numeric index order
    # (FNS codes) so the matrix is directly comparable to the Excel one.
    pivot.index = [region_name_en(c) for c in pivot.index]

    def _cat(x: float) -> str:
        if np.isclose(x, 1.0):
            return "no change (==1)"
        return "fragmentation up (>1)" if x > 1.0 else "fragmentation down (<1)"

    cats = sufficient["change_ratio"].apply(_cat).value_counts() if len(sufficient) else pd.Series(dtype=int)

    summary = {
        "n_pairs_total": len(long),
        "n_pairs_sufficient": len(sufficient),
        "n_pairs_insufficient": len(insufficient),
        "min_active_months": min_active_months,
        "categories": {str(k): int(v) for k, v in cats.items()},
        "n_red": int(cats.get("fragmentation up (>1)", 0)),
        "n_green": int(cats.get("fragmentation down (<1)", 0)),
        "n_yellow": int(cats.get("no change (==1)", 0)),
    }
    return long, pivot, summary


# ---------------------------------------------------------------------------
# Robustness / sensitivity analysis of Block 1
# ---------------------------------------------------------------------------


def robustness_block1(raw: pd.DataFrame, n_iter: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Re-run national Block 1 under alternative cleaning definitions.

    The excluded-month set is always derived from detect_anomalous_months()
    (never hardcoded). Variants:
      base        - automatic rule (the detected months are dropped)
      manual      - the Excel study's manual rule: only the two Julys dropped
      july_kept   - automatic rule minus the two Julys (Julys kept in the sample)
      +region:XX  - automatic rule, excluded region XX re-added one at a time
      after2025   - automatic rule, `after` restricted to calendar year 2025
    """
    detected = detect_anomalous_months(raw)
    july = {"2024-07", "2025-07"}

    variants: list[dict] = [
        {"key": "base", "label": "Auto rule", "counts": True,
         "excluded_regions": EXCLUDED_REGIONS, "excluded_months": set(detected),
         "after_2025": False},
        {"key": "manual", "label": "Manual Excel rule (July only)", "counts": True,
         "excluded_regions": EXCLUDED_REGIONS, "excluded_months": july,
         "after_2025": False},
        {"key": "july_kept", "label": "July kept (2026 spikes only)", "counts": True,
         "excluded_regions": EXCLUDED_REGIONS,
         "excluded_months": set(detected) - july, "after_2025": False},
    ]
    for reg in sorted(EXCLUDED_REGIONS):
        variants.append({"key": f"+region_{reg}", "counts": False,
                         "label": f"Excluded region {reg} re-added",
                         "excluded_regions": EXCLUDED_REGIONS - {reg},
                         "excluded_months": set(detected), "after_2025": False})
    variants.append({"key": "after2025", "counts": True,
                     "label": "After = 2025 only", "excluded_regions": EXCLUDED_REGIONS,
                     "excluded_months": set(detected), "after_2025": True})

    rows = []
    for v in variants:
        df = clean_register(raw, excluded_regions=v["excluded_regions"],
                            excluded_months=v["excluded_months"])
        if v["after_2025"]:
            df = df[df["month"].dt.year <= 2025].reset_index(drop=True)
            df["period"] = assign_period(df, excluded_months=v["excluded_months"])
            df = df[df["period"].isin(["before", "after"])].reset_index(drop=True)
        monthly = monthly_totals(df)
        months = {p: monthly.loc[monthly["period"] == p, list(METRICS)].to_numpy()
                  for p in ("before", "after")}
        res = bootstrap_period_stats(months["before"], months["after"], n_iter=n_iter,
                                     seed=seed + len(rows))
        point = res["point"]
        label = v["label"]
        if v["counts"]:
            label = f"{label} ({len(months['before'])}m / {len(months['after'])}m)"
        rows.append({
            "variant": v["key"],
            "label": label,
            "n_before": len(months["before"]),
            "n_after": len(months["after"]),
            "closed_pct": point["closed_small"]["pct_change"],
            "new_pct": point["new_micro"]["pct_change"],
            "K_pct": point["K"]["pct_change"],
            "closed_after_avg": point["closed_small"]["after"],
            "new_after_avg": point["new_micro"]["after"],
        })

    table = pd.DataFrame(rows)
    base = table.loc[table["variant"] == "base", ["closed_pct", "new_pct", "K_pct"]].iloc[0]
    table["d_closed_pp"] = table["closed_pct"] - base["closed_pct"]
    table["d_new_pp"] = table["new_pct"] - base["new_pct"]
    table["d_K_pp"] = table["K_pct"] - base["K_pct"]
    return table


# ---------------------------------------------------------------------------
# Cleaning scenarios (A vs B), driven by the anomaly classification
# ---------------------------------------------------------------------------


def cleaning_scenarios(raw: pd.DataFrame, n_iter: int = 5000, seed: int = 42,
                       headline_res: dict | None = None) -> pd.DataFrame:
    """Block 1 under the two plausible cleaning rules implied by the anomaly types.

    Scenario `exclude_compensation_patterns` drops only the months identified as
    compensation patterns — same-month churn AND lagged compensation (the July
    methodology change and 2026-02, whose closures reappear as a 2026-03
    new_small wave), while retaining the unresolved 2026-04 month (provisional
    net-exit pattern) in the sample.

    Scenario `exclude_all_flagged` drops every month that trips the magnitude
    rule, as the base cleaning does. This is the more restrictive cleaning
    scenario: it removes the compensation patterns together with any month whose
    flows remain unresolved. Both published scenarios are therefore *sensitivity
    / alternative cleaning scenarios*, not bounds.

    Scenario `exclude_all_flagged` is the headline cleaning *by definition*, so
    when `headline_res` (the bootstrap result of the national Block 1 run) is
    passed in, B reuses that exact calculation instead of drawing an independent
    bootstrap — the CIs published in `cleaning_scenarios.csv` then coincide
    literally with the headline Block 1, not just approximately. Scenario
    `exclude_compensation_patterns` still needs its own bootstrap (different
    excluded set).

    Returns one row per scenario with the Block-1 point statistics, bootstrap
    CIs and period shape.
    """
    diag, _ = anomaly_diagnostics(raw)
    flagged = set(diag["month"])
    if not flagged:
        raise ValueError("no anomalous months detected — scenarios are degenerate")
    compensation = set(diag.loc[diag["anomaly_type"].isin(["churn", "lagged_compensation"]),
                                "month"])

    variants = [
        {"key": "exclude_compensation_patterns",
         "label": "A: exclude compensation patterns (same-month + lagged), "
                  "retain unresolved 2026-04",
         "months": compensation},
        {"key": "exclude_all_flagged",
         "label": "B: exclude all four flagged months (headline cleaning)",
         "months": flagged},
    ]

    rows = []
    for i, v in enumerate(variants):
        df = clean_register(raw, excluded_months=v["months"])
        monthly = monthly_totals(df)
        months = {p: monthly.loc[monthly["period"] == p, list(METRICS)].to_numpy()
                  for p in ("before", "after")}
        if v["months"] == flagged and headline_res is not None:
            res = headline_res  # scenario B == headline: reuse its bootstrap
        else:
            res = bootstrap_period_stats(months["before"], months["after"],
                                         n_iter=n_iter, seed=seed + i)
        point = res["point"]
        rk = res["ratio_K"]
        rows.append({
            "scenario": v["key"],
            "label": v["label"],
            "months_dropped": ", ".join(sorted(v["months"])),
            "n_before": len(months["before"]),
            "n_after": len(months["after"]),
            "closed_pct": point["closed_small"]["pct_change"],
            "closed_pct_ci": _ci_str(res["bootstrap"]["closed_small"]["pct_change"]),
            "new_pct": point["new_micro"]["pct_change"],
            "new_pct_ci": _ci_str(res["bootstrap"]["new_micro"]["pct_change"]),
            "K_pct": point["K"]["pct_change"],
            "K_ratio": rk["value"],
            "K_ratio_ci": _ci_str(rk["ci"], "{:.3f}"),
            "K_includes_one": rk["includes_one"],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Threshold sensitivity (fixed 3.0 is itself a methodological fork)
# ---------------------------------------------------------------------------


def threshold_sensitivity(raw: pd.DataFrame,
                          thresholds: tuple[float, ...] = (2.0, 2.5, 3.0, 4.0, 5.0),
                          n_iter: int = 5000, seed: int = 42,
                          headline_res: dict | None = None) -> pd.DataFrame:
    """Re-run detection and Block 1 across plausible values of the spike threshold.

    `detect_anomalous_months` defaults to `threshold = MONTH_SPIKE_THRESHOLD`
    (3.0). That constant is itself a methodological choice, so this function
    repeats the whole flag->classify->clean->Block-1 chain for every threshold
    in `thresholds`: which months are flagged, how each flagged month classifies
    (churn / lagged_compensation / provisional_net_exit / unclassified), and
    what headline `closed_pct` and
    `K_ratio` result. This is a declared sensitivity check on the cleaning
    fork, not a hidden tuning of the constant.

    The row at the base threshold (`t == MONTH_SPIKE_THRESHOLD`) is the headline
    cleaning by construction; when `headline_res` is passed in it reuses that
    exact Bootstrap instead of drawing an independent one, so its CIs coincide
    literally with the headline Block 1.

    Returns one row per threshold: flagged months (and their anomaly types),
    period shape, and Block-1 point statistics / bootstrap CIs.
    """
    rows = []
    for i, t in enumerate(sorted(thresholds)):
        flagged = detect_anomalous_months(raw, threshold=t)
        diag, _ = anomaly_diagnostics(raw, flagged)
        types = ", ".join(f"{m}={c}" for m, c in
                          zip(diag["month"], diag["anomaly_type"])) if len(diag) else ""

        df = clean_register(raw, excluded_months=flagged)
        monthly = monthly_totals(df)
        months = {p: monthly.loc[monthly["period"] == p, list(METRICS)].to_numpy()
                  for p in ("before", "after")}
        if headline_res is not None and t == MONTH_SPIKE_THRESHOLD:
            res = headline_res  # base threshold == headline cleaning
        else:
            res = bootstrap_period_stats(months["before"], months["after"],
                                         n_iter=n_iter, seed=seed + i)
        point = res["point"]
        rk = res["ratio_K"]
        rows.append({
            "threshold": t,
            "n_flagged": len(flagged),
            "flagged_months": ", ".join(flagged) if flagged else "(none)",
            "anomaly_types": types,
            "n_before": len(months["before"]),
            "n_after": len(months["after"]),
            "closed_pct": point["closed_small"]["pct_change"],
            "closed_pct_ci": _ci_str(res["bootstrap"]["closed_small"]["pct_change"]),
            "K_ratio": rk["value"],
            "K_ratio_ci": _ci_str(rk["ci"], "{:.3f}"),
            "K_includes_one": rk["includes_one"],
        })
    return pd.DataFrame(rows)


def spike_margins(raw: pd.DataFrame, threshold: float = MONTH_SPIKE_THRESHOLD) -> pd.DataFrame:
    """How close every calendar month is to the flag rule's decision boundary.

    For each month, national `closed_small` and `closed_micro` totals as ratios
    of their respective series medians ("x of median"), plus whether the month
    is flagged at `threshold`. This is the raw margin behind the sensitivity
    result, shipped so a reviewer can verify the *distance* from the threshold:
    on the current window the four flagged months sit at 9.8×–22.9× the
    `closed_small` median while every other month is ≤ 1.67×, so the 3.0
    constant is deep inside a plateau and far from the decision boundary.

    Returns one row per calendar month (index becomes the `month` column via
    reset_index; the groupby key already carries that name).
    """
    key = raw["month"].dt.strftime("%Y-%m")
    totals = raw.groupby(key)[["closed_small", "closed_micro"]].sum()
    med = totals.median()
    out = totals.copy()
    out["closed_small_x_median"] = out["closed_small"] / med["closed_small"]
    out["closed_micro_x_median"] = out["closed_micro"] / med["closed_micro"]
    out["flagged_at_threshold"] = (
        (out["closed_small"] > med["closed_small"] * threshold) |
        (out["closed_micro"] > med["closed_micro"] * threshold)
    )
    cols = ["closed_small", "closed_small_x_median",
            "closed_micro", "closed_micro_x_median", "flagged_at_threshold"]
    return out[cols].reset_index()


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def _save_fig(fig, name: str, fmt: str = "png") -> Path:
    """Save a figure to /figures in the requested format."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURES_DIR / f"{name}.{fmt}"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    return path


def _ellipsize(text, width: int = 44) -> str:
    """Truncate to `width` chars at a word boundary, adding an ellipsis.

    Hard-slicing labels mid-word (as slicing `name[:42]` does) reads badly on a
    chart; cutting at the last whitespace keeps the label readable.
    """
    text = str(text).strip()
    if len(text) <= width:
        return text
    cut = text[: width + 1]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:-") + "…"


def figure_national_trend(df: pd.DataFrame, excluded_months=()) -> Path:
    """Monthly national time series with the detected anomalous months marked."""
    import matplotlib.pyplot as plt

    # Whole sample (raw, before cleaning) for the trend, incl. the spikes.
    raw_monthly = df.groupby("month")[["closed_small", "new_micro"]].sum()

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax1.plot(raw_monthly.index, raw_monthly["closed_small"], marker="o", ms=3,
             color="#c0392b", label="closed_small (monthly total)")
    ax1.set_xlabel("Month"); ax1.set_ylabel("Small-enterprise closures")
    x_l, x_r = raw_monthly.index[0], raw_monthly.index[-1]
    ax1.set_xlim(x_l - pd.Timedelta(days=45), x_r + pd.Timedelta(days=100))
    ax1.margins(y=0.18)
    for spike in excluded_months:
        ts = pd.Timestamp(spike)
        if ts in raw_monthly.index:
            y = raw_monthly.loc[ts, "closed_small"]
            ax1.scatter([ts], [y], s=80, facecolor="none", edgecolor="#8e44ad", zorder=5)
            ax1.annotate("excluded (detected anomaly)", (ts, y),
                         textcoords="offset points", xytext=(0, 8), ha="center",
                         fontsize=8, color="#8e44ad")
    ax2 = ax1.twinx()
    ax2.plot(raw_monthly.index, raw_monthly["new_micro"], marker="o", ms=3,
             color="#16a085", label="new_micro (monthly total)")
    ax2.set_ylabel("New micro-enterprise registrations")
    # Period bounds: before = May-Dec 2024, after = Jan 2025 - Apr 2026.
    for border in (pd.Timestamp("2024-05-15"), pd.Timestamp("2024-12-15"),
                   pd.Timestamp("2026-04-30")):
        ax1.axvline(border, color="grey", lw=0.8, ls="--", alpha=0.6)
    ax1.set_title("National SME register dynamics, May 2024 - Apr 2026 "
                  "(dashed: before/after period bounds)")
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.01), ncol=2, fontsize=9)
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    path = _save_fig(fig, "fig1_national_trend")
    plt.close(fig)
    return path


def figure_block1(table: pd.DataFrame) -> Path:
    """Before/after means with 95% bootstrap CIs for the three Block-1 metrics."""
    import matplotlib.pyplot as plt

    metrics = {
        "closed_small": "Small closures (avg/month)",
        "new_micro": "New micro registrations (avg/month)",
        "K": "Fragmentation coefficient K",
    }
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.2))
    for ax, (key, label) in zip(axes, metrics.items()):
        row = table[table["metric"] == key].iloc[0]
        before, after = row["before"], row["after"]
        b_lo, b_hi = _parse_ci(row["before_ci"])
        a_lo, a_hi = _parse_ci(row["after_ci"])
        neg = (before - b_lo, after - a_lo)
        pos = (b_hi - before, a_hi - after)
        ax.bar(["before", "after"], [before, after],
               color=["#7f8c8d", "#2980b9"], width=0.55,
               yerr=(neg, pos), capsize=5, error_kw={"elinewidth": 1})
        pct = row["pct_change"]
        zero = row["zero_in_diff_ci"]
        note = "statistically indistinguishable" if zero else ""
        txt = "n/a (before ~ 0)" if np.isnan(pct) else f"pct change: {pct:+.2f}%"
        suffix = f"\n[{note}]" if note else ""
        ax.set_title(f"{label}\n{txt}{suffix}", fontsize=9.5)
        ax.set_ylabel("value")
    fig.suptitle("Block 1 — national before/after means (95% bootstrap CI)",
                 fontsize=12)
    fig.tight_layout()
    path = _save_fig(fig, "fig2_block1")
    plt.close(fig)
    return path


def figure_block2(table: pd.DataFrame) -> Path:
    """Top-15 OKVED classes by K_after with 95% bootstrap CIs."""
    import matplotlib.pyplot as plt

    t = table.iloc[::-1]
    ylabels = [f"{r['okved']} — {_ellipsize(r['okved_name'], 44)}" for _, r in t.iterrows()]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(ylabels, t["K_after"], color="#2980b9", alpha=0.9)
    for i, (_, r) in enumerate(t.iterrows()):
        lo, hi = _parse_ci(r["K_after_ci"])
        ax.errorbar([lo], [i], xerr=[[r["K_after"] - lo], [hi - r["K_after"]]],
                     fmt="none", ecolor="black", capsize=3)
    ax.set_xlabel("Fragmentation coefficient K after (avg/month), log scale")
    ax.set_xscale("log")
    ax.set_title("Block 2 — top-15 OKVED classes by fragmentation coefficient (after)\n"
                 "error bars: 95% bootstrap CI")
    fig.tight_layout()
    path = _save_fig(fig, "fig3_block2_okved")
    plt.close(fig)
    return path


def figure_block3(table: pd.DataFrame) -> Path:
    """Top-15 regions by closed_small_avg (after) with 95% bootstrap CIs."""
    import matplotlib.pyplot as plt

    t = table.iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.barh(t["region"], t["closed_small_avg_after"], color="#c0392b", alpha=0.9)
    for i, (_, r) in enumerate(t.iterrows()):
        lo, hi = _parse_ci(r["closed_after_ci"])
        ax.errorbar([lo], [i], xerr=[[r["closed_small_avg_after"] - lo],
                                     [hi - r["closed_small_avg_after"]]],
                     fmt="none", ecolor="black", capsize=3)
    ax.set_xlabel("Small-enterprise closures (avg/month, after period)")
    ax.set_title("Block 3 — top-15 regions by small-enterprise closures (after)\n"
                 "error bars: 95% bootstrap CI")
    fig.tight_layout()
    path = _save_fig(fig, "fig4_block3_regions")
    plt.close(fig)
    return path


def figure_matrix_heatmap(pivot: pd.DataFrame, summary: dict) -> Path:
    """Heatmap of change_ratio = K_after / K_before on a log2 colour scale."""
    import matplotlib.pyplot as plt

    data = np.log2(pivot.to_numpy(dtype=float))
    data = np.ma.masked_invalid(data)
    cmap = plt.cm.RdYlGn.copy()
    cmap.set_bad("#cccccc")
    fig, ax = plt.subplots(figsize=(16, 9))
    im = ax.imshow(data, cmap=cmap, vmin=-3, vmax=3, aspect="auto")
    ax.set_xticks(range(data.shape[1]))
    ax.set_xticklabels(pivot.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(data.shape[0]))
    ax.set_yticklabels(pivot.index, fontsize=7)
    ax.set_title("Block 4 — change of the fragmentation coefficient by region x OKVED "
                 "(log2(K_after/K_before); NaN pairs flagged insufficient)")
    cbar = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.01)
    cbar.set_label("log2(K_after / K_before)")
    ax.text(1.0, -0.06,
            f"sufficient pairs: {summary['n_pairs_sufficient']} | "
            f"insufficient (NaN, in grey): {summary['n_pairs_insufficient']} | "
            f"threshold: >= {summary['min_active_months']} months active in both periods",
            transform=ax.transAxes, ha="right", va="top", fontsize=8)
    fig.tight_layout()
    path = _save_fig(fig, "fig5_matrix_heatmap")
    plt.close(fig)
    return path


def figure_robustness(table: pd.DataFrame) -> Path:
    """Sensitivity of the closed_small pct change to cleaning choices."""
    import matplotlib.pyplot as plt

    t = table.iloc[::-1]
    fig, ax = plt.subplots(figsize=(9, 5))
    colors = ["#c0392b" if r["variant"] == "base" else "#7f8c8d"
              for _, r in t.iterrows()]
    ax.barh(t["label"], t["closed_pct"], color=colors, alpha=0.9)
    ax.axvline(t.loc[t["variant"] == "base", "closed_pct"].iloc[0],
               color="black", lw=1, ls="--")
    ax.set_xlabel("closed_small pct change, before -> after (%)")
    ax.set_title("Robustness — how the headline liquidation change moves "
                 "with cleaning choices")
    fig.tight_layout()
    path = _save_fig(fig, "fig6_robustness")
    plt.close(fig)
    return path


def _parse_ci(ci_str: str) -> tuple[float, float]:
    """Parse a '[lo, hi]' string written by _ci_str back into floats."""
    lo, hi = ci_str.strip("[]").split(",")
    return float(lo), float(hi)


# ---------------------------------------------------------------------------
# Pipeline orchestration and reporting
# ---------------------------------------------------------------------------


def run_pipeline(n_iter: int = 5000, seed: int = 42,
                 save_outputs: bool = True, make_plots: bool = True) -> dict:
    """Run the full analysis and return a results container."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    raw = load_register()
    detected_months = detect_anomalous_months(raw)
    diag, diag_warnings = anomaly_diagnostics(raw, detected_months)
    df = clean_register(raw, excluded_months=detected_months)
    shape = period_shape(df)

    results = {"period_shape": shape, "seed": seed, "n_iter": n_iter,
               "cleaning": {"regions_excluded": sorted(EXCLUDED_REGIONS),
                            "months_excluded": detected_months,
                            "month_spike_threshold": MONTH_SPIKE_THRESHOLD}}
    if len(diag):
        results["cleaning"]["anomaly_types"] = {
            m: str(t) for m, t in zip(diag["month"], diag["anomaly_type"])}
    if diag_warnings:
        results["cleaning"]["unclassified_warnings"] = diag_warnings
    results["anomaly_diagnostics"] = diag
    results["lagged_compensation_similarity"] = profile_similarity(
        raw, diag[diag["anomaly_type"] == "lagged_compensation"])

    # --- Block 1 (headline bootstrap) ---
    block1_table, block1_res = block1_national(df, n_iter=n_iter, seed=seed)
    results["block1_table"] = block1_table
    results["block1"] = block1_res

    # Scenarios/sensitivity reuse the headline bootstrap for the base cleaning,
    # so their CIs coincide literally with Block 1 (no second independent run).
    scenarios = cleaning_scenarios(raw, n_iter=n_iter, seed=seed,
                                   headline_res=block1_res["national"])
    results["cleaning_scenarios"] = scenarios
    sens = threshold_sensitivity(raw, n_iter=n_iter, seed=seed,
                                 headline_res=block1_res["national"])
    results["threshold_sensitivity"] = sens
    margins = spike_margins(raw)
    results["spike_margins"] = margins

    # --- Block 2 ---
    top_okved, all_okved = block2_okved(df, n_iter=n_iter, seed=seed)
    results["block2_top15"] = top_okved
    results["block2_all"] = all_okved

    # --- Block 3 ---
    top_regions, all_regions = block3_regions(df, n_iter=n_iter, seed=seed)
    results["block3_top15"] = top_regions
    results["block3_all"] = all_regions

    # --- Block 4 ---
    mat_long, mat_pivot, mat_summary = block4_matrix(df)
    results["block4_long"] = mat_long
    results["block4_pivot"] = mat_pivot
    results["block4_summary"] = mat_summary

    # --- Robustness ---
    rob = robustness_block1(raw, n_iter=n_iter, seed=seed)
    results["robustness"] = rob

    # --- Outputs ---
    if save_outputs:
        df.sort_values(["month", "region", "okved"]).to_csv(
            PROCESSED_DIR / "msp_clean.csv", index=False)
        block1_table.to_csv(PROCESSED_DIR / "block1_national.csv", index=False)
        top_okved.to_csv(PROCESSED_DIR / "block2_top15_okved.csv", index=False)
        all_okved.to_csv(PROCESSED_DIR / "block2_all_okved.csv", index=False)
        top_regions.to_csv(PROCESSED_DIR / "block3_top15_regions.csv", index=False)
        all_regions.to_csv(PROCESSED_DIR / "block3_all_regions.csv", index=False)
        mat_long.to_csv(PROCESSED_DIR / "block4_matrix_long.csv", index=False)
        mat_pivot.to_csv(PROCESSED_DIR / "block4_matrix_pivot.csv")
        rob.to_csv(PROCESSED_DIR / "robustness_block1.csv", index=False)
        diag.to_csv(PROCESSED_DIR / "anomaly_diagnostics.csv", index=False)
        _lag_sim = results.get("lagged_compensation_similarity")
        if _lag_sim is not None:
            _lag_sim.to_csv(PROCESSED_DIR / "lagged_compensation_similarity.csv", index=False)
        scenarios.to_csv(PROCESSED_DIR / "cleaning_scenarios.csv", index=False)
        sens.to_csv(PROCESSED_DIR / "threshold_sensitivity.csv", index=False)
        margins.to_csv(PROCESSED_DIR / "spike_margins.csv", index=False)
        with open(PROCESSED_DIR / "summary.json", "w", encoding="utf-8") as f:
            json.dump(_json_safe(results), f, indent=2, ensure_ascii=False)

    if make_plots:
        figure_national_trend(df, excluded_months=detected_months)
        figure_block1(block1_table)
        figure_block2(top_okved)
        figure_block3(top_regions)
        figure_matrix_heatmap(mat_pivot, mat_summary)
        figure_robustness(rob)

    return results


def _json_safe(results: dict) -> dict:
    """Strip non-serialisable objects (DataFrames) for the JSON dump."""
    keep = ["period_shape", "seed", "n_iter", "cleaning"]
    out = {k: results[k] for k in keep}
    out["block1_summary"] = _json_safe_notebook(results["block1"]["national"])
    out["block4_summary"] = results["block4_summary"]
    out["anomaly_diagnostics"] = _df_to_records(results["anomaly_diagnostics"])
    out["cleaning_scenarios"] = _df_to_records(results["cleaning_scenarios"])
    out["threshold_sensitivity"] = _df_to_records(results["threshold_sensitivity"])
    out["spike_margins"] = _df_to_records(results["spike_margins"])
    return out


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Rows of a DataFrame as records, with NaN -> None (JSON-safe)."""
    records = df.to_dict(orient="records")
    for r in records:
        for k, v in r.items():
            if v is None:
                continue
            if (isinstance(v, float) and np.isnan(v)):
                r[k] = None
            elif isinstance(v, np.generic):
                r[k] = v.item()
    return records


def _json_safe_notebook(res: dict) -> dict:
    """Minimal serialisable summary of one bootstrap result."""
    out = {"point": res["point"], "ratio_K": res["ratio_K"],
           "n_months": res["n_months"]}
    out["bootstrap"] = {m: {k: list(v) for k, v in ci.items()}
                        for m, ci in res["bootstrap"].items()}
    return _np_to_py(out)


def _np_to_py(x):
    """Recursively convert numpy scalars to native Python types (for JSON)."""
    if isinstance(x, dict):
        return {k: _np_to_py(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_np_to_py(v) for v in x]
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating):
        return float(x)
    return x


def print_report(results: dict) -> None:
    """Human-readable console report of the pipeline results."""
    shape = results["period_shape"]
    b1 = results["block1"]
    top_okved = results["block2_top15"]
    top_regions = results["block3_top15"]
    mat = results["block4_summary"]
    rob = results["robustness"]

    print("=" * 78)
    print("MSP register — descriptive analysis (May 2024 - Apr 2026)")
    print(f"  seed={results['seed']}, bootstrap iterations={results['n_iter']}")
    print(f"  regions excluded        : {', '.join(results['cleaning']['regions_excluded'])}")
    print(f"  months excluded         : {', '.join(results['cleaning']['months_excluded'])}")
    types = results["cleaning"].get("anomaly_types", {})
    if types:
        print(f"  anomaly classification  : "
              f"{', '.join(f'{m}={t}' for m, t in sorted(types.items()))}")
    warns = results["cleaning"].get("unclassified_warnings", [])
    for w in warns:
        print(f"  [WARNING] {w}")
    print(f"  before: {shape['before']['n_months']} months "
          f"({', '.join(shape['before']['months'])})")
    print(f"  after : {shape['after']['n_months']} months "
          f"({', '.join(shape['after']['months'])})")
    print("=" * 78)

    print("\n[Anomaly diagnostics] flagged months: compensation vs provisional exit patterns")
    diag = results["anomaly_diagnostics"]
    show_diag = diag[["month", "anomaly_type", "closed_small", "new_small",
                      "actual_delta_small", "implied_delta_small",
                      "new_small_x_median"]].copy()
    show_diag.columns = ["month", "type", "closed_small", "new_small",
                         "actual_delta", "implied_delta", "new_x_median"]
    for c in ("closed_small", "new_small", "actual_delta", "implied_delta", "new_x_median"):
        show_diag[c] = show_diag[c].round(1)
    _print_table(show_diag.to_string(index=False))

    print("\n[Cleaning scenarios] Block 1 under the two alternative cleaning scenarios")
    show_scn = results["cleaning_scenarios"].copy()
    for c in show_scn.columns:
        if pd.api.types.is_numeric_dtype(show_scn[c]):
            show_scn[c] = show_scn[c].round(1)
    show_scn.columns = ["scenario", "label", "months_dropped", "n_b", "n_a",
                        "closed%", "closed%_CI", "new%", "new%_CI", "K%",
                        "K_ratio", "K_ratio_CI", "1_in_CI"]
    _print_table(show_scn.to_string(index=False))

    print("\n[Threshold sensitivity] 3.0 is itself a methodological fork — "
          "flagged months / closed% / K_ratio across the 2.0-5.0 range")
    show_sens = results["threshold_sensitivity"].copy()
    for c in ("threshold", "n_flagged", "n_before", "n_after"):
        show_sens[c] = show_sens[c].astype(int)
    for c in ("closed_pct", "K_ratio"):
        show_sens[c] = show_sens[c].round(1)
    show_sens = show_sens[["threshold", "n_flagged", "flagged_months",
                           "anomaly_types", "closed_pct", "closed_pct_ci",
                           "K_ratio", "K_ratio_ci"]]
    _print_table(show_sens.to_string(index=False))

    print("\n[Spike margins] closed/micro totals as x-of-median per month ")
    show_marg = results["spike_margins"].copy()
    for c in ("closed_small_x_median", "closed_micro_x_median"):
        show_marg[c] = show_marg[c].round(2)
    _print_table(show_marg.to_string(index=False))

    print("\n[Block 1] National before/after (mean monthly values)")
    _print_table(results["block1_table"].to_string(index=False))
    nat = b1["national"]
    print(f"\n  ratio K_after/K_before point = {nat['ratio_K']['value']:.3f}, "
          f"95% CI {_ci_str(nat['ratio_K']['ci'], '{:.3f}')}, "
          f"1 in CI -> {nat['ratio_K']['includes_one']}")

    print("\n[Block 2] Top-15 OKVED classes by fragmentation coefficient (after)")
    show = top_okved[["okved", "okved_name", "K_before", "K_after", "K_after_ci",
                      "K_ratio", "K_ratio_ci", "ratio_includes_one"]].copy()
    show["okved_name"] = show["okved_name"].str[:40]
    _print_table(show.to_string(index=False))

    print("\n[Block 3] Top-15 regions by small-enterprise closures (after)")
    show = top_regions[["region_code", "region", "closed_small_avg_before",
                        "closed_small_avg_after", "closed_after_ci",
                        "closed_pct_change", "zero_in_diff_ci"]].copy()
    _print_table(show.to_string(index=False))

    print("\n[Block 4] region x OKVED matrix")
    print(f"  total pairs            : {mat['n_pairs_total']}")
    print(f"  pairs with SUFFICIENT  : {mat['n_pairs_sufficient']} "
          f"(>= {mat['min_active_months']} active months in both periods)")
    print(f"  pairs INSUFFICIENT     : {mat['n_pairs_insufficient']} "
          f"(change_ratio set to NaN, NOT silently equal to 1)")
    for cat, n in mat["categories"].items():
        print(f"    - {cat}: {n}")

    print("\n[Robustness] Block 1 under alternative cleaning definitions")
    show = rob[["variant", "label", "n_before", "n_after", "closed_pct",
                "d_closed_pp", "new_pct", "d_new_pp", "K_pct", "d_K_pp"]].copy()
    show.columns = ["variant", "label", "n_b", "n_a", "closed%", "d_closed, pp",
                    "new%", "d_new, pp", "K%", "d_K, pp"]
    _print_table(show.to_string(index=False))

    print("\nNOTE: all comparisons are descriptive. Causal identification "
          "(DiD, region x quarter panel, 1-NOM / CBR data) is out of scope; "
          "see EXECUTIVE_SUMMARY.md and README.md.")
    print("=" * 78)


def _print_table(s: str) -> None:
    for line in s.splitlines():
        print("  " + line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Descriptive analysis of the Russian SME register.")
    parser.add_argument("--iters", type=int, default=5000,
                        help="number of bootstrap iterations (default 5000)")
    parser.add_argument("--seed", type=int, default=42,
                        help="random seed for the bootstrap (default 42)")
    parser.add_argument("--no-plots", action="store_true",
                        help="skip figure rendering")
    parser.add_argument("--no-save", action="store_true",
                        help="skip writing processed tables")
    args = parser.parse_args(argv)

    results = run_pipeline(n_iter=args.iters, seed=args.seed,
                           save_outputs=not args.no_save,
                           make_plots=not args.no_plots)
    print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
