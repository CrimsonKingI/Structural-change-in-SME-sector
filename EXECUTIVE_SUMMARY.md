# Executive Summary — Structural changes in the Russian SME sector (2025–2026)

*Russian title: «Структурные изменения сектора МСП РФ в 2025–2026 годах»* (former working
title «Влияние налоговой политики РФ на сектор МСП» was dropped: the study describes
structural change, it does not estimate the effect of tax policy).

Reproducible Python port of the original Excel study. **Descriptive only** — this document
reports an association, not a causal effect.

## Motivation

The Russian SME register compiles monthly flows of **micro-enterprise registrations**
(`new_micro`) and **small-enterprise closures** (`closed_small`) per region × OKVED class.
A high fragmentation coefficient `K = (new + 1) / (closed + 1)` signals many new micro firms
per closure. The original study asked how the intensity of "new micro per closed small"
changed in 2025–2026 relative to 2024. This repository re-implements that analysis in Python
with bootstrap confidence intervals and an **automatic rule** for detecting administrative
months instead of the hand-picked list of the Excel study. Tax receipts (form 1-NOM) and
credit data (CBR key rate) are deliberately **not linked**: the scope is structural change
in the register, not policy effects.

## Data & Method

- Register of 176,647 region × class × month records, May 2024 – Apr 2026.
- Regions `00, 90, 93, 94, 95` excluded (FNS service row and four regions with incomplete data).
- **Automatic month-cleaning rule.** Months are not excluded by a hand-picked list. For every
  calendar month, the national totals of `closed_small` and `closed_micro` are compared with
  their series medians; a month is flagged when **either** total exceeds its median by more than
  **3×** (`detect_anomalous_months`, threshold configurable, default 3.0). On the available
  window the rule flags **2024-07, 2025-07, 2026-02 and 2026-04** (national `closed_small` =
  7,625 / 8,343 / 5,299 / 12,346 vs a ~540 monthly median).
- **Why stop at four — anomaly classification.** The 3× rule only measures the *flow*. To tell
  "register recategorisation" from "exit patterns", `anomaly_diagnostics()` reconciles flows with
  stocks for each flagged month: the flow-implied stock change `new_small − closed_small`
  against the observed `active_small(month) − active_small(prev month)`, and checks whether a
  neighbouring registration wave matches the loss.

  | month | closed_small | new_small (×median) | actual Δstock | classification |
  |---|---|---|---|---|
  | 2024-07 | 7,625 | 1,364 (11.1×) | **+15,458** | **churn** — re-categorisation |
  | 2025-07 | 8,343 | 1,024 (8.4×) | **+11,931** | **churn** — re-categorisation |
  | 2026-02 | 5,299 | 49 (0.40×) | **−5,253** | **lagged_compensation** — matched by 2026-03 |
  | 2026-04 | 12,346 | 51 (0.42×) | **−12,299** | **provisional_net_exit** — t+1 unavailable |

  The two **Julys are a re-categorisation artefact** (an annual July methodology change in the
  FNS average-headcount rule, which re-maps enterprises across size classes): closures are
  compensated by same-month registrations/re-tags (`new_small` itself spikes ~8–11× median) and
  the small-enterprise stock actually **grows**. A secondary/reference source supports the same
  calendar-day mechanism: the FNS runs an annual review of the SME register on **10 July** —
  excluding enterprises that no longer meet the criteria, checking prior-year reporting, and
  removing previous-year registration marks — which aligns with both spikes landing on the
  same date in two consecutive years. This is an external, reference-level explanation (not a
  primary normative act); it reinforces, rather than replaces, the flow/stock evidence above.
  The two **2026 months are different**:
  registrations stay normal (~0.4× median) and the stock falls by almost exactly the number of
  closures (−5,253 ≈ −5,299; −12,299 ≈ −12,346). 2026-02, however, is matched one month later by
  the 2026-03 `new_small` wave (4,907 ≈ 93.4% of the February decline) — a **lagged compensation
  pattern** consistent with a delayed registry adjustment, not necessarily an economic exit.
  2026-04 is treated as a **provisional exit pattern**: aggregate flows alone cannot distinguish
  an economic exit from an administrative reclassification (the code does not track individual
  firms), and its t+1 month lies outside the data window, so the lagged-compensation rule cannot
  run for it.
- **The cleaning fork, in two scenarios.** Because the same flag covers an artefact and exit
  patterns, two defensible samples result (both computed and shipped in `cleaning_scenarios.csv`):
  - **`exclude_all_flagged` — drop all four flagged months (headline/base, 7m / 13m).** The more
    restrictive cleaning scenario: removes the two re-categorisation Julys, the lagged-compensation
    2026-02 and the provisional-exit 2026-04. Closures **+59.1%** (CI `[35.2, 86.2]` — the headline Block-1
    bootstrap, reused here), K_after/K_before = **0.697** (CI `[0.524, 0.948]`).
  - **`exclude_compensation_patterns` — drop the compensation months only (7m / 14m).** The
    Julys and 2026-02 are removed; the *unresolved* 2026-04 month stays in the sample. Closures
    **+276.3%** (CI `[41.9, 749.5]`), K_ratio = **0.292** (CI `[0.121, 0.863]`). The point
    estimate is dominated by that single month, and the CI is correspondingly wide.
- Periods (headline): **before = 7 months** (2024 minus 2024-07), **after = 13 months**
  (Jan 2025 – Apr 2026 minus 2025-07, 2026-02, 2026-04).
- The fork is reported rather than hidden: the alternative sample differs from the headline only
  in the treatment of 2026-02 and 2026-04. The +59.1% figure is not framed as a bound; it is the
  reading under the more restrictive cleaning scenario (Scenario B, `exclude_all_flagged`). All
  blocks below use the cleaned, reproducible base.
- 5,000 bootstrap resamples of months within each period → 95% percentile confidence intervals
  for each statistic, the period difference, and the ratio K_after/K_before.
- Causal design (DiD, region × quarter panel, 1-NOM / CBR data) is **explicitly out of scope**
  and replaced by a sensitivity table.

## Key Findings

1. **National (Block 1).** Mean monthly small-enterprise closures rose from **384 → 611**
   (**+59.1%**, CI `[35.2, 86.2]`, 0 not in CI). Micro-enterprise registrations were essentially
   flat (**+10.8%**, CI `[-9.5, 36.7]` — not significant[^1]). The fragmentation coefficient
   **fell from 295.4 to 205.8 (−30.3%, CI `[-47.6, -5.2]`)**; the ratio
   K_after/K_before = **0.697** (CI `[0.524, 0.948]`, 1 not in CI) — fewer new micro firms per
   closed small enterprise. *(The alternative `exclude_compensation_patterns` sample, which
   retains the unresolved 2026-04 month in the 'after' period, reads much higher — +276.3%;
   that signal is driven by a single month whose own classification is uncertain rather than by
   compensation waves, so it is reported as a fork alongside the headline, not as the headline.)*

2. **OKVED classes (Block 2).** Highest after-period fragmentation in Education (85, K=1,043),
   Land transport (49, 808), Other personal services (96, 757), Professional/scientific
   services (74, 690), Computer repair (95, 574). Under the cleaned periods **no** top-class
   ratio CI excludes 1 — a weaker class-level picture than in the Excel report.

3. **Regions (Block 3).** Moscow (122 closures/month, **+51.5%**), Saint Petersburg (57,
   +48.8%), Moscow Oblast (36, +57.2%), Sverdlovsk (22, +62.1%), Krasnodar Krai (21, +49.2%),
   Novosibirsk (16, +64.8%). CIs exclude 0 for all top-15 regions.

4. **Region × OKVED matrix (Block 4).** Of 7,152 pairs, 4,734 (66%) have ≥3 active months in
   **both** periods and are evaluated; **61%** show fragmentation up, **38%** down, 0.5%
   unchanged. The 2,418 insufficient pairs are flagged as NaN rather than silently set to 1
   (the Excel version's substitution produced 19.7% of "no change").
   The top-15 lists in Blocks 2–3 and the matrix in Block 4 highlight the strongest cells
   *after* observing the data; they are not independent pre-registered hypotheses, so the
   nominal CIs are reported descriptively (no multiplicity correction is applied).

## Robustness

| Variant | closure % change (Δ vs base, pp) |
|---|---|
| **`exclude_all_flagged` (base, headline)** — drop all 4 flagged, 7m / 13m | **+59.1%** |
| `exclude_compensation_patterns` — drop Julys + 2026-02, keep 2026-04, 7m / 14m | +276.3% (+217.2) |
| July kept, 2026 spikes only (8m / 14m) | −9.8% (−69.0) |
| After = 2025 only (7m / 11m) | +56.1% (−3.0) |
| Re-add any excluded region | +59.1 … +59.5% (≈ 0) |

The headline is **+59.1%** (Scenario B, the more restrictive cleaning), not the old report's
**+341.9%** — and the anomaly classification now explains why the two readings differ. Flagged months split into **churn**
(two Julys: register re-categorisation, stock actually grows), **lagged_compensation** (2026-02,
matched by the 2026-03 new_small wave) and **provisional_net_exit** (2026-04, uncompensated and
unresolvable within the data window). `exclude_compensation_patterns` keeps 2026-04 in the
sample and reads **+276.3%**; `exclude_all_flagged` removes it and reads **+59.1%**. Every other
variant that removes all detected anomalies — after-2025, threshold forks, region re-tests —
clusters around **+56…+60%**. The robust reading: *once the register artefacts are removed,
closures rose; the unresolved 2026-04 month, if counted as an exit, moves the answer from +59.1%
to +276.3%, while the narrower threshold/treatment tests cluster at +56…+60%*. The difference
between the two scenarios is a single month's classification, and aggregate data cannot decide
it — the fork is reported rather than resolved. K_after/K_before is **0.697** (headline) or
**0.292** (2026-04 kept) accordingly.

### The 3.0 spike threshold is a declared, non-critical fork

The **3.0** in `detect_anomalous_months(threshold=3.0)` is itself a methodological constant.
The table re-runs the whole flag → classify → clean → Block-1 chain for **2.0, 2.5, 3.0, 4.0
and 5.0** (shipped in `data/processed/threshold_sensitivity.csv`):

| threshold | flagged months | closed% (Block 1) | CI | K_ratio | CI |
|---|---|---|---|---|---|
| 2.0 | 2024-07, 2025-07, 2026-02, 2026-04 | +59.1% | [35.2, 86.2] | 0.697 | [0.524, 0.948] |
| 2.5 | same four | +59.1% | [35.9, 85.8] | 0.697 | [0.528, 0.953] |
| **3.0 (base)** | same four | **+59.1%** | **[35.2, 86.2]** | **0.697** | **[0.524, 0.948]** |
| 4.0 | same four | +59.1% | [35.1, 85.2] | 0.697 | [0.527, 0.953] |
| 5.0 | same four | +59.1% | [35.5, 86.8] | 0.697 | [0.525, 0.945] |

All five thresholds flag **exactly the same four months** — no month appears or disappears
anywhere in 2.0–5.0, and the classification (churn Julys, lagged 2026-02, provisional 2026-04) is
unchanged. Point values are identical by construction (same excluded set). The row at the base
threshold (**3.0**) is the headline cleaning, so it **reuses the headline Block-1 bootstrap
itself** — its CI `[35.2, 86.2]` / `[0.524, 0.948]` is the exact headline CI, not a second
independent run. The other rows are independent draws; their small CI spread is only the
bootstrap sample (`seed + i`). Why the plateau is wide — per-month **distance to the
decision boundary** as `closed / median` (full 24-row table in
`data/processed/spike_margins.csv`):

| month | closed_small (×median) | closed_micro (×median) |
|---|---|---|
| 2024-07 | 14.2× | 9.0× |
| 2025-07 | 15.5× | 8.9× |
| 2026-02 | 9.8× | 3.9× |
| 2026-04 | 22.9× | 4.9× |
| nearest clean month (2026-01) | 1.67× | 1.60× |

The boundary sits inside an **empty plateau between ~1.7× and ~9.8×** (closed_small terms):
the constant would have to drop below ~1.7 to flag a fifth month or rise above ~9.8 to lose
one. **3.0 is deep inside that plateau**, so the headline +59.1% and K_ratio 0.697 are not
sensitive to the threshold over the tested 2.0–5.0 range.

## Limitations

- **No causal inference**: pre/post comparison alone cannot isolate the effect of any policy
  (e.g. the micro-business tax regime) from unrelated shocks; DiD/panel work was declared out of
  scope and kept out of this repository. Tax receipts (1-NOM) and credit (CBR key-rate) data
  were deliberately not linked for the same reason.
- **Anomalous months & the cleaning fork**: the automatic 3×-median rule flags four months;
  flow/stock classification (`anomaly_diagnostics`) labels two as register re-categorisation
  (churn Julys), 2026-02 as a lagged compensation pattern and 2026-04 as a provisional exit
  pattern (its t+1 month is outside the data window). Both samples are legitimate depending on
  how the rule is read, so the headline keeps the all-flagged cleaning (+59.1%), and the sample
  retaining the unresolved 2026-04 month yields +276.3%; a single month's classification drives
  the difference, and aggregate data cannot resolve it.
- **Laplace smoothing**: `+1` on `K` (per the Excel report's smoothed coefficient) shifts point
  estimates ~0.1–0.3% vs raw ratios.
- **Asymmetric periods** (7 vs 13 months) and no seasonal adjustment.
- Register structure and ETL are reproduced from the original files without re-verification
  against source registries.

---

[^1]: Registrations: +10.8% under the base cleaning (95% CI includes 0); under the 2025-only
    variant they are slightly negative (−2.7%).

*Generated by `src/analysis.py` (seed 42, 5,000 bootstrap iterations). Months in the
cleaning are chosen by `detect_anomalous_months()` and labelled by `anomaly_diagnostics()`
(churn / lagged_compensation / provisional_net_exit); the two cleaning scenarios are in
`cleaning_scenarios.csv`. See `notebooks/MSP_pipeline.ipynb` for the full run.*