import { useEffect, useRef } from 'react';
import { init, use } from 'echarts/core';
import { MapChart } from 'echarts/charts';
import { TooltipComponent, VisualMapComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption, EChartsType } from 'echarts';

// Only the parts the map needs are registered, to keep the bundle small.
use([MapChart, TooltipComponent, VisualMapComponent, CanvasRenderer]);

type Props = { option: EChartsOption; label: string; className?: string; onCountry?: (code: string) => void };

export function Chart({ option, label, className = '', onCountry }: Props) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType | null>(null);
  useEffect(() => {
    if (!element.current) return;
    chart.current = init(element.current, undefined, { renderer: 'canvas' });
    const resize = new ResizeObserver(() => chart.current?.resize());
    resize.observe(element.current);
    return () => { resize.disconnect(); chart.current?.dispose(); chart.current = null; };
  }, []);
  useEffect(() => { chart.current?.setOption(option, { notMerge: true }); }, [option]);
  useEffect(() => {
    const instance = chart.current;
    const onClick = (params: unknown) => { const name = (params as { name?: string }).name; if (name) onCountry?.(name); };
    instance?.on('click', onClick);
    return () => { instance?.off('click', onClick); };
  }, [onCountry]);
  return <div ref={element} className={`chart ${className}`} role="img" aria-label={label} />;
}
