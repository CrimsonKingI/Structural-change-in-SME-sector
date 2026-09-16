"""Build notebooks/MSP_pipeline.ipynb from source cells."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3 (MSP venv)", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3"},
}

cells = [
    (
        "markdown",
        "# Structural changes in the Russian SME sector — descriptive pipeline\n\n"
        "Reproducible Python port of the original Excel study *MSP_Fragmentation_Analysis.xlsx* "
        "(May 2024 – Apr 2026). Everything is descriptive; see `EXECUTIVE_SUMMARY.md`. "
        "Tax receipts (form 1-NOM) and credit (CBR key-rate) data are deliberately not linked.\n\n"
        "Same four analytical blocks (national trend, OKVED classes, regions, region×OKVED matrix) "
        "with bootstrap 95% confidence intervals and a sensitivity check that replaces the "
        "causal (DiD) design of the original study.",
    ),
    (
        "code",
        "import sys\nsys.path.insert(0, \"..\")\n\n"
        "import src.analysis as A\n"
        "print('root:', A.ROOT)\n"
        "print('raw csv:', A.RAW_CSV)\n"
        "print(A.__doc__.splitlines()[0])",
    ),
    (
        "markdown",
        "## Run the whole pipeline\n\n"
        "One call reproduces the full analysis: cleaning, four blocks, bootstrap CIs, "
        "sensitivity table, all figures and processed CSVs.",
    ),
    ("code", "res = A.run_pipeline(seed=42, n_iter=5000, save_outputs=True, make_plots=True)\n\n"
             "print('result keys:', sorted(res.keys()))"),
    (
        "markdown",
        "### Anomaly diagnostics & cleaning scenarios\n\n"
        "`detect_anomalous_months` flags abnormal months by flow magnitude alone; "
        "`anomaly_diagnostics` reconciles flows with stocks (`new_small - closed_small` vs "
        "the observed change of `active_small`) and checks neighbouring registration waves, "
        "to label the month as *churn* / *lagged_compensation* (closures matched by same- or "
        "next-month registrations — a pattern consistent with registry re-categorisation or a "
        "delayed adjustment) or *provisional_net_exit* (an unresolved exit pattern; the "
        "compensating flow is restricted to `new_small`, since the losses are small-enterprise "
        "losses). Both July spikes are *churn*: new_small itself spikes ~8–11× its median and "
        "the small-enterprise stock grows. The pattern is consistent with the documented "
        "annual FNS register review — a secondary/reference source puts the annual "
        "SME-register review on 10 July (exclusion of non-qualifying enterprises, prior-year "
        "reporting check, removal of previous-year registration marks), matching both spikes "
        "landing on the same date two years running.\n\n"
        "Two alternative cleaning specifications result (data/processed/cleaning_scenarios.csv):\n\n"
        "- **`exclude_compensation_patterns`** — drop the Julys and 2026-02 "
        "(lagged-compensation), keep 2026-04: closures **+276.3%**;\n"
        "- **`exclude_all_flagged` (headline)** — drop all four flagged months: "
        "closures **+59.1%**.",
    ),
    ("code", "diag = res['anomaly_diagnostics']\n"
             "print('anomaly_diagnostics (flagged months, flow/stock reconciliation):\\n')\n"
             "print(diag[['month', 'anomaly_type', 'closed_small', 'new_small',\n"
             "           'actual_delta_small', 'implied_delta_small', 'new_small_x_median']]\n"
             "      .to_string(index=False))\n\n"
             "print('\\ncleaning_scenarios (Block 1 under each cleaning):\\n')\n"
             "print(res['cleaning_scenarios'][['scenario', 'label', 'months_dropped',\n"
             "          'n_before', 'n_after', 'closed_pct', 'closed_pct_ci',\n"
             "          'K_ratio', 'K_ratio_ci']].to_string(index=False))"),
    (
        "markdown",
        "### Block 1 — national before/after\n\n"
        "Mean monthly small-enterprise closures, new micro-enterprise registrations and the "
        "fragmentation coefficient `K = (new + 1) / (closed + 1)` (Laplace-smoothed, matching "
        "the smoothed figure used in the Excel report, §4.4), with 95% percentile bootstrap "
        "CIs from resampling calendar months within each period (7 before, 13 after).",
    ),
    ("code", "print(res['block1_table'][['metric', 'label', 'before', 'after', 'before_ci',\n"
             "                        'after_ci', 'diff', 'pct_change', 'pct_ci', 'diff_ci',\n"
             "                        'zero_in_diff_ci']].to_string(index=False))\n"
             "nat = res['block1']['national']\n"
             "print(f\"\\nratio K_after/K_before = {nat['ratio_K']['value']:.3f} \"\n"
             "      f\"(95% CI {nat['ratio_K']['ci'][0]:.3f} .. {nat['ratio_K']['ci'][1]:.3f})\")"),
    (
        "markdown",
        "### Block 2 — OKVED classes\n\n"
        "Top-15 classes ranked by `K_after` (Fragmentation coefficient in the after period), "
        "same ranking as the Excel sheet `Блок_2`. Labels from the OKVED-2 classifier. "
        "*Note:* `K` can be unstable for classes with very low closure counts, so the ranking "
        "should be interpreted jointly with the underlying closure and registration volumes.",
    ),
    ("code", "print(res['block2_top15'][['okved', 'okved_name', 'K_before', 'K_after',\n"
             "                        'K_after_ci', 'K_ratio', 'ratio_includes_one']]\n"
             "      .to_string(index=False))"),
    (
        "markdown",
        "### Block 3 — regions\n\n"
        "Top-15 regions by small-enterprise closures (avg/month) in the after period, "
        "with bootstrap CIs and the closure `% change`. All 15 selected regions have 95% "
        "bootstrap CIs for the before/after difference that exclude zero; these intervals are "
        "descriptive because the regions were selected using observed after-period data.",
    ),
    ("code", "print(res['block3_top15'][['region_code', 'region', 'closed_small_avg_before',\n"
             "                        'closed_small_avg_after', 'closed_after_ci',\n"
             "                        'closed_pct_change', 'zero_in_diff_ci']]\n"
             "      .to_string(index=False))"),
    (
        "markdown",
        "### Block 4 — region × OKVED matrix\n\n"
        "Change of the fragmentation coefficient per active pair during 2024 with 2025–2026 "
        "(ordered so the heatmap matches the Excel `Матрица`: **OKVED classes on the X axis, "
        "regions on the Y axis**). Cells with fewer than 3 active months in **either** "
        "period are flagged *insufficient (NaN)* and excluded instead of being silently set "
        "to 1 as in the Excel version.",
    ),
    ("code", "piv = res['block4_pivot']\n"
             "print('pivot shape (regions x okved):', piv.shape)\n"
             "mat = res['block4_summary']\n"
             "print(f\"total pairs {mat['n_pairs_total']} | sufficient {mat['n_pairs_sufficient']} \"\n"
             "      f\"| insufficient {mat['n_pairs_insufficient']} \"\n"
             "      f\"(change_ratio -> NaN, threshold >= {mat['min_active_months']} active months)\\n\")\n"
             "for cat, n in mat['categories'].items():\n"
             "    print(f'  {cat}: {n}')"),
    (
        "markdown",
        "### Sensitivity (alternative cleaning definitions)\n\n"
        "The original study used a causal (DiD) design; that inference is **out of scope** here. "
        "Instead we stress the headline Block 1 statistic (closure `% change`) across cleaning "
        "choices. The anomaly classification describes the flag: the two Julys show a churn "
        "pattern (closures matched by same-month registrations, consistent with the documented "
        "annual FNS reclassification), 2026-02 is a lagged compensation pattern and 2026-04 "
        "an unresolved exit pattern. The headline (`exclude_all_flagged`, all four dropped) reads "
        "+59.1%; `exclude_compensation_patterns`, which retains the unresolved 2026-04, reads "
        "+276.3% — the whole gap is that single month's treatment, which aggregate data cannot "
        "resolve.",
    ),
    ("code", "print(res['robustness'][['variant', 'label', 'n_before', 'n_after', 'closed_pct',\n"
             "                        'd_closed_pp', 'new_pct', 'd_new_pp', 'K_pct', 'd_K_pp']]\n"
             "      .to_string(index=False))"),
    (
        "markdown",
        "### Figures\n\n"
        "All figures are written to `../figures/`:\n\n"
        "- `fig1_national_trend.png` — monthly time series, July spikes marked;\n"
        "- `fig2_block1.png` — Block 1 bars with bootstrap CIs;\n"
        "- `fig3_block2_okved.png` — top-15 OKVED;  \n"
        "- `fig4_block3_regions.png` — top-15 regions;  \n"
        "- `fig5_matrix_heatmap.png` — region×OKVED heatmap;  \n"
        "- `fig6_robustness.png` — sensitivity of the headline effect.",
    ),
    ("code", "from IPython.display import Image, display\n"
             "import os\n"
             "figs = sorted(p.name for p in A.FIGURES_DIR.glob('*.png'))\n"
             "print('figures in', A.FIGURES_DIR, ':', figs)\n"
             "for f in ['fig1_national_trend', 'fig2_block1', 'fig3_block2_okved',\n"
             "          'fig4_block3_regions', 'fig6_robustness']:\n"
             "    if os.path.exists(f'../figures/{f}.png'):\n"
             "        display(Image(filename=f'../figures/{f}.png'))"),
]

nb["cells"] = [nbf.v4.new_markdown_cell(src) if kind == "markdown" else nbf.v4.new_code_cell(src)
               for kind, src in cells]

out = "notebooks/MSP_pipeline.ipynb"
with open(out, "w", encoding="utf-8") as fh:
    nbf.write(nb, fh)
print("wrote", out)