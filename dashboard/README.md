# React dashboard — M4 shell and member workspaces

The app provides responsive navigation and member-owned dashboard pages. M5's
Education & workforce workspace reads live data from the FastAPI endpoints;
other workspaces may still be placeholders. It does not use mock or sample
data when the API is unavailable.

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

Open the Local URL printed by Vite. Run `pnpm build` for TypeScript checking and a production build.
The M5 page requests `/education`, `/emerging`, `/companies` and `/platforms`
from `http://localhost:8000` by default. Set `VITE_API_BASE_URL` before the
build or Vite startup to use another API origin. Start the API and Cassandra
stack separately as documented in `api/README.md`; without them, the M5 page
shows an explicit connection error instead of sample data.

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

The FastAPI skeleton is delivered separately under #33. Domain endpoints and dashboard requirements remain in `docs/ARCHITECTURE.md`; this release does not claim the later Europe/threat dashboard issues complete.
