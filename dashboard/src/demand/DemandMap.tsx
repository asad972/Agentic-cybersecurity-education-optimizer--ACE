import { useCallback, useMemo } from 'react';
import { Chart, type ChartClick, type ChartOption } from './Chart';
import { countryName, countryNames } from './countries';
import { escapeHtml, formatCount } from './api';
import europe from './europe.geo.json';

// One hue, light to dark, in the shell's green: more Demand is darker.
const RAMP = ['#e3efe9', '#b6d5c7', '#83b6a1', '#4f937c', '#2f705d', '#1a4a3e'];
const NO_DATA = '#f1f3f2';
const OUTSIDE_SCOPE = '#fafbfa';

type Props = {
  values: { country: string; count: number }[];
  item: string;
  selected: string;
  onSelect: (country: string) => void;
  label: string;
};

export function DemandMap({ values, item, selected, onSelect, label }: Props) {
  const option = useMemo<ChartOption>(() => {
    const counts = new Map(values.filter(v => v.country in countryNames).map(v => [v.country, v.count]));
    const max = Math.max(1, ...counts.values());
    const data = europe.features.map(feature => {
      const id = feature.properties.id;
      if (!feature.properties.scope) {
        return { name: id, value: null, itemStyle: { areaColor: OUTSIDE_SCOPE, borderColor: '#ffffff' },
          emphasis: { disabled: true }, select: { disabled: true }, tooltip: { show: false } };
      }
      const count = counts.get(id);
      const border = id === selected ? { borderColor: '#142e30', borderWidth: 2 } : {};
      return count === undefined
        ? { name: id, value: null, itemStyle: { areaColor: NO_DATA, ...border } }
        : { name: id, value: count, itemStyle: border };
    });
    return {
      animation: false,
      tooltip: {
        trigger: 'item',
        formatter: (point: { name: string; value: unknown }) => {
          const value = typeof point.value === 'number' && !Number.isNaN(point.value)
            ? `${formatCount(point.value)} job ads mention ${escapeHtml(item)}`
            : 'No Demand recorded';
          return `<strong>${escapeHtml(countryName(point.name))}</strong><br/>${value}`;
        },
      },
      visualMap: {
        type: 'continuous', min: 0, max, calculable: false, orient: 'horizontal',
        left: 8, bottom: 4, itemWidth: 10, itemHeight: 140, text: [formatCount(max), '0'],
        textStyle: { color: '#5b6b70', fontSize: 11 }, inRange: { color: RAMP },
      },
      series: [{
        type: 'map', map: 'ace-europe', nameProperty: 'id', roam: false, selectedMode: false,
        aspectScale: 0.68, top: 8, bottom: 8,
        itemStyle: { areaColor: NO_DATA, borderColor: '#ffffff', borderWidth: 0.7 },
        emphasis: { label: { show: false }, itemStyle: { areaColor: '#d2a854', borderColor: '#142e30', borderWidth: 1.5 } },
        label: { show: false },
        data,
      }],
    };
  }, [values, item, selected]);

  const click = useCallback((event: ChartClick) => {
    if (event.name && event.name in countryNames) onSelect(event.name);
  }, [onSelect]);

  return <Chart option={option} label={label} height={460} onClick={click} />;
}
