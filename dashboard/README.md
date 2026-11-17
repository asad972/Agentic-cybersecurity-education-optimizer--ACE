# React dashboard — M4 shell and member workspaces

The app provides responsive navigation and member-owned dashboard pages. M4's
Europe overview (the home page) and M5's Education & workforce workspace read
live data from the FastAPI endpoints; other workspaces may still be
placeholders. It does not use mock or sample data when the API is unavailable.

## Run with Docker

From the repository root:

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
and `pnpm test` for the unit tests of the request and date helpers.
The Europe overview requests `/map/meta`, `/map` and `/map/country`.
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

The map is drawn with ECharts from `public/countries.geojson` (Natural Earth,
public domain; see `public/MAP-LICENSE.txt`). Shared pieces other pages may
reuse: `src/services/api.ts` (`getJSON`), `src/hooks/useQuery.ts`,
`src/components/Chart.tsx`, `CountryMap.tsx` and `Status.tsx`.

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

The FastAPI skeleton is delivered separately under #33. Domain endpoints and dashboard requirements remain in `docs/ARCHITECTURE.md`. The threat landscape dashboard (#40) and the live alert feed (#39) are later releases.

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
