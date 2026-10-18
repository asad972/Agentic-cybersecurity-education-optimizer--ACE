# Design decisions – Oct 15, 2026

**Status: agreed by all five members on Oct 15, 2026.** Every point below was accepted as written.

I compared our design with the professor's brief and with CyberSeek, and proposed the changes below. We went through every point together and all five of us agreed. The full detail is in [ARCHITECTURE.md](ARCHITECTURE.md); this page records what we decided and why.

---

## A. Bigger changes (affect what we build)

**A1. Make ACE look more like CyberSeek.** The brief says "develop something similar to CyberSeek". CyberSeek has a map where you switch between metrics, top skills and certifications, a supply/demand ratio, and a career pathway. Proposal: add a **Europe map with a metric switch**, a **Top 10 certifications** list, and a **Role explorer**, a career pathway built on the 12 European (ECSF) cybersecurity roles.
- [x] Agree  
- [ ] Disagree
- [ ] Discuss  
  
 Comment:

**A2. Use CYBERHEAD for education.** CYBERHEAD is ENISA's database of university cybersecurity programmes, not a workforce report, which is how our design treated it. Our project is an *education* optimizer, so proposal: an **education agent** collects programmes per country, and we show them next to skill demand ("country X needs cloud security but few programmes teach it"). ISC2 reports get their own **workforce agent**.
- [x] Agree  
- [ ] Disagree  
- [ ] Discuss  
  
 Comment:

**A3. Platform analysis stays an AI agent that publishes through Kafka**, like every other agent, so we have one simple rule. Its results show as a comparison table.
- [x] Agree
- [ ] Disagree
- [ ]  Discuss  
  
Comment:

**A4. Changes to who builds what.** These follow from A1–A3:
- Each agent belongs to the member who uses its data: M1 job ads and profiles; M4 threats; education (CYBERHEAD), workforce (ISC2) and platform analysis go to M5 (see A6).
- M1's dashboard becomes the **Role explorer**. "Pipeline health" becomes a few Grafana panels, built by M2 as part of monitoring.
- M4 builds the API service, the website shell and the **Europe map** home page; each member writes the API endpoints for their own dashboard.
- M2 builds one shared **sink** that writes all Kafka data into Cassandra (nobody owned this before).
- We build the first skill list together: each member adds the skills and certifications for their area, and M1 merges.
- [x] Agree 
- [ ] Disagree 
- [ ] Discuss 
  
Comment:

**A5. Make the orchestrator the "brain" of the agents.** Our design says the orchestrator starts the agents and restarts them when they fail, but Kubernetes already does that. Proposal: Kubernetes keeps the agents running, and the orchestrator decides **what to collect**. Every 6 hours it looks at which data is missing (for example "few job ads for Poland") and uses the AI to choose from a short, fixed list of actions (fetch job ads from the job API, refresh university programmes, refresh threat data, rerun the platform comparison). It sends at most 5 tasks at a time through Kafka, each with a one-sentence reason, and a panel shows **"What the orchestrator decided"**. This makes the "agentic AI" part of the brief something we can actually show. Owner M1; if time runs short, fixed rules can replace the AI.
- [x] Agree 
- [ ] Disagree 
- [ ] Discuss 
  
Comment:

**A6. A fifth member (M5).** Our team now has 5 members, as the brief expects, and each member needs their own 2 stream stages and 1 dashboard. Proposal:
- M5 takes the education (CYBERHEAD), workforce (ISC2) and platform analysis agents from M3, so M3 can focus on forecasting, the Skills Gap and the AI module.
- Two new stream stages for M5: **S9 emerging skills** lists the skills job ads ask for that are not in our skill list yet (Lossy Counting), so we know what to add next; **S10 hiring companies** counts how many different companies are hiring in each country (HyperLogLog), as a new metric on the Europe map.
- M5's dashboard is **Education and workforce**: university programmes next to Demand, workforce figures, emerging skills, hiring companies and the platform comparison. The education and platform graphs move here from M3's dashboard.
- M5 writes the Related work section of the report (similar platforms).
- S10 needs the company name in the job ads, so the dataset check also looks for that field.
- [x] Agree 
- [ ] Disagree 
- [ ] Discuss 
  
Comment:

---

## B. Naming and shared rules

**B1. Say "Demand" and "Supply"**, never "needed" and "offered". Topics, tables and folders are renamed to match.
- [x] Agree 
- [ ] Disagree  
- [ ] Discuss  
  
Comment:

**B2. Use one fixed list of skills, certifications and roles**, based on the European framework (ECSF), maintained by M1. Every skill the AI extracts must match an entry, so demand and supply can be compared.
- [x] Agree  
- [ ] Disagree 
- [ ] Discuss  
  
 Comment:

**B3. Which countries?** Skills: EU27 + EEA + UK + Switzerland. Threats: the whole world. Country codes always use the ISO two-letter standard (`GR`, `GB`).
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**B4. A coordinator** (I'm happy to take this on) settles changes to shared files and runs integration. Everyone still owns, and is graded on, their own part.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

---

## C. Technical fixes (mostly affect one person each)

These fix problems that would show wrong numbers on the maps. The owner of each stage should check them.

| # | Proposal | Owner | Agree? |
| --- | --- | --- | --- |
| C1 | Our job and profile data are old datasets that we replay. Every stage uses **the date inside the record**, not the time it arrived. | All stages | Yes |
| C2 | A day's count is published **once, when the day is over**. Late records wait 1 hour (15 min for threats), then are dropped and counted. | All stages | Yes |
| C3 | The database tables no longer use the count as part of the key (that caused duplicate rows). | M2 | Yes |
| C4 | Top 10 = **last 30 days**, saved daily so the map can scroll back. Same for certifications. | M2 (S3) | Yes |
| C5 | S4 also counts **job ads per country** for the Europe map. | M2 (S4) | Yes |
| C6 | Profiles have no posting date, so Supply = people with the skill over the **last 365 days**, counted with exponential histograms (a published algorithm, which is better for the grade). | M3 (S5) | Yes |
| C7 | Drift detection only watches skills that have been in a country's Top 10, plus EU totals. | M3 (S6) | Yes |
| C8 | The skills gap is only shown when at least **20 people** have the skill; otherwise "not enough data". | M3 | Yes |
| C9 | S7 also produces **hourly** threat counts; S8 reads those and learns what's normal **per country**. | M4 (S7, S8) | Yes |
---

## D. Shared code

If we don't plan this, all five of us will write the same code several times, each slightly differently. Proposal: one shared Python package that everyone uses.

**D1. One shared package** (`shared/python/ace/`) with three parts to start:
- **Stage base** (owner M2): handles Kafka for every stream stage (reading, format checks, dates, late data, publishing once). Each of us then writes only our algorithm.
- **Topic list** (owner M2): one file, `shared/schemas/topics.csv`, used by all code and by the Kafka setup script, so a typo can't silently break things.
- **Skill matcher** (owner M1): one place that decides "which skill is this?", so the demand and supply maps use the same names.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**D2. Ready by Oct 27**, with S4 as a working example everyone can copy.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**D3. Rules:** messages in the wrong format go to a new `errors.invalid` topic instead of being lost; every stage ships at least one test with fake records (no Kafka needed); the shared package counts as a shared file, so changes are agreed by everyone.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**D4. Postponed:** a shared database helper for the API. M4 decides around Nov 15, once two dashboards exist.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**D5. One AI module** (owner M3): S2, the education agent and the platform agent all talk to the AI through it. It remembers answers already paid for, retries when the AI is busy, stops at a **daily spending limit**, and lets tests use saved answers for free.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**D6. Which AI do we use?** The AI module supports both, switched by one setting:
- a **free open-source model** on our own computers (fits the Open Source Technologies course, but slower and less accurate), or
- a **paid online model** (better quality, costs money).

Proposal: develop with the free one, compare quality on a small hand-checked sample, then choose for the final run.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**D7. One "replay player" and fake test data** (owner M1): plays our old datasets into Kafka in date order at a chosen speed (e.g. 1 day = 5 seconds) and continues where it stopped after a restart. A small fake file (about 200 job ads and 200 profiles) is ready by **Oct 22**, so everyone can run the whole pipeline in week one.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**D8. The sink uses small code functions** (owner M2): one function per topic turns a message into rows for each table, instead of a big settings file, so it can be tested.
- [x] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

---

## E. Open items, whatever we decide

- [x] **Datasets (most urgent).** Checked on Oct 16: public LinkedIn datasets have almost no European data, so we use other online sources instead (see F1). I took over the dataset check (issue #4) and finish the open points by **Oct 17**.
- [x] **Diagrams** in `docs/images/` show the agreed design, including M5, S9 and S10.
- [ ] **Tell the professor (Oct 19)** which data sources we use (F1) and why LinkedIn is not one of them. Threat data will come from OTX and AbuseIPDB; is that fine?
- [ ] **Check before building:** the real country list and free limits of the Adzuna API, which security roles the Stack Overflow survey lists, and whether Cedefop data can be downloaded as files.

---

## F. Data sources (added Oct 16)

**F1. Use European online sources instead of LinkedIn.** The brief asks for a "Dataset: From online sources (e.g. LinkedIn job ads and professional user profiles, CYBERHEAD database, ISC2 annual reports)". LinkedIn is an example, not a requirement. I checked the public LinkedIn job-ad datasets we could find: they are almost all from the US (under 1% of rows are in our countries), LinkedIn has no public API for reading job ads, and scraping breaks its terms. Proposal:

| What | Source | Why |
| --- | --- | --- |
| Live job ads (Demand) | **Adzuna API** (several European countries), **Bundesagentur für Arbeit** job search API (Germany), **France Travail** job offers API (France) | Free, real ads with date, company and country. The orchestrator's "fetch job ads" action calls them |
| Job-ad history (Demand) | **German job postings dataset** on Hugging Face (70k ads, 2015–2026, skills already labelled with ESCO) | Gives the forecast years of history; replayed like before |
| Profiles (Supply) | **Stack Overflow Developer Survey** (yearly, free, about 80k answers), filtered to security roles | Real IT professionals with country, role and skills; each answer acts as an anonymous profile |
| Check on our Demand numbers | **Cedefop Skills-OVATE** (EU statistics from over 100 million job ads) | Official European figures to compare against |
| Education and workforce | **CYBERHEAD** and **ISC2** reports, as before | Named in the brief and already in our design |

Update Oct 18: France Travail is dropped. Its general job offers API is no longer in the public francetravail.io catalogue, so we can't subscribe to it. Adzuna covers France instead (see issue #4).

Nothing else in the pipeline changes: the agents still publish the same messages to Kafka, and the stages, tables and dashboards stay as they are.
- [ ] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

**F2. Which AI model (follows D6).** Proposal: **Qwen**, a free open-source model, run on our own machines with Ollama (one extra container in the cluster). It costs nothing, fits the Open Source Technologies course, handles German and French job ads well, and no data leaves our machines. **Claude Haiku 5.5** is the paid fallback. As agreed in D6, we compare both on about 50 hand-checked job ads (including German and French ones); if Qwen is clearly less accurate, the final run switches to Haiku with one setting. Owner M3.
- [ ] Agree 
- [ ] Disagree 
- [ ]  Discuss  
  
Comment:

---

## How the changes reach the repo

The changes sit on the `proposal/design-update` branch. I open one pull request to `master` for everyone to review, as `CONTRIBUTING.md` requires for shared files, and we merge it before the Oct 19 submission.
