"""Regression tests locking the pipeline's point values.

The months excluded from the register are chosen by the automatic rule
detect_anomalous_months(), not by a hardcoded list. On the current window
(May 2024 - Apr 2026) that rule flags 2024-07, 2025-07, 2026-02 and 2026-04,
so "before" has 7 months and "after" has 13. anomaly_diagnostics() labels
them churn (the two Julys), lagged_compensation (2026-02, compensated one
month late by the 2026-03 new_small wave) and provisional_net_exit (2026-04,
provisional — its t+1 month is outside the data window).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import src.analysis as A


@pytest.fixture(scope="module")
def results():
    """Run once per test session (bootstrap 500 iters, no figures/CSVs).

    Point statistics are exact functions of the cleaned data and do not depend
    on n_iter; only the bootstrap CIs do, and at 500 iterations the K ratio's
    97.5th percentile sits ~0.95 with a wide margin below 1.0 (checked across
    several seeds), so the CI-dependent assertions stay stable.
    """
    return A.run_pipeline(n_iter=500, seed=42, save_outputs=False, make_plots=False)


def test_period_shape(results):
    shape = results["period_shape"]
    assert shape["before"]["n_months"] == 7
    assert shape["after"]["n_months"] == 13
    assert shape["before"]["months"] == ["2024-05", "2024-06", "2024-08", "2024-09",
                                         "2024-10", "2024-11", "2024-12"]
    assert shape["after"]["months"] == ["2025-01", "2025-02", "2025-03", "2025-04",
                                        "2025-05", "2025-06", "2025-08", "2025-09",
                                        "2025-10", "2025-11", "2025-12", "2026-01",
                                        "2026-03"]


def test_clean_register_exclusions():
    df = A.load_register()
    assert df["region"].nunique() == 90
    assert {"00", "90", "93", "94", "95"} <= set(df["region"])
    assert {"01", "77", "86"} <= set(df["region"])

    # The automatic rule, not a manual list, decides which months are dropped.
    detected = A.detect_anomalous_months(df)
    assert detected == ["2024-07", "2025-07", "2026-02", "2026-04"]

    clean = A.clean_register(df)
    assert clean["region"].nunique() == 85
    assert set(clean["region"]).isdisjoint({"00", "90", "93", "94", "95"})
    months = set(clean["month"].dt.strftime("%Y-%m"))
    assert months.isdisjoint(set(detected))
    assert set(clean[["month", "region", "okved"]].duplicated()) == {False}


def test_detect_anomalous_months_on_synthetic_data():
    """The detection logic is verified independently of the real figures."""
    rng = np.random.default_rng(1)
    n_months = 24
    months = pd.date_range("2023-01-01", periods=n_months, freq="MS").repeat(3)
    base = pd.DataFrame({
        "month": months,
        "region": list("ABC") * n_months,
        "okved": ["01"] * (3 * n_months),
        "closed_small": rng.integers(40, 60, 3 * n_months),
        "closed_micro": rng.integers(100, 120, 3 * n_months),
        "new_micro": rng.integers(300, 320, 3 * n_months),
    })
    # One month with a closed_micro spike (~90x the median).
    base.loc[base["month"].eq(pd.Timestamp("2023-11-01")), "closed_micro"] = 10000
    assert A.detect_anomalous_months(base) == ["2023-11"]
    # A generous threshold stops flagging that spike.
    assert A.detect_anomalous_months(base, threshold=100) == []
    # A closed_small-only spike is enough on its own (closed_micro left normal).
    base2 = base.copy()
    base2.loc[base2["month"].eq(pd.Timestamp("2023-11-01")), "closed_micro"] = 110
    base2.loc[base2["month"].eq(pd.Timestamp("2023-03-01")), "closed_small"] = 500
    assert A.detect_anomalous_months(base2) == ["2023-03"]


def test_block1_point_values(results):
    nat = results["block1"]["national"]["point"]
    assert nat["closed_small"]["before"] == pytest.approx(383.857143, rel=1e-5)
    assert nat["closed_small"]["after"] == pytest.approx(610.846154, rel=1e-5)
    assert nat["new_micro"]["before"] == pytest.approx(113683.571429, rel=1e-5)
    assert nat["new_micro"]["after"] == pytest.approx(125946.538462, rel=1e-5)
    assert nat["closed_small"]["pct_change"] == pytest.approx(59.133721, rel=1e-4)
    assert nat["new_micro"]["pct_change"] == pytest.approx(10.786930, rel=1e-3)


def test_block1_k_smoothed(results):
    k = results["block1"]["national"]["point"]["K"]
    assert k["before"] == pytest.approx(295.394209, rel=1e-4)
    assert k["after"] == pytest.approx(205.848378, rel=1e-4)
    ratio = results["block1"]["national"]["ratio_K"]
    assert ratio["value"] == pytest.approx(0.696860, rel=1e-3)
    assert not ratio["includes_one"]
    assert ratio["ci"][1] < 1.0


def test_block3_region77(results):
    row = results["block3_top15"].set_index("region_code").loc["77"]
    assert row["closed_small_avg_after"] == pytest.approx(122.307692, rel=1e-4)


def test_block4_counts(results):
    mat = results["block4_summary"]
    assert mat["n_pairs_total"] == 7152
    assert mat["n_pairs_sufficient"] == 4734
    assert mat["n_pairs_insufficient"] == 2418
    cats = mat["categories"]
    assert cats["fragmentation down (<1)"] == 1821
    assert cats["fragmentation up (>1)"] == 2887
    assert cats["no change (==1)"] == 26


def test_robustness_variants(results):
    variants = results["robustness"]["variant"].tolist()
    assert variants == ["base", "manual", "july_kept",
                        "+region_00", "+region_90", "+region_93",
                        "+region_94", "+region_95", "after2025"]
    base = results["robustness"].set_index("variant").loc["base"]
    assert base["closed_pct"] == pytest.approx(59.133721, rel=1e-4)


def test_anomaly_diagnostics_real_data(results):
    """On the real register the four flagged months split 2 churn + 1 lagged
    compensation + 1 provisional exit pattern."""
    diag = results["anomaly_diagnostics"]
    assert diag["month"].tolist() == ["2024-07", "2025-07", "2026-02", "2026-04"]
    assert diag["anomaly_type"].tolist() == ["churn", "churn", "lagged_compensation",
                                             "provisional_net_exit"]
    warns = results["cleaning"].get("unclassified_warnings")
    assert warns is not None and any("lagged-compensation" in w and "2026-04" in w for w in warns)
    typ = results["cleaning"]["anomaly_types"]
    assert typ == {"2024-07": "churn", "2025-07": "churn",
                   "2026-02": "lagged_compensation", "2026-04": "provisional_net_exit"}
    # The February loss is matched one month later by the 2026-03 new_small wave
    # (within the 20% tolerance), so it labels lagged_compensation, not an exit.
    feb = diag.set_index("month").loc["2026-02"]
    assert feb["compensation_month"] == "2026-03"
    assert feb["compensation_metric"] == "new_small"
    assert 0.8 <= feb["compensation_ratio"] <= 1.2
    # July churn months: stock GROWS; the provisional exit month (2026-04)
    # has stock falling ~= closures.
    churn = diag.set_index("month").loc[["2024-07", "2025-07"]]
    assert (churn["actual_delta_small"] > 0).all()
    net = diag.set_index("month").loc[["2026-04"]]
    assert abs(net["actual_delta_small"] + net["closed_small"]).max() <= 50
    sim = results["lagged_compensation_similarity"]
    assert sim["month"].tolist() == ["2026-02"]
    assert sim["region_cosine"].iloc[0] > 0.99


def test_anomaly_diagnostics_synthetic():
    """The classification logic is verified independently of the real figures."""
    d = lambda m: pd.Timestamp(m)
    base = {
        # 9 baseline months: tiny, stable flows; active stocks constant.
        "month": [d(f"2023-{mm:02d}-01") for mm in (1, 2, 3, 4, 5, 7, 9, 11, 12)] * 3,
        "region": list("ABC") * 9,
        "okved": ["01"] * 27,
        "closed_small": [10] * 27,
        "new_small": [10] * 27,
        "active_small": [1000] * 27,
        "closed_micro": [100] * 27,
        "new_micro": [100] * 27,
        "active_micro": [100000] * 27,
    }
    df = pd.DataFrame(base)
    def add(month, closed_s, new_s, act_s, closed_m, new_m):
        for i, r in enumerate("ABC"):
            df.loc[len(df)] = [pd.Timestamp(month), r, "01", closed_s, new_s, act_s,
                               closed_m, new_m, 100000]
    add("2023-06-01", 100, 100, 1050, 10000, 5000)   # churn: micro spike + compensated
    add("2023-08-01", 300, 10, 700, 100, 100)        # provisional_net_exit: uncompensated
    add("2023-10-01", 200, 10, 700, 100, 100)        # unclassified: spike but no stock move

    flagged = A.detect_anomalous_months(df)
    assert flagged == ["2023-06", "2023-08", "2023-10"]
    diag, warnings = A.anomaly_diagnostics(df, flagged)
    got = dict(zip(diag["month"], diag["anomaly_type"]))
    assert got == {"2023-06": "churn", "2023-08": "provisional_net_exit",
                   "2023-10": "unclassified"}
    assert len(warnings) == 1 and "UNCLASSIFIED" in warnings[0] and "2023-10" in warnings[0]


def test_lagged_compensation_synthetic():
    """A flagged month whose stock loss is matched one month later by a
    registration spike is labelled lagged_compensation, not provisional_net_exit."""
    d = lambda m: pd.Timestamp(m)
    df = pd.DataFrame(columns=["month", "region", "okved", "closed_small",
                               "new_small", "active_small", "closed_micro",
                               "new_micro", "active_micro"])
    for mm in (1, 2, 3, 4, 5, 6, 9, 10, 11, 12):          # baseline months
        for r in "ABC":
            df.loc[len(df)] = [d(f"2023-{mm:02d}-01"), r, "01", 10, 10, 1000,
                               100, 100, 100000]
    # July: closures spike (total 300), registrations normal, stock falls ~= closures.
    for r in "ABC":
        df.loc[len(df)] = [d("2023-07-01"), r, "01", 100, 10, 900, 100, 100, 100000]
    # August: same-sized new_small wave (total 300) — the lagged compensation.
    for r in "ABC":
        df.loc[len(df)] = [d("2023-08-01"), r, "01", 10, 100, 900, 100, 100, 100000]

    flagged = A.detect_anomalous_months(df)
    assert flagged == ["2023-07"]
    diag, _ = A.anomaly_diagnostics(df, flagged)
    got = diag.set_index("month").loc["2023-07"]
    assert got["anomaly_type"] == "lagged_compensation"
    assert got["compensation_month"] == "2023-08"
    assert got["compensation_metric"] == "new_small"
    assert got["compensation_ratio"] == pytest.approx(1.0, abs=1e-6)


def test_provisional_net_exit_without_t_plus_1():
    """A flagged month with no t+1 month available (end of window) is labelled
    provisional_net_exit and raises the lagged-compensation rule warning."""
    d = lambda m: pd.Timestamp(m)
    df = pd.DataFrame(columns=["month", "region", "okved", "closed_small",
                               "new_small", "active_small", "closed_micro",
                               "new_micro", "active_micro"])
    for mm in (2, 3, 4):
        for r in "AB":
            df.loc[len(df)] = [d(f"2023-{mm:02d}-01"), r, "01", 10, 10, 1000,
                               100, 100, 100000]
    # May (last month of the window): closure spike, stock falls, no June data.
    for r in "AB":
        df.loc[len(df)] = [d("2023-05-01"), r, "01", 100, 10, 900, 100, 100, 100000]

    flagged = A.detect_anomalous_months(df)
    assert flagged == ["2023-05"]
    diag, warnings = A.anomaly_diagnostics(df, flagged)
    got = diag.set_index("month").loc["2023-05"]
    assert got["anomaly_type"] == "provisional_net_exit"
    assert any("lagged-compensation" in w and "2023-05" in w for w in warnings)


def test_cleaning_scenarios(results):
    """Scenario exclude_compensation_patterns (drops the July churn + the lagged
    2026-02 compensation, retains the unresolved 2026-04) vs exclude_all_flagged
    (headline)."""
    scn = results["cleaning_scenarios"].set_index("scenario")
    a = scn.loc["exclude_compensation_patterns"]
    b = scn.loc["exclude_all_flagged"]
    assert a["n_before"] == b["n_before"] == 7
    assert a["n_after"] == 14 and b["n_after"] == 13
    assert a["months_dropped"] == "2024-07, 2025-07, 2026-02"
    assert b["months_dropped"] == "2024-07, 2025-07, 2026-02, 2026-04"
    assert b["closed_pct"] == pytest.approx(59.133721, rel=1e-4)
    assert a["closed_pct"] == pytest.approx(276.311872, rel=1e-4)
    assert b["K_ratio"] == pytest.approx(0.696860, rel=1e-3)
    assert a["K_ratio"] == pytest.approx(0.292235, rel=1e-3)


def test_threshold_sensitivity():
    """Lock the spike-threshold fork across 2.0-5.0.

    The 3.0 constant is itself a methodological choice, so the flag rule must
    return the same four months at 2.0, 3.0 and 5.0, and the margin away from
    the decision boundary must stay wide (flagged months > 8x the closed_small
    median, every clean month < 2x) — otherwise the headline would depend on
    the exact threshold.
    """
    expected = ["2024-07", "2025-07", "2026-02", "2026-04"]
    raw = A.load_register()
    for t in (2.0, 3.0, 5.0):
        detected = A.detect_anomalous_months(raw, threshold=t)
        assert detected == expected, f"threshold={t} flagged {detected}"

    margins = A.spike_margins(raw)
    flagged = margins[margins["flagged_at_threshold"]]
    clean = margins[~margins["flagged_at_threshold"]]
    assert flagged["closed_small_x_median"].min() > 8.0
    assert clean["closed_small_x_median"].max() < 2.0

    sens = A.threshold_sensitivity(raw, n_iter=200, seed=42)
    assert len(sens) == 5
    assert sens["flagged_months"].nunique() == 1
    assert sens["flagged_months"].iloc[0] == ", ".join(expected)
    assert sens["closed_pct"].nunique() == 1
    assert sens["K_ratio"].nunique() == 1
    assert sens["K_ratio"].iloc[0] == pytest.approx(0.696860, rel=1e-3)