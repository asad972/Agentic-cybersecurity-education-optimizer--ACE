# Related work: cybersecurity skills and workforce platforms

## Comparable sources and platforms

CyberSeek is a useful reference for presenting cybersecurity workforce
intelligence: it combines labor-market metrics with career pathways and
skills-development information. Its current data and methodology describe the
United States market, so ACE uses it as a product and visualization reference,
not as a source for European country values [1].

For the European education context, ENISA's European Cybersecurity Skills
Framework (ECSF) describes 12 cybersecurity role profiles, including their
tasks, skills, knowledge and competences. Using those profiles gives ACE a
shared vocabulary for relating education programmes to cybersecurity work.
Any programme-to-role mapping still needs to be reviewed against programme
evidence; the framework alone does not establish that a course teaches a role
[2].

ISC2's 2024 global workforce study surveyed 15,852 cybersecurity practitioners
and decision-makers and reports workforce size, shortages, skills gaps, hiring
needs and changing skills priorities [3]. Figures 2 and 3 publish estimates
and year-over-year changes for the global total and five regions. The separate
*2024 Cybersecurity Workforce Survey - Focus on the E.U.* adds EU-focused
findings [4]. These reports complement programme catalogues and job-ad
observations, but regional findings are not country-level counts. ACE
preserves each report's geography, metric definition and year, and does not
allocate regional aggregates among individual countries.

The 2023 ISC2 study is the latest edition with country-level workforce and
workforce-gap figures. Figures 1-B and 2-B list country estimates including
Germany, France, the United Kingdom, Spain, the Netherlands and Ireland [5].
The 2025 edition dropped the workforce-gap estimate and did not publish
country-level workforce estimates; its country percentages describe survey
respondents, not national workforces. ACE therefore uses the 2023 edition
for country comparisons and shows “not
enough data” when a country is absent rather than inferring a value from a
regional total [6]. The 2023 report's workforce gap estimates the difference
between demand over the next 12 months and available supply; this methodology
is not interchangeable with later editions.

The 2024 global report's narrative totals differ slightly from the totals
printed in Figures 2 and 3. ACE's curated source records use the figure values
consistently and retain the figure references so the chosen source values are
auditable; the regional figures also do not sum exactly to the published
global totals. ISC2's workforce gap is the estimated difference between the
staff organizations require and active cybersecurity professionals, not a
count of current job openings.

## Streaming methods used by ACE

For S9, Lossy Counting is a streaming frequency-estimation method that
summarizes frequent items with bounded memory. ACE applies it to unmapped
skill mentions, groups sketches by country and event-time day, and combines
the retained estimates and error bounds over a rolling 30-day period. The
results are taxonomy candidates, not automatically approved taxonomy entries
[7].

For S10, HyperLogLog estimates the number of distinct values using a compact
register sketch that can be merged. ACE uses one daily sketch per country and
merges the sketches for the rolling 30-day estimate. This is appropriate for
counting distinct hiring organizations without retaining company-level
membership in the aggregate; the output is an estimate, not an exact census
[8].

These methods make daily aggregation practical for a stream, but their output
has different semantics from source data: S9 counts approximate mention
frequency, while S10 estimates distinct companies. Neither metric should be
presented as an exact count.

## Relevance and limitations for ACE

Together, the sources provide complementary context rather than a single
interchangeable dataset: ECSF supplies the role vocabulary, education
programmes indicate where learning opportunities are offered, and ISC2
provides workforce-study evidence. CyberSeek informs the dashboard design but
its US-specific values are not used as European observations. Programme-to-role
labels require evidence from each programme, and workforce figures retain
their report year, geography, and source rather than being inferred for
countries where the report provides no breakdown.

The streaming outputs also retain their approximation semantics. S9 surfaces
frequent unmapped terms for human taxonomy review; it does not silently add
them to the canonical taxonomy. S10 is an estimate of distinct hiring
organizations, not a list of employers or an exact company count.

## References

1. CyberSeek. “Cybersecurity Insights.” <https://www.cyberseek.org/> (accessed
   2026-11-02).
2. European Union Agency for Cybersecurity (ENISA). “European cybersecurity
   skills framework (ECSF) role profiles.” 2022-09-19.
   <https://www.enisa.europa.eu/publications/european-cybersecurity-skills-framework-role-profiles>
3. ISC2. “Global Cybersecurity Workforce Prepares for an AI-Driven World:
   2024 ISC2 Cybersecurity Workforce Study.” 2024.
   <https://www.isc2.org/Insights/2024/10/ISC2-2024-Cybersecurity-Workforce-Study>
4. ISC2 and Digital Skills and Jobs Platform. “2024 Cybersecurity Workforce
   Survey - Focus on the E.U.” 2025.
   <https://digital-skills-jobs.europa.eu/system/files/2025-03/ISC2_2024_EU_WFS_DSJP.pdf>
5. ISC2. “ISC2 Cybersecurity Workforce Study, 2023.” 2023.
   <https://ismg-cdn.nyc3.cdn.digitaloceanspaces.com/asset_files/external/isc2cybersecurityworkforcestudy2023.pdf>
6. ISC2. “2025 ISC2 Cybersecurity Workforce Study.” 2025, Research Note and
   Appendix.
   <https://www.isc2.org/insights/2025/12/2025-ISC2-Cybersecurity-Workforce-Study>
7. G. S. Manku and R. Motwani. “Approximate Frequency Counts over Data
   Streams.” Proceedings of the 28th International Conference on Very Large
   Data Bases (VLDB), 2002, pp. 346–357.
   <https://www.vldb.org/conf/2002/S10P03.pdf>
8. P. Flajolet, É. Fusy, O. Gandouet and F. Meunier. “HyperLogLog: the
   analysis of a near-optimal cardinality estimation algorithm.” Discrete
   Mathematics & Theoretical Computer Science Proceedings, 2007.
   <https://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf>
