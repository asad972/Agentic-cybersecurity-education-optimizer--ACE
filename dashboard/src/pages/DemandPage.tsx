import { lazy, Suspense } from 'react';

// The charts load only when this page opens, so other pages stay light.
const DemandDashboard = lazy(() => import('../demand/DemandDashboard'));

export function DemandPage() {
  return <>
    <section className="page-heading">
      <span className="eyebrow">CYBERSECURITY OBSERVATORY</span>
      <h1>Skills demand</h1>
      <p>How often job ads in each European country ask for a Skill or Certification, and the Top 10 Skills and Certifications per country.</p>
    </section>
    <Suspense fallback={<p className="data-note" role="status">Loading the dashboard…</p>}>
      <DemandDashboard />
    </Suspense>
  </>;
}
