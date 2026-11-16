import { useEffect, useMemo, useState } from 'react';
import { registerMap } from 'echarts/core';
import type { EChartsOption } from 'echarts';
import type { Country, MapData } from '../services/api';
import { countLabel } from '../services/dates';
import { Chart } from './Chart';
import { ErrorState, Loading } from './Status';
type MapGeoJSON = Parameters<typeof registerMap>[1];
let boundaryPromise: Promise<MapGeoJSON> | undefined;
function boundaries() {
  if (!boundaryPromise) boundaryPromise = fetch('/countries.geojson').then(response => { if (!response.ok) throw new Error('Country boundaries could not be loaded.'); return response.json(); }).catch(error => { boundaryPromise = undefined; throw error; });
  return boundaryPromise;
}
export function CountryMap({ response, countries, selected, onSelect, world = false }: { response: MapData; countries: Country[]; selected: string; onSelect: (code: string) => void; world?: boolean }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setError('');
    boundaries().then(data => { registerMap('ace-world', data); if (active) setReady(true); }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [revision]);
  const option = useMemo<EChartsOption>(() => {
    const values = response.data.flatMap(row => row.value == null ? [] : [row.value]);
    const min = Math.min(0, ...values);
    const max = Math.max(1, ...values);
    return {
      animationDurationUpdate: 250,
      tooltip: { trigger: 'item', confine: true, renderMode: 'richText', formatter: (raw: unknown) => { const row = raw as { name: string; value?: number }; const name = countries.find(c => c.code === row.name)?.name || row.name; return `${name}\n${countLabel(row.value)}${Number.isFinite(row.value) ? ` ${response.unit}` : ''}`; } },
      visualMap: world ? { type: 'piecewise', orient: 'horizontal', bottom: 8, left: 'center', itemWidth: 12, itemHeight: 9, textStyle: { color: '#617580', fontSize: 10 }, pieces: [{ value: 0, label: '0', color: '#d5eee5' }, { min: 1, max: 249, label: '1–249', color: '#a4d7c7' }, { min: 250, max: 999, label: '250–999', color: '#51a997' }, { min: 1000, max: 4999, label: '1k–4.9k', color: '#238477' }, { min: 5000, label: '5k+', color: '#0c4f50' }] } : { min, max, calculable: false, orient: 'horizontal', left: 'center', bottom: 8, itemHeight: 150, itemWidth: 8, text: [countLabel(max), countLabel(min)], textStyle: { color: '#617580', fontSize: 10 }, inRange: { color: ['#d5eee5', '#75c0ab', '#258d7a', '#0c4f50'] } },
      series: [{ type: 'map', map: 'ace-world', nameProperty: 'code', roam: true, scaleLimit: { min: 1, max: 7 }, ...(world ? { boundingCoords: [[-180, 84], [180, -58]] } : { boundingCoords: [[-24, 72], [44, 33]] }), top: 10, bottom: 45, left: 5, right: 5, selectedMode: 'single', itemStyle: { areaColor: '#e2e8e9', borderColor: '#fff', borderWidth: .7 }, emphasis: { label: { show: false }, itemStyle: { areaColor: '#e6b95b', borderColor: '#b78722', borderWidth: 1 } }, select: { label: { show: false }, itemStyle: { borderColor: '#c5881d', borderWidth: 2, areaColor: '#e8c16e' } }, data: response.data.map(row => ({ name: row.country, value: row.value ?? undefined, selected: row.country === selected })) }],
    };
  }, [response, countries, selected, world]);
  if (error) return <ErrorState message={error} retry={() => setRevision(x => x + 1)}/>;
  if (!ready) return <Loading text="Loading country boundaries…"/>;
  return <><Chart option={option} label={`${world ? 'World' : 'Europe'} map showing ${response.metric}. Use the country selector or data table for keyboard access.`} className={world ? 'world-map' : 'europe-map'} onCountry={code => { if (countries.some(c => c.code === code)) onSelect(code); }}/><div className="map-foot"><span><i className="no-data-swatch"/> No data</span><span>Scroll to zoom · drag to explore</span><a href="https://www.naturalearthdata.com/about/terms-of-use/" target="_blank" rel="noreferrer">Natural Earth</a></div></>;
}
