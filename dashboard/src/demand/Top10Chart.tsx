import { useMemo } from 'react';
import { Chart, type ChartOption } from './Chart';
import { escapeHtml, formatCount, type RankedItem } from './api';

const BAR = '#2f705d';

export function Top10Chart({ items, label }: { items: RankedItem[]; label: string }) {
  const option = useMemo<ChartOption>(() => {
    // Rank 1 at the top.
    const rows = [...items].sort((a, b) => b.rank - a.rank);
    return {
      animation: false,
      grid: { left: 8, right: 48, top: 8, bottom: 8 },
      tooltip: {
        trigger: 'item',
        formatter: (point: { name: string; value: number; dataIndex: number }) =>
          `#${rows[point.dataIndex].rank} ${escapeHtml(point.name)}<br/>${formatCount(point.value)} job ads`,
      },
      xAxis: { type: 'value', show: false },
      yAxis: {
        type: 'category', data: rows.map(row => row.item),
        axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe5e3' } },
        axisLabel: { color: '#3d5058', fontSize: 11, width: 150, overflow: 'truncate' },
      },
      series: [{
        type: 'bar', data: rows.map(row => row.count), barMaxWidth: 18,
        itemStyle: { color: BAR, borderRadius: [0, 4, 4, 0] },
        emphasis: { itemStyle: { color: '#1a4a3e' } },
        label: { show: true, position: 'right', color: '#3d5058', fontSize: 11, formatter: (p: { value: number }) => formatCount(p.value) },
      }],
    };
  }, [items]);
  return <Chart option={option} label={label} height={Math.max(160, items.length * 32 + 24)} />;
}
