import { lazy, Suspense } from 'react';
import { Loading } from '../components/Status';

// The charts load only when this page opens, so other pages stay light.
const ThreatLandscape = lazy(() => import('./ThreatLandscape'));

export function ThreatPage() {
  return <>
    <section className="page-heading">
      <span className="eyebrow">THREAT LANDSCAPE</span>
      <h1>Threat landscape</h1>
      <p>Threat observations from public feeds, worldwide and per country: a world map you can scroll back through day by day, observations per hour with the bursts S8 detected, and the threat types behind them. All times are UTC.</p>
    </section>
    <Suspense fallback={<div className="panel europe-state"><Loading text="Loading the threat landscape…" /></div>}>
      <ThreatLandscape />
    </Suspense>
  </>;
}
