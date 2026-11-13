import { MapPin, Zap } from 'lucide-react';
import type { CountryData } from '../services/api';
import { countLabel, formatDate, formatTime } from '../services/dates';
import { ErrorState, Loading } from './Status';

type Props = {
  code: string;
  name: string;
  date: string;
  panel: { data?: CountryData; error?: string; loading: boolean; retry: () => void };
  onSkill: (skill: string) => void;
};

const ALERT_LABELS: Record<string, string> = { drift: 'Demand drift', threat_burst: 'Threat burst' };

export function CountryPanel({ code, name, date, panel, onSkill }: Props) {
  const data = panel.data;
  const top = data?.top_skills[0]?.count || 1;
  return <aside className="panel country-panel" aria-label={`${name} on ${formatDate(date)}`}>
    <div className="country-heading">
      <span className="country-code">{code}</span>
      <div><span className="eyebrow">COUNTRY SPOTLIGHT</span><h2>{name}</h2></div>
      <MapPin size={18} />
    </div>
    {panel.loading ? <Loading text="Loading country data…" />
      : panel.error ? <ErrorState message={panel.error} retry={panel.retry} />
      : data && <>
        <section className="panel-section">
          <h3>Most requested skills <span>TOP 10 · 30 DAYS</span></h3>
          {data.top_skills.length ? <ol className="ranking">{data.top_skills.map(row => <li key={row.rank}>
            <span className="rank">{String(row.rank).padStart(2, '0')}</span>
            <div>
              <button className="text-button" title={`Show ${row.item} demand on the map`} onClick={() => onSkill(row.item)}>{row.item}</button>
              <div className="rank-track"><i style={{ width: `${Math.max(2, row.count / top * 100)}%` }} /></div>
            </div>
            <strong>{countLabel(row.count)}</strong>
          </li>)}</ol> : <p className="empty-inline">No Top 10 skills are stored for this date.</p>}
        </section>
        <section className="panel-section">
          <h3>Certifications <span>TOP 10 · 30 DAYS</span></h3>
          {data.top_certifications.length ? <div className="tags">{data.top_certifications.map(row =>
            <span key={row.rank} title={`${countLabel(row.count)} job ads`}>{row.item}<small>{countLabel(row.count)}</small></span>)}</div>
            : <p className="empty-inline">No Top 10 certifications are stored for this date.</p>}
        </section>
        <section className="panel-section">
          <h3>Workforce <span>{data.workforce ? `LATEST · ${data.workforce.year}` : 'LATEST BASELINE'}</span></h3>
          {data.workforce ? <>
            <div className="mini-stats">
              <div><strong>{countLabel(data.workforce.workforce_size)}</strong><span>professionals</span></div>
              <div><strong>{countLabel(data.workforce.workforce_gap)}</strong><span>workforce gap</span></div>
            </div>
            <p className="tiny-note">Latest stored baseline{data.workforce.source ? ` (${data.workforce.source})` : ''}, not the selected date.</p>
          </> : <p className="empty-inline">No workforce baseline is stored for this country.</p>}
        </section>
        <section className="panel-section">
          <h3>Alerts <span>{formatDate(date).toUpperCase()}</span></h3>
          {data.alerts.length ? <ul className="alert-list">{data.alerts.map(alert => <li key={`${alert.created_at}|${alert.alert_type}|${alert.subject}`}>
            <span className="alert-icon"><Zap size={13} /></span>
            <div><strong>{ALERT_LABELS[alert.alert_type] ?? alert.alert_type}: {alert.subject}</strong><time dateTime={alert.created_at}>{formatTime(alert.created_at)}</time></div>
            <span className="score">{countLabel(alert.score)}<small>score</small></span>
          </li>)}</ul> : <p className="empty-inline">No drift or threat-burst alerts on this date.</p>}
          {data.alerts_truncated && <p className="tiny-note">Showing the 50 most recent alerts of this day.</p>}
        </section>
      </>}
  </aside>;
}
