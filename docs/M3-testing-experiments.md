# Testing and experiments: Supply, drift, and forecasting

## Method

M3 used two complementary forms of evidence. First, automated tests exercise
message contracts, event-time boundaries, replay handling, checkpoint restore,
configuration validation, and failure behavior with offline fake records. These
tests require neither Kafka nor Cassandra. The stream-stage tests compare local
execution with PyFlink for ordered records, while separately marked integration
tests cover infrastructure that is not available in an ordinary unit-test run.
Second, a deterministic experiment harness evaluates the behavior of the three
main algorithms under controlled workloads. The harness imports the production
implementations rather than reimplementing them.

All experiments use seed `20261122`. The checked-in JSON records harness commit
`42678f9`, Python 3.11.18, dependency versions, configurations, and metrics
rounded to four decimal places. A Linux ARM64 Docker image recomputes the results
and rejects any difference. Stream and forecast workers use separate virtual
environments because River 0.23 requires pandas below version 3, whereas the
forecast package pins pandas 3.1.1. This preserves both production dependency
sets instead of weakening either constraint. No wall-clock timings are reported,
because they would primarily measure the test machine.

For S5, each scenario contains 100 trials of 730 daily counts. Stationary Supply
is Poisson with mean 20; seasonal Supply varies sinusoidally between Poisson
means 10 and 30; bursty Supply combines a Poisson mean of 5 with a five-percent
chance of an additional Poisson mean-100 burst. Every DGIM estimate is compared
with the exact inclusive 365-day sum. The harness checks the configured error
bound, power-of-two bucket sizes, chronological order, and the per-size bucket
limit at every observation.

For S6, 100 trials each contain 365 nonnegative daily observations with Gaussian
noise of standard deviation 3. Stationary demand has mean 20. The upward and
downward cases change between means 20 and 40 at day 180. The experiment uses
the production ADWIN configuration, including `delta=0.002` and `clock=1`, and
records false alerts, correctly directed detections, misses, and detection delay.

Forecast evaluation uses synthetic data because ACE has no versioned multi-year
Demand history. Four 84-month series represent stable annual seasonality, trend
plus seasonality, an abrupt level change, and intermittent demand with zero
months. At origins 48, 60, and 72, seasonal naive, SARIMA, LightGBM, and the
production selection policy forecast the following 12 months. Metrics aggregate
36 predictions per scenario. MAPE excludes zero actuals. Coverage is the share
of actuals inside the nominal 95-percent bounds.

## Results

| Component | Scenario | Measured result |
| --- | --- | --- |
| S5 DGIM | Stationary | relative error mean/p95/max 2.57% / 4.30% / 8.33%; max 103 buckets |
| S5 DGIM | Seasonal | relative error mean/p95/max 2.61% / 4.35% / 8.33%; max 103 buckets |
| S5 DGIM | Bursty | relative error mean/p95/max 2.57% / 4.32% / 8.33%; max 96 buckets |
| S6 ADWIN | Stationary | false-alert trials 0/100 (0.00%) |
| S6 ADWIN | Upward shift | detection 100.00%; median/p95 delay 4 / 4 days |
| S6 ADWIN | Downward shift | detection 100.00%; median/p95 delay 3 / 4 days |
| Forecast | Stable Seasonality | MAE SN/SARIMA/LightGBM/policy 2/1.9162/2.4624/1.8382; policy coverage 97.22%; selected lightgbm=1, sarima=2 |
| Forecast | Trend Seasonality | MAE SN/SARIMA/LightGBM/policy 7.2778/2.3528/4.7601/3.7954; policy coverage 52.78%; selected lightgbm=2, sarima=1 |
| Forecast | Level Change | MAE SN/SARIMA/LightGBM/policy 6.6111/6.7237/3.7421/6.2985; policy coverage 86.11%; selected lightgbm=1, sarima=2 |
| Forecast | Intermittent | MAE SN/SARIMA/LightGBM/policy 14.4722/12.3851/11.0912/11.7633; policy coverage 72.22%; selected lightgbm=2, sarima=1 |

DGIM's mean error remained near 2.6 percent and its 95th percentile remained
below the configured five-percent target in every workload. The 8.33-percent
maximum occurs for small early counts where the permitted upward integer
rounding is large relative to the exact value; every absolute error still met
`ceil(epsilon × exact)`. At most 103 buckets represented a rolling population,
and the largest observed compression was 79.66 active profiles per bucket.

ADWIN produced no stationary or pre-change false alerts in these trials. It
detected every injected change in the correct direction, with medians of four
days upward and three days downward and a 95th percentile of four days for both.
This supports the selected configuration for clear, sustained shifts, but does
not imply a zero false-positive rate on real Demand.

No forecast model dominated all scenarios. The production policy had the lowest
stable-seasonality MAE, while direct SARIMA was best for trend and direct
LightGBM was best after the level change and on intermittent demand. The
three-month internal holdout sometimes selected a worse model for the subsequent
12 months, most visibly for trend. Nominal interval coverage also varied from
52.78 percent for the trend policy forecast to 97.22 percent for stable
seasonality. LightGBM's lower and upper models are recursively forecast
quantiles, not formally calibrated prediction intervals, so their coverage
should not be interpreted as a statistical guarantee.

## Test evidence and threats to validity

The final non-Flink shared and streaming run produced 315 passes, three skipped
Cassandra LWT tests, 16 deselected Flink tests, and one expected failure. The
forecast suite produced 44 passes and two skips: live Cassandra and host-native
LightGBM, whose OpenMP library was unavailable on macOS. LightGBM nevertheless
ran successfully in the canonical Linux experiment image. The experiment image
tests produced seven passes, its complete result check matched exactly, and the
Docker PyFlink run produced two passes with 64 non-Flink tests deselected.

The experiments cover only synthetic, fixed-seed workloads and a small set of
abrupt changes. They do not represent production country distributions, gradual
drift, reporting gaps, or adversarial bursts. Forecast evaluation has only three
rolling origins per scenario and cannot establish real-world generalization.
There is no production-scale latency or resource benchmark, and the bucket count
is an algorithmic memory proxy rather than process memory. These results should
therefore be read as reproducible checks of implementation behavior, accuracy,
and bounded state—not as claims about future European labor-market accuracy.

## References

1. M. Datar, A. Gionis, P. Indyk, and R. Motwani, “Maintaining Stream
   Statistics over Sliding Windows,” *SIAM Journal on Computing*, vol. 31,
   no. 6, pp. 1794–1813, 2002. <https://doi.org/10.1137/S0097539701398363>
2. A. Bifet and R. Gavaldà, “Learning from Time-Changing Data with Adaptive
   Windowing,” *Proceedings of the Seventh SIAM International Conference on
   Data Mining*, pp. 443–448, 2007.
   <https://doi.org/10.1137/1.9781611972771.42>
3. G. E. P. Box, G. M. Jenkins, G. C. Reinsel, and G. M. Ljung, *Time Series
   Analysis: Forecasting and Control*, 5th ed. Wiley, 2015.
   <https://www.wiley.com/en-us/-p-9781118675021>
4. G. Ke, Q. Meng, T. Finley, T. Wang, W. Chen, W. Ma, Q. Ye, and T.-Y. Liu,
   “LightGBM: A Highly Efficient Gradient Boosting Decision Tree,” *Advances in
   Neural Information Processing Systems 30*, 2017.
   <https://proceedings.neurips.cc/paper/2017/hash/6449f44a102fde848669bdd9eb6b76fa-Abstract.html>
