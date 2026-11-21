# React dashboard — M4 shell and member workspaces

The app provides responsive navigation and member-owned dashboard pages. M4's
Europe overview (the home page) and M5's Education & workforce workspace read
live data from the FastAPI endpoints; other workspaces may still be
placeholders. It does not use mock or sample data when the API is unavailable.

## Run with Docker

`docker compose up -d --build --wait` from the repository root starts the dashboard together with the API and the rest of the stack; see [Running the project](../README.md#running-the-project). To start the dashboard on its own, stop the full stack first with `docker compose down`, because both use port 5173. Then, from the repository root:

```bash
docker compose -f dashboard/compose.yml up --build -d --wait
```

Open http://localhost:5173. Stop with `docker compose -f dashboard/compose.yml stop`.
If that port is occupied, prefix the startup command with `ACE_DASHBOARD_PORT=5174` and open port 5174.
The standalone image builds with `docker build -t ace-dashboard -f dashboard/Dockerfile dashboard`.

## Local development

Use Node.js 22+ and pnpm 11.25.0 (the version in package.json):

```bash
cd dashboard
pnpm install --frozen-lockfile
pnpm dev
```

Open the Local URL printed by Vite. Run `pnpm build` for TypeScript checking and a production build,
and `pnpm test` for the request, date, transformation, and page-behavior tests.
The Europe overview requests `/map/meta`, `/map` and `/map/country`, and opens
the `WS /live` alert feed.
The M5 page requests `/education`, `/emerging`, `/companies` and `/platforms`
from `http://localhost:8000` by default. Set `VITE_API_BASE_URL` before the
build or Vite startup to use another API origin. Start the API and Cassandra
stack separately as documented in `api/README.md`; without them, the M5 page
shows an explicit connection error instead of sample data. The Europe
overview does the same, with a retry button.

## Calling the API through `/api` (Skills demand page)

The Skills demand page calls the API on the same origin under `/api`, e.g. `/api/top10?...`. Whatever sits in front of the page forwards `/api/...` to the FastAPI service and removes the `/api` prefix, so the API serves `/top10`:

| Where | What forwards `/api` |
| --- | --- |
| `pnpm dev` / `pnpm preview` | Vite proxy to `http://127.0.0.1:8000` (`vite.config.ts`) |
| Dashboard image | nginx proxy to `ACE_API_URL` (default `http://api:8000`) |
| `dashboard/compose.yml` | nginx proxy to `http://host.docker.internal:8000`, the API from `api/compose.yml` |
| Kubernetes | The Ingress routes `/api` to the API service |

The page still loads without an API; requests to `/api` then fail and each dashboard shows a "not reachable" message. To point the image at another API (in Kubernetes use the full service name, e.g. `http://api.ace.svc.cluster.local:8000`, because nginx does not apply DNS search domains):

```powershell
docker run --rm -p 127.0.0.1:5173:80 -e ACE_API_URL=http://host.docker.internal:8000 ace-dashboard
```

## Supply, gap and forecast (M3, #31)

The M3 workspace at `#forecast` combines four independent stored-data views:

- `/map?metric=supply&date=&item=` supplies the Skill Supply choropleth for an
  exact canonical Skill on one event day.
- `/gap?country=&month=` supplies the Demand-versus-Supply bars and Skill
  suggestions for a complete month.
- `/forecast?region=&skill=` supplies the 12-month country or EU forecast and
  confidence band.
- `/map/country?country=&date=` supplies stored alerts; this page displays only
  country-level drift alerts and does not connect to `WS /live`.

The country list and Supply-date bounds come from `/map/meta`. Country defaults
to `DE`, forecast scope defaults to `EU`, the day defaults to the previous UTC
day within the advertised bounds, and the month defaults to the previous
complete UTC month. After the first Gap response, the page initially selects
the positive-Demand Skill with the largest Demand, using its canonical name to
break ties. A user's later Skill selection is preserved when other filters
change.

Panels load and fail independently and each error has its own retry action.
Missing rows, a null Gap ratio, and grey map countries mean unavailable data,
not zero. Supply is a sparse event-day snapshot and is not silently carried
forward from an earlier day. Each chart has an adjacent accessible data table,
and all filters are keyboard usable.

## Europe overview (M4, #37)

A choropleth of the EU27, the EEA, the United Kingdom and Switzerland with a
metric switch: job advertisements, skill demand, skill supply, skills gap,
education programmes, hiring companies and threat observations. Demand, supply
and gap are stored per skill, so they need an exact skill name: type it and
press Enter, or click a skill in the country panel. Only Skills can be mapped
this way for now, not Certifications. Which metrics need a skill comes from
`/map/meta`. For threat observations the API also returns countries outside
Europe (for the world threat map, #40); this page shows the Europe-map
countries only. Clicking a country (or
choosing it in the selector or the data table) opens its panel with the Top 10
skills and certifications, the latest workforce baseline and that day's drift
and threat-burst alerts. Countries with no stored value stay grey; a missing
value is never shown as zero.

Below the map, the **Live alerts** panel lists drift and threat-burst alerts
as they are raised (`WS /live`, #39), latest arrival first, up to 50. It shows
what happened since the page was opened; earlier alerts of a day are in the
country panel. The badge shows the feed's state: Live, Feed unavailable (the
API cannot reach Kafka), Reconnecting (the browser lost the API and retries
after 1 to 30 seconds), Switched off (`API_LIVE_ALERTS=false`) or Not allowed
(the page's address is missing from the API's `API_CORS_ORIGINS`; the panel
does not retry). If the page falls behind and the API skips alerts for it, a
note says so until the page is reloaded. Clicking a country name selects it on
the map.
Reusable pieces: `src/hooks/useLiveAlerts.ts` and `components/LiveAlerts.tsx`.

The map is drawn with ECharts from `public/countries.geojson` (Natural Earth,
public domain; see `public/MAP-LICENSE.txt`). Shared pieces other pages may
reuse: `src/services/api.ts` (`getJSON`), `src/hooks/useQuery.ts`,
`src/components/Chart.tsx`, `CountryMap.tsx` and `Status.tsx`.

## Threat landscape (M4, #40)

`#threats` shows threat observations from public feeds, by the country the
feeds report, worldwide:

- a world map of one day's observations (`GET /map?metric=threats`). A time
  slider covers 30 days, with previous and next buttons and a play button that
  steps through the days up to today. The date field reaches any earlier day,
  and the slider moves so that day is in its middle;
- observations per UTC hour for one country (`GET /threats/hourly`), for the
  selected day or the 7 days ending on it, with gold pins on the hours of S8
  bursts. Hours without stored observations are gaps, not zero;
- the threat types behind those observations, and the list of S8 bursts.

Click a country on the map or pick it in the Country list; until you do, the
country with the most observations on the first day shown is selected. The
map, the hourly values and the bursts can also be read as tables. Country names
come from the map outline (`useWorldCountries` in `CountryMap.tsx`). The page
lives in `pages/ThreatLandscape.tsx` and loads only when it is opened, together
with the ECharts line and bar charts, so other pages stay light.

## Teammate extension points

Each page is a separate component in `src/pages/`, registered in `src/pages.ts`.
Replace your own placeholder component with your dashboard. Preserve other members' components and register additional routes in the page registry rather than replacing App.tsx.

| Hash route | Component | Owner |
| --- | --- | --- |
| #europe | EuropePage.tsx | M4 |
| #roles | RolePage.tsx | M1 |
| #demand | DemandPage.tsx | M2 |
| #forecast | ForecastPage.tsx | M3 |
| #threats | ThreatPage.tsx | M4 |
| #education | EducationPage.tsx | M5 |

Direct hash links, browser Back/Forward and unknown-route fallback work without a server router. At narrow widths use the menu button; Escape closes the navigation. The skip link focuses the main content.

The FastAPI skeleton is delivered separately under #33. Domain endpoints and dashboard requirements remain in `docs/ARCHITECTURE.md`. The threat landscape dashboard (#40) is a later release.

## Skills demand page (M2)

`#demand` shows a Europe map of Demand for one Skill or Certification on one day, and the Top 10 Skills and Top 10 Certifications of the selected country. Click a country on the map, or use the Country list, to select it. The day defaults to the latest day with data.

It reads these endpoints (`api/ace_api/routers/demand.py`):

| Endpoint | Used for |
| --- | --- |
| `GET /demand/latest-day` | Default day |
| `GET /demand/items?kind=&date=` | Skill or Certification list, most demanded first |
| `GET /demand/map?kind=&item=&date=` | Map values |
| `GET /top10?country=&kind=&date=` | Top 10 bar charts; falls back to the latest earlier day within 30 days |

Charts use [Apache ECharts](https://echarts.apache.org) 6 (Apache-2.0 licence), loaded only when the page opens.

**Map data:** `src/demand/europe.geo.json` is made from [Natural Earth](https://www.naturalearthdata.com) 1:50m Admin 0 countries, v5.1.2, which is in the public domain. It keeps the area 25°W–45°E, 34°N–71.5°N, without Greenland. Countries in the Skills Scope (EU27, Iceland, Liechtenstein, Norway, the UK and Switzerland) have `scope: true` and their ISO 3166-1 alpha-2 code as `id`; Åland is merged into FI and Northern Cyprus into CY. Neighbouring countries are kept for orientation only, with `id` set to `~` plus their name. Overseas territories, the Canary Islands, Madeira and the Azores fall outside the area. To rebuild it, download `ne_50m_admin_0_countries.geojson` from the `v5.1.2` tag of github.com/nvkelso/natural-earth-vector into an empty folder, then run from that folder:

```powershell
$scope = "['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT','LU','MT','NL','PL','PT','RO','SK','SI','ES','SE','IS','LI','NO','GB','CH']"
docker run --rm -v "${PWD}:/geo" -w /geo node:22-alpine npx -y mapshaper@0.7.101 -i ne_50m_admin_0_countries.geojson -filter "ISO_A2_EH != 'GL'" -clip bbox=-25,34,45,71.5 -each "code = ADMIN == 'Northern Cyprus' ? 'CY' : (ISO_A2_EH == 'AX' ? 'FI' : ISO_A2_EH)" -each "scope = $scope.indexOf(code) > -1, id = scope ? code : '~' + NAME_EN" -dissolve id copy-fields=scope -simplify 20% keep-shapes -o europe.geo.json format=geojson precision=0.01
```
