import { useCallback, useMemo, useState } from 'react';
import { CalendarDays, Layers, MapPin, Search } from 'lucide-react';
import type { CountryData, MapData, MapMeta } from '../services/api';
import { useQuery } from '../hooks/useQuery';
import { countLabel, formatDate, isDay, yesterday } from '../services/dates';
import { CountryMap } from '../components/CountryMap';
import { CountryPanel } from '../components/CountryPanel';
import { ErrorState, Loading } from '../components/Status';

export function EuropePage() {
  const meta = useQuery<MapMeta>('/map/meta');
  return <>
    <section className="page-heading">
      <span className="eyebrow">EUROPEAN SKILLS OBSERVATORY</span>
      <h1>Europe overview</h1>
      <p>Compare cybersecurity job ads, skill demand and supply, the skills gap, education programmes, hiring companies and threat observations across the EU27, the EEA, the United Kingdom and Switzerland.</p>
    </section>
    {meta.loading ? <div className="panel europe-state"><Loading text="Connecting to the ACE API…" /></div>
      : meta.error || !meta.data ? <div className="panel europe-state"><ErrorState message={meta.error ?? 'The API returned no map metadata.'} retry={meta.retry} /></div>
      : <EuropeMap meta={meta.data} />}
  </>;
}

function EuropeMap({ meta }: { meta: MapMeta }) {
  const [metric, setMetric] = useState(meta.metrics[0]?.id ?? 'job_ads');
  const [skill, setSkill] = useState('');
  const [draft, setDraft] = useState('');
  const [date, setDate] = useState(() => { const day = yesterday(); return isDay(day, meta.date_min, meta.date_max) ? day : meta.date_max; });
  const [country, setCountry] = useState(() => meta.countries.some(item => item.code === 'HU') ? 'HU' : meta.countries[0]?.code ?? '');

  // Which metrics are stored per skill comes from /map/meta, so the API keeps the only list.
  const selected = meta.metrics.find(item => item.id === metric);
  const needsSkill = selected?.needs_item ?? false;
  const label = selected?.label ?? metric;
  const query = useQuery<MapData>(needsSkill && !skill ? null : '/map', { metric, date, ...(needsSkill ? { item: skill } : {}) });
  const panel = useQuery<CountryData>(country ? '/map/country' : null, { country, date });

  // Demand, supply and gap are stored per exact taxonomy name, so the country's Top 10 is offered as suggestions.
  const suggestions = panel.data?.top_skills.map(row => row.item) ?? [];
  const showSkill = useCallback((name: string) => {
    setDraft(name);
    setSkill(name);
    setMetric(current => meta.metrics.find(item => item.id === current)?.needs_item ? current : meta.metrics.find(item => item.needs_item)?.id ?? current);
  }, [meta.metrics]);

  // Threat observations also come back for the rest of the world (for the world threat map); this page shows Europe only.
  const europe = useMemo(() => {
    if (!query.data) return undefined;
    const codes = new Set(meta.countries.map(item => item.code));
    return { ...query.data, data: query.data.data.filter(row => codes.has(row.country)) };
  }, [query.data, meta.countries]);
  const map = { ...query, data: europe };

  const reported = map.data?.data.filter(row => row.value != null) ?? [];
  const total = reported.reduce((sum, row) => sum + (row.value ?? 0), 0);
  const headline = !reported.length ? null : metric === 'gap' ? total / reported.length : total;
  const countryName = (code: string) => meta.countries.find(item => item.code === code)?.name ?? code;

  return <>
    <section className="filters panel" aria-label="Map filters">
      <label><span><Layers size={13} /> Metric</span>
        <select value={metric} onChange={event => setMetric(event.target.value)}>
          {meta.metrics.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
      <label><span><Search size={13} /> Skill</span>
        <input type="text" list="europe-skill-names" value={draft} disabled={!needsSkill} maxLength={200} placeholder={needsSkill ? 'Exact skill name' : 'Not used by this metric'}
          onChange={event => { setDraft(event.target.value); if (suggestions.includes(event.target.value)) setSkill(event.target.value); }}
          onBlur={() => setSkill(draft.trim())}
          onKeyDown={event => { if (event.key === 'Enter') setSkill(draft.trim()); }} />
        <datalist id="europe-skill-names">{suggestions.map(name => <option key={name} value={name} />)}</datalist>
        <small>{needsSkill ? 'Press Enter to apply, or pick from the country’s Top 10' : 'Used by demand, supply and skills gap'}</small>
      </label>
      <label><span><CalendarDays size={13} /> Date · UTC</span>
        <input type="date" min={meta.date_min} max={meta.date_max} value={date} onChange={event => { if (isDay(event.target.value, meta.date_min, meta.date_max)) setDate(event.target.value); }} />
      </label>
      <label><span><MapPin size={13} /> Country</span>
        <select value={country} onChange={event => setCountry(event.target.value)}>
          {meta.countries.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}
        </select>
      </label>
    </section>

    <div className="stat-grid">
      <div className="stat-card"><span>{metric === 'gap' ? 'Mean reported ratio' : 'Across reporting countries'}</span><strong>{map.loading ? '…' : countLabel(headline)}</strong><small>{map.data?.unit ?? label}</small></div>
      <div className="stat-card"><span>Country coverage</span><strong>{map.loading ? '…' : reported.length}<small> of {meta.countries.length}</small></strong><small>Countries with a stored value</small></div>
      <div className="stat-card"><span>Selected date</span><strong className="date-stat">{formatDate(date)}</strong><small>{map.data?.period ?? 'Dates use UTC'}</small></div>
    </div>

    <div className="explore-grid">
      <section className="panel map-panel" aria-label={`${label} map`}>
        <div className="panel-heading">
          <div><span className="eyebrow">THE EUROPEAN PICTURE</span><h2>{label}</h2></div>
          <span className="pill">{needsSkill ? skill || 'No skill selected' : 'Country comparison'}</span>
        </div>
        {needsSkill && !skill ? <div className="state-box" role="status"><Search size={22} /><strong>Choose a skill to map</strong><p>{label} is stored per skill. Type the exact skill name above, or click a skill in the country panel.</p></div>
          : map.loading ? <Loading />
          : map.error ? <ErrorState message={map.error} retry={map.retry} />
          : map.data && <>
            <CountryMap response={map.data} countries={meta.countries} selected={country} onSelect={setCountry} />
            <div className="map-explanation"><strong>Click a country to explore</strong><p>{map.data.note} Grey countries have no stored value for this selection; that is not the same as zero.</p></div>
            <details className="data-details">
              <summary>View the values as a table</summary>
              <div className="table-scroll"><table>
                <thead><tr><th scope="col">Country</th><th scope="col">{map.data.unit}</th></tr></thead>
                <tbody>{map.data.data.map(row => <tr key={row.country}>
                  <td><button className="text-button" onClick={() => setCountry(row.country)}>{countryName(row.country)}</button></td>
                  <td>{countLabel(row.value)}</td>
                </tr>)}</tbody>
              </table></div>
            </details>
          </>}
      </section>
      {country && <CountryPanel code={country} name={countryName(country)} date={date} panel={panel} onSkill={showSkill} />}
    </div>
  </>;
}
