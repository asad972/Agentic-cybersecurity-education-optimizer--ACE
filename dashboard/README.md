# React app shell — M4 / #34

This release provides responsive navigation and one placeholder page per workspace. It runs independently of FastAPI, Cassandra and Kafka. No observations, maps, charts or live alerts are shipped in this skeleton.

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
The shell intentionally makes no API requests, so it remains usable before the API release is merged.
Add the agreed API proxy/client when the first data-backed page is introduced.

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
