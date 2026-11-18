import { Radio, Zap } from 'lucide-react';
import { ALERT_LABELS, alertKey } from '../services/api';
import type { Country, FeedState } from '../services/api';
import { useLiveAlerts } from '../hooks/useLiveAlerts';
import { countLabel, formatDate, formatTime } from '../services/dates';

const STATE_LABELS: Record<FeedState, string> = { connecting: 'Connecting', connected: 'Live', unavailable: 'Feed unavailable', disabled: 'Switched off', offline: 'Reconnecting', refused: 'Not allowed' };

/** Drift and threat-burst alerts as they are raised, from WS /live. Earlier alerts live in the country panel. */
export function LiveAlerts({ countries, onCountry }: { countries: Country[]; onCountry: (code: string) => void }) {
  const feed = useLiveAlerts();
  const name = (code: string) => code === 'EU' ? 'European Union' : countries.find(item => item.code === code)?.name ?? code;
  return <section className="panel live-panel" aria-label="Live alerts">
    <div className="panel-heading">
      <div><span className="eyebrow">AS IT HAPPENS</span><h2>Live alerts</h2></div>
      <span className={`feed-state feed-${feed.state}`} role="status" title={feed.message}><Radio size={12} />{STATE_LABELS[feed.state]}</span>
    </div>
    <div className="live-body" aria-live="polite">
      {feed.alerts.length ? <ul className="alert-list">{feed.alerts.map(alert => <li key={alertKey(alert)}>
        <span className="alert-icon"><Zap size={13} /></span>
        <div>
          <strong>{countries.some(item => item.code === alert.country)
            ? <button className="text-button" title={`Show ${name(alert.country)} in the country panel`} onClick={() => onCountry(alert.country)}>{name(alert.country)}</button>
            : name(alert.country)} · {ALERT_LABELS[alert.alert_type] ?? alert.alert_type}: {alert.subject}</strong>
          <time dateTime={alert.created_at}>{formatDate(alert.day)}, {formatTime(alert.created_at)}</time>
        </div>
        <span className="score">{countLabel(alert.score)}<small>score</small></span>
      </li>)}</ul> : <p className="empty-inline">{feed.state === 'connected'
        ? 'No alerts have been raised since this page was opened. Earlier alerts are listed per day in the country panel.'
        : feed.message}</p>}
      {feed.alerts.length > 0 && feed.state !== 'connected' && <p className="tiny-note">{feed.message}</p>}
      {feed.skipped && <p className="tiny-note feed-skipped" role="status">Some alerts were skipped because this page fell behind. The country panel lists every alert of a day.</p>}
    </div>
  </section>;
}
