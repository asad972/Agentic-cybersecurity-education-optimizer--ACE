import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { BarChart, MapChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, VisualMapComponent } from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';
import europe from './europe.geo.json';

// Only the ECharts parts this dashboard uses, so the bundle stays small.
echarts.use([BarChart, MapChart, GridComponent, TooltipComponent, VisualMapComponent, SVGRenderer]);
echarts.registerMap('ace-europe', europe as unknown as Parameters<typeof echarts.registerMap>[1]);

export type ChartOption = echarts.EChartsCoreOption;
export type ChartClick = { name?: string; data?: unknown };

type Props = {
  option: ChartOption;
  /** Text alternative read by screen readers instead of the drawing. */
  label: string;
  height: number;
  onClick?: (event: ChartClick) => void;
};

export function Chart({ option, label, height, onClick }: Props) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const node = element.current!;
    const instance = echarts.init(node, undefined, { renderer: 'svg' });
    chart.current = instance;
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(node);
    return () => {
      resize.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const instance = chart.current;
    if (!instance || !onClick) return;
    const handler = (event: unknown) => onClick(event as ChartClick);
    instance.on('click', handler);
    return () => { instance.off('click', handler); };
  }, [onClick]);

  return <div className="demand-chart" style={{ height }} role="img" aria-label={label}><div ref={element} className="demand-chart-canvas" aria-hidden="true" /></div>;
}
