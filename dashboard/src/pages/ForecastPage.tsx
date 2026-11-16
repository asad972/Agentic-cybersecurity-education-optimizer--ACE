import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent } from 'echarts/components';
import { use as registerCharts } from 'echarts/core';
import type { EChartsOption } from 'echarts';
import { CalendarDays, Layers, MapPin, Search, TrendingUp, Zap } from 'lucide-react';
import { Chart } from '../components/Chart';
import { CountryMap } from '../components/CountryMap';
import { ErrorState, Loading } from '../components/Status';
import { useQuery } from '../hooks/useQuery';
import type { CountryAlert, CountryData, ForecastData, ForecastItem, GapData, GapItem, MapData, MapMeta } from '../services/api';
import { countLabel, formatDate, formatTime, formatTimestamp, isDay, previousMonth, yesterday } from '../services/dates';

registerCharts([BarChart, LineChart, GridComponent, LegendComponent]);

export function rankGap(items: GapItem[]) {
  return [...items].sort((a, b) => b.demand - a.demand || a.skill.localeCompare(b.skill)).slice(0, 10);
}

export function initialSkill(items: GapItem[]) {
  return rankGap(items).find(item => item.demand > 0)?.skill ?? '';
}

export function forecastBands(items: ForecastItem[]) {
  return {
    months: items.map(item => item.target_month),
    predicted: items.map(item => item.predicted),
    lower: items.map(item => item.lower),
    range: items.map(item => item.upper - item.lower),
  };
}

export function newestDriftAlerts(alerts: CountryAlert[]) {
  return alerts.filter(alert => alert.alert_type === 'drift')
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || a.subject.localeCompare(b.subject))
    .slice(0, 10);
}

function gapOption(items: GapItem[]): EChartsOption {
  return {
    animationDurationUpdate: 250,
    grid: { left: 16, right: 22, top: 36, bottom: 12, containLabel: true },
    legend: { top: 4, textStyle: { color: '#617580', fontSize: 10 } },
    tooltip: { trigger: 'axis', confine: true, renderMode: 'richText' },
    xAxis: { type: 'value', min: 0, axisLabel: { color: '#71827a' }, splitLine: { lineStyle: { color: '#edf0ee' } } },
    yAxis: { type: 'category', inverse: true, data: items.map(item => item.skill), axisLabel: { color: '#52675f', width: 145, overflow: 'truncate' } },
    series: [
      { name: 'Demand', type: 'bar', data: items.map(item => item.demand), itemStyle: { color: '#297d70' } },
      { name: 'Supply', type: 'bar', data: items.map(item => item.supply), itemStyle: { color: '#d4a94f' } },
    ],
  };
}

function forecastOption(items: ForecastItem[]): EChartsOption {
  const bands = forecastBands(items);
  return {
    animationDurationUpdate: 250,
    grid: { left: 18, right: 22, top: 36, bottom: 30, containLabel: true },
    legend: { top: 4, data: ['Forecast', '95% band'], textStyle: { color: '#617580', fontSize: 10 } },
    tooltip: { trigger: 'axis', confine: true, renderMode: 'richText' },
    xAxis: { type: 'category', data: bands.months, axisLabel: { color: '#71827a', rotate: 30 } },
    yAxis: { type: 'value', min: 0, axisLabel: { color: '#71827a' }, splitLine: { lineStyle: { color: '#edf0ee' } } },
    series: [
      { name: 'Band floor', type: 'line', stack: 'confidence', data: bands.lower, symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true },
      { name: '95% band', type: 'line', stack: 'confidence', data: bands.range, symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: '#9bcdbd', opacity: 0.38 } },
      { name: 'Forecast', type: 'line', data: bands.predicted, smooth: false, symbolSize: 6, lineStyle: { color: '#1d6f63', width: 3 }, itemStyle: { color: '#1d6f63' } },
    ],
  };
}

export function ForecastPage() {
  const meta = useQuery<MapMeta>('/map/meta');
  return <>
    <section className="page-heading">
      <span className="eyebrow">SUPPLY AND DEMAND OUTLOOK · M3</span>
      <h1>Supply, gap &amp; forecast</h1>
      <p>Compare the observed cybersecurity workforce with current Demand, then inspect the twelve-month Demand outlook and stored drift signals.</p>
    </section>
    {meta.loading ? <div className="panel forecast-state"><Loading text="Connecting to the ACE API…" /></div>
      : meta.error || !meta.data ? <div className="panel forecast-state"><ErrorState message={meta.error ?? 'The API returned no map metadata.'} retry={meta.retry} /></div>
      : <ForecastDashboard meta={meta.data} />}
  </>;
}

function ForecastDashboard({ meta }: { meta: MapMeta }) {
  const defaultDay = isDay(yesterday(), meta.date_min, meta.date_max) ? yesterday() : meta.date_max;
  const monthMax = previousMonth();
  const [country, setCountry] = useState(() => meta.countries.some(item => item.code === 'DE') ? 'DE' : meta.countries[0]?.code ?? '');
  const [day, setDay] = useState(defaultDay);
  const [month, setMonth] = useState(monthMax);
  const [scope, setScope] = useState<'EU' | 'country'>('EU');
  const [skill, setSkill] = useState('');
  const [draft, setDraft] = useState('');
  const choseInitialSkill = useRef(false);
  const gap = useQuery<GapData>('/gap', { country, month });
  const suggestions = gap.data?.items.map(item => item.skill) ?? [];

  useEffect(() => {
    if (!gap.data || choseInitialSkill.current) return;
    choseInitialSkill.current = true;
    const choice = initialSkill(gap.data.items);
    if (choice) { setSkill(choice); setDraft(choice); }
  }, [gap.data]);

  const region = scope === 'EU' ? 'EU' : country;
  const supply = useQuery<MapData>(skill ? '/map' : null, { metric: 'supply', date: day, item: skill });
  const forecast = useQuery<ForecastData>(skill ? '/forecast' : null, { region, skill });
  const countryData = useQuery<CountryData>('/map/country', { country, date: day });
  const gapRows = rankGap(gap.data?.items ?? []);
  const selectedGap = gap.data?.items.find(item => item.skill === skill);
  const selectedSupply = supply.data?.data.find(item => item.country === country)?.value;
  const firstForecast = forecast.data?.items[0];
  const alerts = newestDriftAlerts(countryData.data?.alerts ?? []);
  const countryName = (code: string) => meta.countries.find(item => item.code === code)?.name ?? code;
  const applySkill = (value: string) => { const exact = value.trim(); setDraft(exact); setSkill(exact); };

  return <>
    <section className="panel forecast-filters" aria-label="Supply, gap and forecast filters">
      <label><span><MapPin size={13} /> Country</span><select value={country} onChange={event => setCountry(event.target.value)}>{meta.countries.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
      <label><span><CalendarDays size={13} /> Supply day · UTC</span><input type="date" min={meta.date_min} max={meta.date_max} value={day} onChange={event => { if (isDay(event.target.value, meta.date_min, meta.date_max)) setDay(event.target.value); }} /></label>
      <label><span><CalendarDays size={13} /> Gap month · UTC</span><input type="month" min={meta.date_min.slice(0, 7)} max={monthMax} value={month} onChange={event => { if (/^\d{4}-\d{2}$/.test(event.target.value) && event.target.value <= monthMax) setMonth(event.target.value); }} /></label>
      <label><span><Search size={13} /> Canonical Skill</span>
        <input type="text" list="forecast-skill-names" maxLength={200} value={draft} placeholder="Exact Skill name" onChange={event => { setDraft(event.target.value); if (suggestions.includes(event.target.value)) applySkill(event.target.value); }} onBlur={() => applySkill(draft)} onKeyDown={event => { if (event.key === 'Enter') applySkill(draft); }} />
        <datalist id="forecast-skill-names">{suggestions.map(name => <option key={name} value={name} />)}</datalist>
      </label>
      <fieldset><legend><TrendingUp size={13} /> Forecast scope</legend><label><input type="radio" name="forecast-scope" checked={scope === 'EU'} onChange={() => setScope('EU')} /> EU</label><label><input type="radio" name="forecast-scope" checked={scope === 'country'} onChange={() => setScope('country')} /> {country}</label></fieldset>
    </section>

    <section className="forecast-stat-grid" aria-label="Selected Skill summary">
      <Summary label={`${country} Demand`} value={selectedGap ? countLabel(selectedGap.demand) : 'No data'} note={month} />
      <Summary label={`${country} Supply`} value={selectedSupply == null ? 'No data' : countLabel(selectedSupply)} note={`${formatDate(day)} event-day snapshot`} />
      <Summary label="Gap ratio" value={!selectedGap ? 'No data' : selectedGap.gap_ratio == null ? 'Not enough Supply data' : countLabel(selectedGap.gap_ratio)} note="Demand ÷ Supply" />
      <Summary label={`${region} forecast`} value={firstForecast?.model.replace('_', ' ') ?? 'No data'} note={firstForecast ? `Trained ${formatTimestamp(firstForecast.trained_at)}` : 'No stored forecast'} />
    </section>

    <div className="forecast-grid">
      <Panel title="Skill Supply across Europe" eyebrow="365-DAY PROFILE STOCK" pill={skill || 'No Skill selected'} className="forecast-map-panel">
        {!skill ? <Prompt text="Choose an exact Skill to load its Supply map." /> : supply.loading ? <Loading /> : supply.error ? <ErrorState message={supply.error} retry={supply.retry} /> : supply.data && <><CountryMap response={supply.data} countries={meta.countries} selected={country} onSelect={setCountry} /><p className="forecast-note">Supply is an event-day snapshot. Missing countries remain grey and are not silently carried forward or converted to zero.</p><DataTable summary="View Supply values as a table" headers={['Country', supply.data.unit]} rows={supply.data.data.map(row => [countryName(row.country), countLabel(row.value)])} /></>}
      </Panel>

      <Panel title="Demand versus Supply" eyebrow={`${countryName(country).toUpperCase()} · ${month}`} pill="TOP 10 BY DEMAND">
        {gap.loading ? <Loading /> : gap.error ? <ErrorState message={gap.error} retry={gap.retry} /> : !gapRows.length ? <Prompt text="No Skills Gap rows are stored for this country and month." /> : <><Chart option={gapOption(gapRows)} label={`Demand and Supply for the ten highest-Demand Skills in ${countryName(country)}`} className="forecast-bars" /><DataTable summary="View Demand and Supply as a table" headers={['Skill', 'Demand', 'Supply', 'Gap']} rows={gapRows.map(row => [row.skill, countLabel(row.demand), countLabel(row.supply), row.gap_ratio == null ? 'Not enough Supply data' : countLabel(row.gap_ratio)])} /></>}
      </Panel>

      <Panel title="Twelve-month Demand forecast" eyebrow={`${region} · ${skill || 'NO SKILL'}`} pill="95% BAND" className="forecast-line-panel">
        {!skill ? <Prompt text="Choose an exact Skill to load its Demand forecast." /> : forecast.loading ? <Loading /> : forecast.error ? <ErrorState message={forecast.error} retry={forecast.retry} /> : !forecast.data?.items.length ? <Prompt text="No forecast is stored for this region and Skill." /> : <><Chart option={forecastOption(forecast.data.items)} label={`Twelve-month ${skill} Demand forecast for ${region} with a 95 percent interval`} className="forecast-line" /><p className="forecast-note">The shaded range is the stored model interval. It is uncertainty, not additional Demand.</p><DataTable summary="View forecast values as a table" headers={['Month', 'Prediction', 'Lower', 'Upper', 'Model']} rows={forecast.data.items.map(row => [row.target_month, countLabel(row.predicted), countLabel(row.lower), countLabel(row.upper), row.model.replace('_', ' ')])} /></>}
      </Panel>

      <Panel title="Stored Demand drift alerts" eyebrow={`${countryName(country).toUpperCase()} · ${formatDate(day).toUpperCase()}`} pill={`${alerts.length} SHOWN`}>
        {countryData.loading ? <Loading /> : countryData.error ? <ErrorState message={countryData.error} retry={countryData.retry} /> : !alerts.length ? <Prompt text="No stored Demand drift alerts exist for this country and day." /> : <ul className="forecast-alerts">{alerts.map(alert => <li key={`${alert.created_at}|${alert.subject}`}><span className="alert-icon"><Zap size={13} /></span><div><strong>{alert.subject}</strong><time dateTime={alert.created_at}>{formatTime(alert.created_at)}</time><p>{alert.details || 'No additional details.'}</p></div><span className="score">{countLabel(alert.score)}<small>score</small></span></li>)}</ul>}
      </Panel>
    </div>
  </>;
}

function Summary({ label, value, note }: { label: string; value: string; note: string }) {
  return <article className="stat-card"><span>{label}</span><strong>{value}</strong><small>{note}</small></article>;
}

function Panel({ title, eyebrow, pill, className = '', children }: { title: string; eyebrow: string; pill: string; className?: string; children: ReactNode }) {
  return <section className={`panel forecast-panel ${className}`}><div className="panel-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div><span className="pill">{pill}</span></div>{children}</section>;
}

function Prompt({ text }: { text: string }) {
  return <div className="state-box" role="status"><Layers size={22} /><strong>Data unavailable for this selection</strong><p>{text}</p></div>;
}

function DataTable({ summary, headers, rows }: { summary: string; headers: string[]; rows: string[][] }) {
  return <details className="data-details"><summary>{summary}</summary><div className="table-scroll"><table><thead><tr>{headers.map(header => <th key={header} scope="col">{header}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row[0]}|${index}`}>{row.map((value, cell) => <td key={`${cell}|${value}`}>{value}</td>)}</tr>)}</tbody></table></div></details>;
}
