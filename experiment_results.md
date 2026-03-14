# Experiment Report — Crypto News Research Engine

## Experiment 1: Event-Return Correlation

**Hypothesis:** Specific event types cause statistically significant price moves compared to random time windows.

**Methodology:** For each event category, measured avg return of affected assets at T+1h, T+4h, T+24h. Compared to baseline of random time windows of same duration using Mann-Whitney U test (non-parametric).

### Results

- **total_categories:** 7
- **total_tests:** 9
- **significant_results:** 0
- **significant_pct:** 0.0

| Category | Window | N (events) | Avg Return (events) | Avg Return (random) | Difference | U-statistic | p-value | Effect Size | Significant |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ADOPTION | 1h | 14 | -0.1761 | 0.0564 | -0.2325 | 331.0 | 0.253462 | 0.1985 | False |
| ADOPTION | 4h | 14 | -0.1012 | -0.0808 | -0.0204 | 407.0 | 0.938571 | 0.0145 | False |
| ADOPTION | 24h | 14 | 0.7501 | 0.3605 | 0.3896 | 462.0 | 0.496768 | -0.1186 | False |
| MACRO | 1h | 14 | 0.0324 | 0.0564 | -0.024 | 436.5 | 0.747239 | -0.0569 | False |
| MACRO | 4h | 14 | -0.3309 | -0.0808 | -0.2501 | 402.5 | 0.888563 | 0.0254 | False |
| MACRO | 24h | 14 | -0.2053 | 0.3605 | -0.5658 | 352.5 | 0.400496 | 0.1465 | False |
| SENTIMENT | 1h | 24 | -0.0891 | 0.0564 | -0.1455 | 627.0 | 0.418755 | 0.1144 | False |
| SENTIMENT | 4h | 24 | -0.2813 | -0.0808 | -0.2005 | 633.0 | 0.45427 | 0.1059 | False |
| SENTIMENT | 24h | 24 | -0.4883 | 0.3605 | -0.8487 | 582.0 | 0.207458 | 0.178 | False |

**Interpretation:** 0 of 9 category-window combinations showed statistically significant difference from random (p < 0.05). No significant edge detected with current data.

**Limitations:** Short data history limits statistical power. Multiple comparison problem (no Bonferroni correction). Random baseline uses same asset pool, not matched timestamps.

---

## Experiment 2: Sentiment-Return Correlation

**Hypothesis:** More positive sentiment predicts higher subsequent returns.

**Methodology:** Bucketed events into 5 sentiment groups (very negative to very positive). Measured avg subsequent return per bucket at 1h/4h/24h. Tested Spearman rank correlation between sentiment score and 4h return.

### Results

- **spearman_correlation:** 0.3204
- **correlation_p_value:** 0.013372
- **significant:** True
- **n_observations:** 59

| Sentiment Bucket | Window | N | Avg Return (%) | Median Return (%) | Std Dev |
| --- | --- | --- | --- | --- | --- |
| Very Negative | 1h | 16 | 0.0315 | 0.0197 | 0.5719 |
| Very Negative | 4h | 16 | -0.3333 | -0.1573 | 1.1183 |
| Very Negative | 24h | 16 | -0.1591 | -0.3367 | 1.7745 |
| Negative | 1h | 1 | -0.6353 | -0.6353 | 0.0 |
| Negative | 4h | 1 | 0.0095 | 0.0095 | 0.0 |
| Negative | 24h | 1 | 3.4335 | 3.4335 | 0.0 |
| Neutral | 1h | 6 | -0.0607 | 0.0084 | 0.4143 |
| Neutral | 4h | 6 | -0.2106 | 0.0531 | 1.0579 |
| Neutral | 24h | 6 | -1.0657 | -1.058 | 0.6791 |
| Positive | 1h | 6 | -0.4627 | -0.889 | 0.9436 |
| Positive | 4h | 6 | -1.2393 | -1.2653 | 1.4698 |
| Positive | 24h | 6 | -1.0932 | -1.9764 | 1.9612 |
| Very Positive | 1h | 30 | -0.1062 | 0.0566 | 0.4754 |
| Very Positive | 4h | 30 | 0.053 | 0.1953 | 0.8808 |
| Very Positive | 24h | 30 | 0.5474 | 0.1413 | 1.798 |

**Interpretation:** Spearman correlation between sentiment and 4h return: r=0.320 (p=0.0134). Statistically significant relationship.

**Limitations:** VADER sentiment may not capture crypto-specific nuance. Sentiment extracted from event summary, not raw article text. Non-linear relationships not captured by Spearman.

---

## Experiment 3: Narrative Accumulation Test

**Hypothesis:** Events covered by multiple articles (narrative accumulation) predict larger price moves than isolated single-article events.

**Methodology:** Split events into multi-article clusters (article_count > 1) vs single-article events. Compared absolute move sizes using Mann-Whitney U test.

### Results

- **multi_article_events:** 5
- **solo_events:** 54

| Window | Multi-Article N | Multi-Article Avg (%) | Solo N | Solo Avg (%) | Avg |Multi| | Avg |Solo| | p-value | Significant |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1h | 5 | -0.3618 | 54 | -0.0861 | 0.5149 | 0.4489 | 0.247276 | False |
| 4h | 5 | -0.3387 | 54 | -0.1989 | 0.6659 | 0.7705 | 0.902499 | False |
| 24h | 5 | 0.6469 | 54 | 0.0208 | 1.6298 | 1.4104 | 0.33383 | False |

**Interpretation:** Compared magnitude of moves between narrative-accumulated events and isolated events. Larger absolute moves from multi-article clusters would support the narrative accumulation hypothesis.

**Limitations:** Small sample of multi-article events may limit power. Clustering threshold affects what counts as 'multi-article'. Does not account for event severity differences between groups.

---

## Experiment 4: Signal Decay Analysis

**Hypothesis:** Most of the price move happens within the first hour after event detection, making the edge difficult to capture with execution latency.

**Methodology:** For events with data at all 3 windows (1h, 4h, 24h), computed what fraction of the total 24h move occurred in each time bucket: 0-1h, 1h-4h, 4h-24h.

### Results

- **avg_first_hour_fraction:** 0.5527
- **n_events_analyzed:** 59
- **capturable_after_15min:** True

| Time Bucket | Avg Fraction of 24h Move | Median Fraction | N |
| --- | --- | --- | --- |
| 0-1h | 0.5527 | 0.3999 | 59 |
| 1h-4h | 0.688 | 0.5657 | 59 |
| 4h-24h | 0.9083 | 0.9549 | 59 |

**Interpretation:** On average, 55% of the 24h move occurs in the first hour. Most of the move happens early — with 15min execution latency, a significant portion may already be priced in.

**Limitations:** Fractional decomposition can exceed 100% when the move reverses. Does not account for intra-hour timing (move could happen in first 5 minutes vs last 5 minutes of first hour).

---

## Experiment 5: Walk-Forward Validation

**Hypothesis:** The trading strategy maintains positive Sharpe ratio in out-of-sample periods (Sharpe >= 0.5).

**Methodology:** Expanding window walk-forward: train on N days, test on next 7 days. Sliding forward. Latency buffer: 15 minutes. Cost model: 0.30% round-trip. Regime tagged by 30-day BTC trend.

### Results

- **n_windows:** 0
- **in_sample_sharpe:** -0.4624
- **out_of_sample_sharpe:** -0.4624
- **sharpe_degradation:** 0.0
- **is_trades:** 34
- **oos_trades:** 34
- **oos_win_rate:** 0.5
- **edge_appears_real:** False

**Interpretation:** In-sample Sharpe: -0.46, Out-of-sample Sharpe: -0.46. OOS Sharpe < 0.5 — edge may not be reliable. Consider more data or strategy refinement.

**Limitations:** Short data window limits number of walk-forward periods. Regime tagging based on BTC only, may not reflect altcoin regimes. No optimization of signal weights between windows (pure replay).

---
