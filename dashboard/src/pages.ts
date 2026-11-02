import { Compass, Network, ChartNoAxesCombined, Layers, Shield, GraduationCap } from 'lucide-react';
import { EuropePage } from './pages/EuropePage';
import { RolePage } from './pages/RolePage';
import { DemandPage } from './pages/DemandPage';
import { ForecastPage } from './pages/ForecastPage';
import { ThreatPage } from './pages/ThreatPage';
import { EducationPage } from './pages/EducationPage';

export const pages = [
  { id: 'europe', name: 'Europe overview', icon: Compass, component: EuropePage },
  { id: 'roles', name: 'Role explorer', icon: Network, component: RolePage },
  { id: 'demand', name: 'Skills demand', icon: ChartNoAxesCombined, component: DemandPage },
  { id: 'forecast', name: 'Supply, gap & forecast', icon: Layers, component: ForecastPage },
  { id: 'threats', name: 'Threat landscape', icon: Shield, component: ThreatPage },
  { id: 'education', name: 'Education & workforce', icon: GraduationCap, component: EducationPage },
];

export function pageFromHash(hash: string) {
  return pages.find(page => page.id === hash.slice(1)) ?? pages[0];
}
