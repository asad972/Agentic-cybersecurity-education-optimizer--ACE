import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2, RotateCw } from 'lucide-react';
import { api, formatCount, formatDay, todayIso, useApi, type ApiError, type Kind, type Load, type Top10 } from './api';
import { countryName, scopeCountries } from './countries';
import { DemandMap } from './DemandMap';
import { Top10Chart } from './Top10Chart';
import './demand.css';

const kindLabels: Record<Kind, { one: string; many: string }> = {
  skill: { one: 'Skill', many: 'Skills' },
  certification: { one: 'Certification', many: 'Certifications' },
};

export default function DemandDashboard() {
  const [reload, setReload] = useState(0);
  const [dateChoice, setDateChoice] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind>('skill');
  const [itemChoice, setItemChoice] = useState<string | null>(null);
  const [countryChoice, setCountryChoice] = useState<string | null>(null);
  const retry = useCallback(() => setReload(value => value + 1), []);

  const latest = useApi(signal => api.latestDay(signal), `latest:${reload}`);
  const noRecentDay = latest.status === 'error' && latest.error.kind === 'not-found';
  const date = dateChoice ?? (latest.status === 'ready' ? latest.data.date : noRecentDay ? todayIso() : null);

  const items = useApi(date ? signal => api.items(kind, date, signal) : null, `items:${kind}:${date}:${reload}`);
  const itemList = items.status === 'ready' ? items.data.items : [];
  const item = itemList.some(entry => entry.item === itemChoice) ? itemChoice : itemList[0]?.item ?? null;

  const map = useApi(date && item ? signal => api.map(kind, item, date, signal) : null, `map:${kind}:${item}:${date}:${reload}`);
  const mapValues = map.status === 'ready' ? map.data.values : [];
  const topCountry = [...mapValues].sort((a, b) => b.count - a.count).find(value => scopeCountries.includes(value.country))?.country;
  // Until a country is chosen, show the map's top country. Wait for the map instead of guessing,
  // and keep the last default while the map reloads, so the Top 10 cards don't jump or refetch.
  const mapDefault = map.status === 'ready' ? topCountry ?? 'DE'
    : items.status === 'error' || map.status === 'error' || (items.status === 'ready' && !item) ? 'DE'
    : null;
  const [lastDefault, setLastDefault] = useState<string | null>(null);
  useEffect(() => { if (mapDefault) setLastDefault(mapDefault); }, [mapDefault]);
  const country = countryChoice ?? mapDefault ?? lastDefault;
  // True when no country was chosen and the map gave none, so Germany is shown as a fallback.
  const countryFallback = !countryChoice && mapDefault === 'DE' && topCountry !== 'DE';

  const topSkills = useApi(date && country ? signal => api.top10(country, 'skill', date, signal) : null, `top:skill:${country}:${date}:${reload}`);
  const topCertifications = useApi(date && country ? signal => api.top10(country, 'certification', date, signal) : null, `top:certification:${country}:${date}:${reload}`);

  if (latest.status === 'error' && !noRecentDay) {
    return <div className="demand"><Problem error={latest.error} onRetry={retry} /></div>;
  }
  if (!date) {
    return <div className="demand"><Loading text="Finding the latest day with Demand data…" /></div>;
  }

  const labels = kindLabels[kind];
  const mapLabel = item
    ? `Map of Demand for ${item} on ${formatDay(date)}. ` + (mapValues.length
      ? [...mapValues].sort((a, b) => b.count - a.count).map(v => `${countryName(v.country)} ${formatCount(v.count)}`).join(', ') + '.'
      : 'No Demand recorded in any country.')
    : '';

  return <div className="demand">
    <form className="demand-filters" aria-label="Dashboard filters" onSubmit={event => event.preventDefault()}>
      <label>Day
        <input type="date" value={date} max={todayIso()} required onChange={event => { if (event.target.value) setDateChoice(event.target.value); }} />
      </label>
      <label>Show
        <select value={kind} onChange={event => setKind(event.target.value as Kind)}>
          <option value="skill">Skills</option>
          <option value="certification">Certifications</option>
        </select>
      </label>
      <label>{labels.one}
        <select value={item ?? ''} disabled={!itemList.length} onChange={event => setItemChoice(event.target.value)}>
          {!itemList.length && <option value="">{items.status === 'loading' ? 'Loading…' : 'None on this day'}</option>}
          {itemList.map(entry => <option key={entry.item} value={entry.item}>{entry.item} ({formatCount(entry.count)})</option>)}
        </select>
      </label>
      <label>Country
        <select value={country ?? ''} onChange={event => setCountryChoice(event.target.value)}>
          {!country && <option value="">Loading…</option>}
          {scopeCountries.map(code => <option key={code} value={code}>{countryName(code)}</option>)}
        </select>
      </label>
    </form>
    {noRecentDay && !dateChoice && <p className="demand-notice" role="status">No Demand data was found in the last 31 days. Pick a day to look further back.</p>}
    {countryFallback && <p className="demand-notice" role="status">The map has no Demand to pick a Country from, so the Top 10 shows {countryName('DE')}. Pick a Country above to change it.</p>}

    <section className="demand-card" aria-labelledby="demand-map-title">
      <header className="demand-card-header">
        <h2 id="demand-map-title">{item ? <>Demand for {item} by Country</> : <>Demand by Country</>}</h2>
        <p>Job ads mentioning the {labels.one} on {formatDay(date)}. Select a country on the map or in the Country list to see its Top 10.</p>
      </header>
      {items.status === 'error' ? <Problem error={items.error} onRetry={retry} compact />
        : items.status !== 'ready' ? <Loading text={`Loading ${labels.many}…`} />
        : !item ? <Empty text={`No Demand for any ${labels.one} was recorded on ${formatDay(date)}. Pick another day.`} />
        : map.status === 'error' ? <Problem error={map.error} onRetry={retry} compact />
        : map.status !== 'ready' ? <Loading text="Loading the map…" />
        : <>
          <DemandMap values={mapValues} item={item} selected={country ?? ''} onSelect={setCountryChoice} label={mapLabel} />
          <p className="demand-legend"><span className="swatch no-data" aria-hidden="true" />No Demand recorded <span className="swatch outside" aria-hidden="true" />Outside the Skills Scope</p>
          <details className="demand-table">
            <summary>Show the map as a table</summary>
            {mapValues.length ? <table>
              <caption>Job ads mentioning {item} on {formatDay(date)}</caption>
              <thead><tr><th scope="col">Country</th><th scope="col">Demand</th></tr></thead>
              <tbody>{[...mapValues].sort((a, b) => b.count - a.count).map(value => <tr key={value.country}>
                <th scope="row"><button type="button" className="link-button" onClick={() => setCountryChoice(value.country)}>{countryName(value.country)}</button></th>
                <td>{formatCount(value.count)}</td>
              </tr>)}</tbody>
            </table> : <p>No Demand recorded in any country.</p>}
          </details>
        </>}
    </section>

    <div className="demand-grid">
      <Top10Card kind="skill" country={country} load={topSkills} onRetry={retry} />
      <Top10Card kind="certification" country={country} load={topCertifications} onRetry={retry} />
    </div>
    <p className="data-note">Demand is the number of job ads that mention an item. Counts are stream estimates (Count-Min Sketch and Space-Saving), exact in practice. A Top 10 counts the 30 days ending on its day.</p>
  </div>;
}

function Top10Card({ kind, country, load, onRetry }: { kind: Kind; country: string | null; load: Load<Top10>; onRetry: () => void }) {
  const labels = kindLabels[kind];
  const name = country ? countryName(country) : 'the selected country';
  const id = `demand-top-${kind}`;
  let body;
  let subtitle = `Job ads that mention each ${labels.one} over the last 30 days.`;
  if (load.status === 'error') body = <Problem error={load.error} onRetry={onRetry} compact />;
  else if (load.status !== 'ready') body = <Loading text={`Loading the Top 10 ${labels.many}…`} />;
  else if (!load.data.date || !load.data.items.length) {
    subtitle = `Job ads that mention each ${labels.one} over 30 days.`;
    body = <Empty text={`No Top 10 ${labels.many} for ${name} in the 30 days up to ${formatDay(load.data.requested_date)}.`} />;
  } else {
    const { date, requested_date: requested, items } = load.data;
    subtitle = `Job ads that mention each ${labels.one} over the 30 days ending ${formatDay(date)}.`;
    body = <>
      {date !== requested && <p className="demand-notice">No Top 10 {labels.many} for {formatDay(requested)} in {name}, so the latest earlier day is shown.</p>}
      <Top10Chart items={items} label={`Top 10 ${labels.many} in ${name} over the 30 days ending ${formatDay(date)}: ` +
        items.map(entry => `${entry.rank}. ${entry.item}, ${formatCount(entry.count)}`).join('; ') + '.'} />
    </>;
  }
  return <section className="demand-card" aria-labelledby={id}>
    <header className="demand-card-header"><h2 id={id}>Top 10 {labels.many} in {name}</h2><p>{subtitle}</p></header>
    {body}
  </section>;
}

function Loading({ text }: { text: string }) {
  return <div className="demand-state" role="status"><Loader2 className="spin" size={18} aria-hidden="true" />{text}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="demand-state" role="status">{text}</div>;
}

const problems: Record<ApiError['kind'], string> = {
  unreachable: 'The ACE API is not reachable, so no data can be shown. Check that the API is running, then try again.',
  database: 'The ACE API is running but cannot reach the database (Cassandra). Try again in a moment.',
  'not-found': 'The ACE API did not find this data.',
  other: 'The ACE API returned an unexpected error.',
};

function Problem({ error, onRetry, compact }: { error: ApiError; onRetry: () => void; compact?: boolean }) {
  return <div className={'demand-problem' + (compact ? ' compact' : '')} role="alert">
    <AlertTriangle size={18} aria-hidden="true" />
    <p>{problems[error.kind]}{error.status && error.kind === 'other' ? ` (HTTP ${error.status})` : ''}</p>
    <button type="button" onClick={onRetry}><RotateCw size={14} aria-hidden="true" />Try again</button>
  </div>;
}
