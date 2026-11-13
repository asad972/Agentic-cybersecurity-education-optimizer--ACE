# S3 Top 10 Skills and Certifications (Space-Saving)

`skills.demand` → `agg.top10_demand`. Owner: M2.

Each day, for every Country, S3 publishes the Top 10 Skills and the Top 10 Certifications asked for in job ads over the last 30 days. The sink writes them to the `top10` table, which the Country panel and the Skills demand dashboard read.

## How it works

- **Daily summaries.** For each (Country, day) window the stage keeps two [Space-Saving](space_saving.py) summaries, one for Skills and one for Certifications. A job ad counts once for each Skill or Certification it lists, even if it lists one twice, because Demand counts job ads.
- **30-day merge.** When a day closes (end of day plus the 1-hour grace period), its two summaries go into the Country's memory, which holds at most the last 30 days; older days are dropped. The 30 daily summaries are merged into one per kind, and the stage publishes two messages for that Country and day (`kind` `skill` and `certification`), each with up to 10 items ranked 1 to 10. A day with no Certifications still gets a (possibly empty) Certification list.
- **Capacity.** k = 100 counters per summary. A count is at most N/k too high (N = mentions of that kind in the 30 days, so 1%), every item with more than 1% of the mentions is sure to be kept in the summary, and two items can only swap places when their counts are less than 1% apart. While a Country has no more than 100 different entries of a kind in a day, the Top 10 counts are exact.
- **Merge.** Counts and errors of the same item are added; an item missing from one summary gets that summary's smallest count (0 while it still has free counters) as both count and error, because it may have occurred that often there. The 100 highest counts are kept. Every count stays an upper bound, and the N/k error bound holds for the merged stream.
- **Ties** are broken by name (A to Z), both in the ranking and when a counter is taken over, so a replay gives the same Top 10.
- `count` in the output is the Space-Saving count (an upper bound of the true count; exact in the usual case above).

A day with no job ads in a Country has no window, so it gets no Top 10 that day; the API can show the latest earlier day.

## Run the tests

```powershell
python -m pytest streaming/s3_top10 -m "not flink"   # fast, plain Python
python -m pytest streaming/s3_top10 -m flink         # same records on a local Flink (needs Java)
```

## References

1. Metwally, A., Agrawal, D., & El Abbadi, A. (2005). Efficient computation of frequent and top-k elements in data streams. In *Database Theory – ICDT 2005*, LNCS 3363, pp. 398–412. Springer. https://doi.org/10.1007/978-3-540-30570-5_27. The original Space-Saving algorithm (older than 10 years, cited as the source).
2. Agarwal, P. K., Cormode, G., Huang, Z., Phillips, J. M., Wei, Z., & Yi, K. (2013). Mergeable summaries. *ACM Transactions on Database Systems*, 38(4), Article 26. https://doi.org/10.1145/2500128. Why daily summaries can be merged with the same error bound (older than 10 years, cited as the source).
3. Cormode, G., & Yi, K. (2020). *Small Summaries for Big Data*. Cambridge University Press. https://doi.org/10.1017/9781108769938. Textbook treatment of Space-Saving and of merging summaries.
4. Cafaro, M., Pulimeno, M., & Tempesta, P. (2016). A parallel space saving algorithm for frequent items and the Hurwitz zeta distribution. *Information Sciences*, 329, 1–19. https://doi.org/10.1016/j.ins.2015.09.003. Merging Space-Saving summaries with the other summary's minimum for missing items.
