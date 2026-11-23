// The threat landscape page (#40), loaded only when the page opens (see ThreatPage.tsx).
import { useEffect, useMemo, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight, Clock, MapPin, Pause, Play, Zap } from 'lucide-react';
import { use } from 'echarts/core';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, MarkPointComponent } from 'echarts/components';
import type { EChartsOption } from 'echarts';
import type { HourlyThreats, MapData, MapMeta } from '../services/api';
import { useQuery } from '../hooks/useQuery';
import { countLabel, formatDate, formatTime, isDay, yesterday } from '../services/dates';
import { addDays, burstSlots, daysBetween, hourSlots, sliderEnd, withCodes } from '../services/threats';
import { CountryMap, useWorldCountries } from '../components/CountryMap';
import { Chart } from '../components/Chart';
import { ErrorState, Loading } from '../components/Status';

// The line and bar charts need more of ECharts than the maps; registered here so other pages stay light.
use([LineChart, BarChart, GridComponent, MarkPointComponent]);

const WINDOW = 30;      // days the time slider covers
const PLAY_MS = 1500;   // pause between days while playing
const TYPES_SHOWN = 10;
const SPANS: Record<string, { days: number; label: string }> = {
  day: { days: 1, label: 'Selected day' },
  week: { days: 7, label: '7 days' },
};
const typeLabel = (type: string) => type.replaceAll('_', ' ');
const shortDay = (timestamp: string) => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(timestamp));

export default function ThreatLandscape() {
  const meta = useQuery<MapMeta>('/map/meta');
  return meta.loading ? <div className="panel europe-state"><Loading text="Connecting to the ACE API…" /></div>
    : meta.error || !meta.data ? <div className="panel europe-state"><ErrorState message={meta.error ?? 'The API returned no map metadata.'} retry={meta.retry} /></div>
    : <Threats meta={meta.data} />;
}

function Threats({ meta }: { meta: MapMeta }) {
  const today = meta.date_max;
  const [date, setDate] = useState(() => { const day = yesterday(); return isDay(day, meta.date_min, today) ? day : today; });
  const [end, setEnd] = useState(today);
  const [playing, setPlaying] = useState(false);
  const [span, setSpan] = useState('day');
  const [country, setCountry] = useState('');
  const world = useWorldCountries();

  const choose = (day: string, keepPlaying = false) => {
    if (!isDay(day, meta.date_min, today)) return;
    setDate(day);
    setEnd(current => sliderEnd(current, day, today, WINDOW));
    if (!keepPlaying) setPlaying(false);
  };

  // Playback steps forward one day at a time and stops at today. The effect runs after every render,
  // so the next step waits until the day's data has been drawn rather than racing a slow API.
  useEffect(() => {
    if (!playing) return;
    if (date >= today) { setPlaying(false); return; }
    const timer = setTimeout(() => choose(addDays(date, 1), true), PLAY_MS);
    return () => clearTimeout(timer);
  });

  const map = useQuery<MapData>('/map', { metric: 'threats', date });
  const reported = useMemo(() => (map.data?.data ?? []).filter(row => row.value != null).sort((a, b) => (b.value ?? 0) - (a.value ?? 0)), [map.data]);
  const countries = useMemo(() => withCodes(world, reported.map(row => row.country)), [world, reported]);
  const countryName = (code: string) => countries.find(item => item.code === code)?.name ?? code;

  // Until a country is chosen, the one with the most observations on the first day shown is selected.
  useEffect(() => { if (!country && (map.data || map.error)) setCountry(reported[0]?.country ?? 'HU'); }, [country, map.data, map.error, reported]);

  const days = SPANS[span].days;
  const first = [addDays(date, -(days - 1)), meta.date_min].sort()[1];
  const period = first === date ? formatDate(date) : `${formatDate(first)} – ${formatDate(date)}`;
  const hourly = useQuery<HourlyThreats>(country ? '/threats/hourly' : null, { country, from: first, to: date });
  const answer = hourly.data;
  const slots = useMemo(() => answer ? hourSlots(answer) : [], [answer]);
  const marked = useMemo(() => answer ? burstSlots(answer, slots) : [], [answer, slots]);
  const types = useMemo(() => Object.entries(answer?.by_type ?? {}).slice(0, TYPES_SHOWN), [answer]);

  const line = useMemo<EChartsOption>(() => {
    const labels = slots.map(slot => days === 1 ? slot.hour.slice(11, 16) : `${shortDay(slot.hour)} ${slot.hour.slice(11, 16)}`);
    return {
      color: ['#2b6f5c'], animation: false,
      grid: { left: 44, right: 18, top: 30, bottom: 30 },
      tooltip: { trigger: 'axis', confine: true, renderMode: 'richText', formatter: (raw: unknown) => {
        const point = (raw as { dataIndex: number }[])[0];
        const total = slots[point.dataIndex]?.total;
        return `${labels[point.dataIndex]} UTC\n${total == null ? 'No stored observations' : `${countLabel(total)} observations`}${marked.includes(point.dataIndex) ? '\nBurst detected by S8' : ''}`;
      } },
      xAxis: { type: 'category', data: labels, boundaryGap: false, axisTick: { show: false }, axisLine: { lineStyle: { color: '#d7e1e2' } },
        axisLabel: { color: '#78868b', fontSize: 10, interval: days === 1 ? 2 : 23, formatter: (label: string) => days === 1 ? label : label.slice(0, -6) } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#78868b', fontSize: 10 }, splitLine: { lineStyle: { color: '#eef2f3' } } },
      series: [{
        type: 'line', name: 'Observations', data: slots.map(slot => slot.total), connectNulls: false,
        // Symbols keep an hour visible when the hours around it have no stored observations.
        showSymbol: true, symbolSize: days === 1 ? 6 : 3, lineStyle: { width: 2 }, areaStyle: { color: 'rgba(43,111,92,.08)' },
        markPoint: { symbol: 'pin', symbolSize: 30, itemStyle: { color: '#d49732' }, label: { formatter: '!', color: '#fff', fontWeight: 'bold' },
          data: marked.map(index => ({ name: 'Burst', coord: [labels[index], slots[index].total!] })) },
      }],
    };
  }, [slots, marked, days]);

  const bars = useMemo<EChartsOption>(() => ({
    color: ['#63ad9d'], animation: false,
    grid: { left: 8, right: 44, top: 8, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, confine: true, renderMode: 'richText' },
    xAxis: { type: 'value', show: false },
    yAxis: { type: 'category', inverse: true, data: types.map(([type]) => typeLabel(type)), axisTick: { show: false }, axisLine: { show: false },
      axisLabel: { color: '#52656f', fontSize: 10, width: 110, overflow: 'truncate' } },
    series: [{ type: 'bar', name: 'Observations', data: types.map(([, count]) => count), barMaxWidth: 18, itemStyle: { borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', color: '#52656f', fontSize: 10, formatter: (raw: unknown) => countLabel((raw as { value: number }).value) } }],
  }), [types]);

  const start = [addDays(end, -(WINDOW - 1)), meta.date_min].sort()[1];
  // No stored observations anywhere is "No data", never 0.
  const worldTotal = reported.length ? reported.reduce((sum, row) => sum + (row.value ?? 0), 0) : null;
  const name = countryName(country);
  const hoursWithData = slots.filter(slot => slot.total != null).length;

  return <>
    <section className="filters threat-filters panel" aria-label="Threat filters">
      <label><span><CalendarDays size={13} /> Date · UTC</span>
        <input type="date" min={meta.date_min} max={today} value={date} onChange={event => choose(event.target.value)} />
      </label>
      <label><span><MapPin size={13} /> Country</span>
        <select value={country} onChange={event => setCountry(event.target.value)}>
          {!country && <option value="">Loading…</option>}
          {countries.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}
        </select>
        <small>Or click a country on the map</small>
      </label>
      <label><span><Clock size={13} /> Hourly chart</span>
        <select value={span} onChange={event => setSpan(event.target.value)}>
          {Object.entries(SPANS).map(([id, item]) => <option key={id} value={id}>{item.label}</option>)}
        </select>
        <small>Ending on the selected date</small>
      </label>
    </section>

    <div className="stat-grid">
      <div className="stat-card"><span>World observations · {formatDate(date)}</span><strong>{map.loading ? '…' : map.error ? '—' : countLabel(worldTotal)}</strong><small>{reported.length} countries with stored observations</small></div>
      <div className="stat-card"><span>{name || 'Country'} · {days === 1 ? 'selected day' : '7 days'}</span><strong>{hourly.loading ? '…' : hourly.error ? '—' : countLabel(answer?.hours.length ? answer.total : null)}</strong><small>{hourly.loading || hourly.error ? 'Hourly observations' : `${hoursWithData} of ${slots.length} hours with stored observations`}</small></div>
      <div className="stat-card"><span>Bursts detected by S8</span><strong>{hourly.loading ? '…' : hourly.error ? '—' : answer?.bursts.length ?? 0}</strong><small>{name ? `${name} · ${period}` : period}</small></div>
    </div>

    <section className="panel threat-map-panel" aria-label="World threat map">
      <div className="panel-heading">
        <div><span className="eyebrow">WORLD THREAT MAP</span><h2>{formatDate(date)}</h2></div>
        <span className="pill">Threat observations</span>
      </div>
      {map.loading ? <Loading /> : map.error ? <ErrorState message={map.error} retry={map.retry} />
        : map.data && <CountryMap response={map.data} countries={countries} selected={country} onSelect={setCountry} world />}
      <div className="timeline" role="group" aria-label="Scroll back through days">
        <button className="icon-button" aria-label={playing ? 'Pause' : 'Play the days up to today'} title={playing ? 'Pause' : 'Play the days up to today'}
          onClick={() => { if (playing) { setPlaying(false); return; } if (date >= today) choose(start); setPlaying(true); }}>{playing ? <Pause size={16} /> : <Play size={16} />}</button>
        <button className="icon-button" aria-label="Previous day" disabled={date <= meta.date_min} onClick={() => choose(addDays(date, -1))}><ChevronLeft size={16} /></button>
        <div className="timeline-range">
          <input type="range" aria-label="Day shown on the map" min={0} max={daysBetween(start, end)} value={daysBetween(start, date)} aria-valuetext={formatDate(date)}
            onChange={event => choose(addDays(start, Number(event.target.value)))} />
          <div><span>{formatDate(start)}</span><strong>{formatDate(date)}</strong><span>{formatDate(end)}</span></div>
        </div>
        <button className="icon-button" aria-label="Next day" disabled={date >= today} onClick={() => choose(addDays(date, 1))}><ChevronRight size={16} /></button>
      </div>
      <div className="map-explanation"><strong>Click a country for its hourly activity</strong><p>{map.data?.note ?? ''} Grey countries have no stored observations for this day; that is not the same as zero. The slider covers {WINDOW} days; pick any earlier day in the date field.</p></div>
      {map.data && <details className="data-details">
        <summary>View the map as a table</summary>
        {reported.length ? <div className="table-scroll"><table>
          <thead><tr><th scope="col">Country</th><th scope="col">Observations</th></tr></thead>
          <tbody>{reported.map(row => <tr key={row.country}>
            <td><button className="text-button" onClick={() => setCountry(row.country)}>{countryName(row.country)}</button></td>
            <td>{countLabel(row.value)}</td>
          </tr>)}</tbody>
        </table></div> : <p className="tiny-note">No country has stored observations for this day.</p>}
      </details>}
    </section>

    <div className="chart-grid">
      <section className="panel" aria-label={`Observations per hour in ${name}`}>
        <div className="panel-heading"><div><span className="eyebrow">{name.toUpperCase()} · {period.toUpperCase()}</span><h2>Observations per hour</h2></div><span className="chart-legend"><i /> Observations <b /> Burst</span></div>
        {!country || hourly.loading ? <Loading /> : hourly.error ? <ErrorState message={hourly.error} retry={hourly.retry} />
          : !answer?.hours.length ? <p className="empty-chart">No observations are stored for {name} on {period}.</p>
          : <><Chart option={line} className="threat-line" label={`Observations per UTC hour in ${name}, ${period}, with ${marked.length} burst markers. The hourly table below has the same values.`} />
            <p className="chart-caption">UTC hours · gaps are hours without stored observations · gold pins mark the hours of S8 bursts</p></>}
      </section>
      <section className="panel" aria-label={`Threat types in ${name}`}>
        <div className="panel-heading"><div><span className="eyebrow">{name.toUpperCase()} · {period.toUpperCase()}</span><h2>Threat types</h2></div></div>
        {!country || hourly.loading ? <Loading /> : hourly.error ? <ErrorState message={hourly.error} retry={hourly.retry} />
          : !types.length ? <p className="empty-chart">No threat types to show for this selection.</p>
          : <><Chart option={bars} className="threat-types" label={`Threat types in ${name}, ${period}: ${types.map(([type, count]) => `${typeLabel(type)} ${countLabel(count)}`).join(', ')}.`} />
            <p className="chart-caption">{Object.keys(answer?.by_type ?? {}).length > TYPES_SHOWN ? `The ${TYPES_SHOWN} most observed types · ` : ''}totals over the hours shown</p></>}
      </section>
    </div>

    <section className="panel bursts-panel" aria-label="Bursts detected by S8">
      <div className="panel-heading"><div><span className="eyebrow">{name.toUpperCase()} · {period.toUpperCase()}</span><h2>Bursts detected by S8</h2></div><span className="pill">{answer?.bursts.length ?? 0}</span></div>
      <div className="live-body">
        {!country || hourly.loading ? <Loading /> : hourly.error ? <ErrorState message={hourly.error} retry={hourly.retry} />
          : !answer?.bursts.length ? <p className="empty-inline">S8 detected no bursts for {name} on {period}.</p>
          : <ul className="alert-list">{answer.bursts.map(burst => <li key={`${burst.created_at}|${burst.threat_type}`}>
            <span className="alert-icon"><Zap size={13} /></span>
            <div><strong>{typeLabel(burst.threat_type)}</strong><time dateTime={burst.created_at}>{days === 1 ? '' : `${formatDate(burst.created_at.slice(0, 10))}, `}{formatTime(burst.created_at)}</time></div>
            <span className="score">{countLabel(burst.score)}<small>score</small></span>
          </li>)}</ul>}
        {answer?.bursts_truncated && <p className="tiny-note">A day had more alerts than one request reads; the oldest are not listed.</p>}
        <p className="tiny-note">Anomaly scores are model signals, not probabilities. The time is the UTC hour the burst affected.</p>
      </div>
      {answer && answer.hours.length > 0 && <details className="data-details">
        <summary>View the hourly values as a table</summary>
        <div className="table-scroll"><table>
          <thead><tr><th scope="col">Hour (UTC)</th><th scope="col">Observations</th><th scope="col">By threat type</th></tr></thead>
          <tbody>{answer.hours.map(hour => <tr key={hour.hour}>
            <td>{days === 1 ? '' : `${formatDate(hour.hour.slice(0, 10))}, `}{formatTime(hour.hour)}</td>
            <td>{countLabel(hour.total)}</td>
            <td>{Object.entries(hour.counts).map(([type, count]) => `${typeLabel(type)} ${countLabel(count)}`).join(', ')}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="tiny-note">{answer.note}</p>
      </details>}
    </section>
  </>;
}
