# Experiment Report — Crypto News Research Engine

## Experiment 1: Event-Return Correlation

**Hypothesis:** Specific event types cause statistically significant price moves compared to random time windows.

**Methodology:** For each event category, measured avg return of affected assets at T+1h, T+4h, T+24h. Compared to baseline of random time windows of same duration using Mann-Whitney U test (non-parametric).

### Results

- **total_categories:** 8
- **total_tests:** 21
- **significant_results:** 2
- **significant_pct:** 9.5

| Category | Window | N (events) | Avg Return (events) | Avg Return (random) | Difference | U-statistic | p-value | Effect Size | Significant |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ADOPTION | 1h | 43 | -0.0885 | -0.005 | -0.0835 | 5226.5 | 0.773049 | 0.0276 | False |
| ADOPTION | 4h | 43 | -0.1954 | 0.0493 | -0.2447 | 4606.5 | 0.134523 | 0.143 | False |
| ADOPTION | 24h | 43 | 0.0582 | 0.0395 | 0.0187 | 5267.0 | 0.83408 | 0.0201 | False |
| EXCHANGE | 1h | 7 | 0.3388 | -0.005 | 0.3437 | 1228.0 | 0.069172 | -0.4034 | False |
| EXCHANGE | 4h | 7 | -0.5796 | 0.0493 | -0.629 | 645.0 | 0.23674 | 0.2629 | False |
| EXCHANGE | 24h | 7 | -1.3721 | 0.0395 | -1.4116 | 848.0 | 0.891332 | 0.0309 | False |
| MACRO | 1h | 37 | 0.038 | -0.005 | 0.043 | 5140.0 | 0.274844 | -0.1114 | False |
| MACRO | 4h | 37 | -0.0575 | 0.0493 | -0.1068 | 4256.5 | 0.434777 | 0.0797 | False |
| MACRO | 24h | 37 | 0.0537 | 0.0395 | 0.0142 | 4517.5 | 0.820348 | 0.0232 | False |
| MARKET_STRUCTURE | 1h | 7 | 0.0453 | -0.005 | 0.0503 | 935.0 | 0.759034 | -0.0686 | False |
| MARKET_STRUCTURE | 4h | 7 | -0.1283 | 0.0493 | -0.1776 | 621.0 | 0.191245 | 0.2903 | False |
| MARKET_STRUCTURE | 24h | 7 | 0.0157 | 0.0395 | -0.0238 | 836.0 | 0.842666 | 0.0446 | False |
| PROTOCOL | 1h | 8 | -0.4822 | -0.005 | -0.4772 | 558.0 | 0.033586 | 0.442 | True |
| PROTOCOL | 4h | 8 | -0.3465 | 0.0493 | -0.3959 | 787.0 | 0.306404 | 0.213 | False |
| PROTOCOL | 24h | 8 | -0.488 | 0.0395 | -0.5275 | 880.0 | 0.565175 | 0.12 | False |
| REGULATORY | 1h | 11 | -0.0528 | -0.005 | -0.0479 | 1305.0 | 0.776688 | 0.0509 | False |
| REGULATORY | 4h | 11 | -0.325 | 0.0493 | -0.3743 | 1096.0 | 0.25571 | 0.2029 | False |
| REGULATORY | 24h | 11 | 0.1738 | 0.0395 | 0.1343 | 1506.0 | 0.594321 | -0.0953 | False |
| SENTIMENT | 1h | 135 | -0.0 | -0.005 | 0.0049 | 17683.0 | 0.438334 | -0.0479 | False |
| SENTIMENT | 4h | 135 | -0.2719 | 0.0493 | -0.3213 | 13202.5 | 0.000425 | 0.2176 | True |
| SENTIMENT | 24h | 135 | -0.1727 | 0.0395 | -0.2122 | 15881.0 | 0.340323 | 0.0589 | False |

**Interpretation:** 2 of 21 category-window combinations showed statistically significant difference from random (p < 0.05). This suggests some event types do move prices.

**Limitations:** Short data history limits statistical power. Multiple comparison problem (no Bonferroni correction). Random baseline uses same asset pool, not matched timestamps.

---

## Experiment 2: Sentiment-Return Correlation

**Hypothesis:** More positive sentiment predicts higher subsequent returns.

**Methodology:** Bucketed events into 5 sentiment groups (very negative to very positive). Measured avg subsequent return per bucket at 1h/4h/24h. Tested Spearman rank correlation between sentiment score and 4h return.

### Results

- **spearman_correlation:** 0.0543
- **correlation_p_value:** 0.392325
- **significant:** False
- **n_observations:** 250

| Sentiment Bucket | Window | N | Avg Return (%) | Median Return (%) | Std Dev |
| --- | --- | --- | --- | --- | --- |
| Very Negative | 1h | 43 | 0.0111 | 0.1436 | 0.543 |
| Very Negative | 4h | 43 | -0.3125 | -0.305 | 1.0733 |
| Very Negative | 24h | 43 | -0.0987 | -0.7306 | 2.4153 |
| Negative | 1h | 10 | -0.0596 | -0.2206 | 0.5934 |
| Negative | 4h | 10 | -0.2927 | -0.2147 | 0.3567 |
| Negative | 24h | 10 | 1.0157 | 1.266 | 1.8665 |
| Neutral | 1h | 83 | 0.1342 | 0.1537 | 0.6212 |
| Neutral | 4h | 83 | -0.1534 | -0.305 | 1.1362 |
| Neutral | 24h | 83 | -0.1574 | -0.5171 | 2.233 |
| Positive | 1h | 22 | -0.0003 | 0.0935 | 0.7518 |
| Positive | 4h | 22 | -0.5508 | -0.6394 | 1.1445 |
| Positive | 24h | 22 | 0.043 | -0.6039 | 2.9161 |
| Very Positive | 1h | 92 | -0.1624 | -0.0368 | 0.5619 |
| Very Positive | 4h | 92 | -0.1992 | -0.2888 | 1.1343 |
| Very Positive | 24h | 92 | -0.2729 | -0.6002 | 2.9741 |

**Interpretation:** Spearman correlation between sentiment and 4h return: r=0.054 (p=0.3923). No significant linear relationship detected.

**Limitations:** VADER sentiment may not capture crypto-specific nuance. Sentiment extracted from event summary, not raw article text. Non-linear relationships not captured by Spearman.

---

## Experiment 3: Narrative Accumulation Test

**Hypothesis:** Events covered by multiple articles (narrative accumulation) predict larger price moves than isolated single-article events.

**Methodology:** Split events into multi-article clusters (article_count > 1) vs single-article events. Compared absolute move sizes using Mann-Whitney U test.

### Results

- **multi_article_events:** 35
- **solo_events:** 215

| Window | Multi-Article N | Multi-Article Avg (%) | Solo N | Solo Avg (%) | Avg |Multi| | Avg |Solo| | p-value | Significant |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1h | 35 | 0.2148 | 215 | -0.0532 | 0.699 | 0.4062 | 0.030073 | True |
| 4h | 35 | -0.0677 | 215 | -0.2659 | 0.7954 | 0.8023 | 0.385171 | False |
| 24h | 35 | 0.7098 | 215 | -0.2612 | 2.2387 | 1.9542 | 0.038376 | True |

**Interpretation:** Compared magnitude of moves between narrative-accumulated events and isolated events. Larger absolute moves from multi-article clusters would support the narrative accumulation hypothesis.

**Limitations:** Small sample of multi-article events may limit power. Clustering threshold affects what counts as 'multi-article'. Does not account for event severity differences between groups.

---

## Experiment 4: Signal Decay Analysis

**Hypothesis:** Most of the price move happens within the first hour after event detection, making the edge difficult to capture with execution latency.

**Methodology:** For events with data at all 3 windows (1h, 4h, 24h), computed what fraction of the total 24h move occurred in each time bucket: 0-1h, 1h-4h, 4h-24h.

### Results

- **avg_first_hour_fraction:** 0.4241
- **n_events_analyzed:** 248
- **capturable_after_15min:** True

| Time Bucket | Avg Fraction of 24h Move | Median Fraction | N |
| --- | --- | --- | --- |
| 0-1h | 0.4241 | 0.2515 | 248 |
| 1h-4h | 0.5708 | 0.4486 | 248 |
| 4h-24h | 0.8885 | 0.8085 | 248 |

**Interpretation:** On average, 42% of the 24h move occurs in the first hour. The move is more gradual, suggesting execution latency is less of a concern.

**Limitations:** Fractional decomposition can exceed 100% when the move reverses. Does not account for intra-hour timing (move could happen in first 5 minutes vs last 5 minutes of first hour).

---

## Experiment 5: Walk-Forward Validation

**Hypothesis:** The trading strategy maintains positive Sharpe ratio in out-of-sample periods (Sharpe >= 0.5).

**Methodology:** Expanding window walk-forward: train on N days, test on next 7 days. Sliding forward. Latency buffer: 15 minutes. Cost model: 0.30% round-trip. Regime tagged by 30-day BTC trend.

### Results

- **n_windows:** 1
- **in_sample_sharpe:** -10.255
- **out_of_sample_sharpe:** 0.2899
- **sharpe_degradation:** -10.5449
- **is_trades:** 18
- **oos_trades:** 71
- **oos_win_rate:** 0.5634
- **edge_appears_real:** False

| Window | Train Period | Test Period | Regime | IS Trades | IS Win Rate | IS Sharpe | OOS Trades | OOS Win Rate | OOS Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2026-02-20 to 2026-03-06 | 2026-03-06 to 2026-03-13 | choppy | 18 | 0.278 | -10.26 | 71 | 0.563 | 0.29 |
|  |  |  | choppy |  |  |  |  |  |  |

**Interpretation:** In-sample Sharpe: -10.26, Out-of-sample Sharpe: 0.29. OOS Sharpe < 0.5 — edge may not be reliable. Consider more data or strategy refinement.

**Limitations:** Short data window limits number of walk-forward periods. Regime tagging based on BTC only, may not reflect altcoin regimes. No optimization of signal weights between windows (pure replay).

---
