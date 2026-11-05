import { useEffect, useState } from 'react';

type Programme = {
  programme_id: string;
  name: string;
  institution: string;
  level: string | null;
  ecsf_roles: string[];
  url: string | null;
};

type WorkforceRecord = {
  year: number;
  workforce_size: number | null;
  workforce_gap: number | null;
  source: string;
};

type EducationData = {
  country: string;
  programmes: Programme[];
  workforce: WorkforceRecord[];
};

type EmergingData = {
  country: string;
  day: string;
  items: { mention: string; count: number; max_error?: number }[];
};

type CompaniesData = {
  country: string;
  day: string;
  companies_day: number;
  companies_30d: number;
  missing_company: number;
};

type PlatformData = {
  items: {
    platform: string;
    feature: string;
    supported: boolean;
    notes?: string;
    analysed_at: string;
  }[];
};

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

// Use a configured API URL in deployments and the local API by default.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new ApiError(`API request failed (${response.status})`, response.status);
  }
  return response.json() as Promise<T>;
}

function BarChart({
  title,
  items,
}: {
  title: string;
  items: { label: string; value: number; detail?: string }[];
}) {
  const maximum = Math.max(1, ...items.map(item => item.value));
  return (
    <section className="m5-card" aria-label={title}>
      <h2>{title}</h2>
      {items.length === 0 ? (
        <p className="m5-empty">No observations are available for this selection.</p>
      ) : (
        <ul className="m5-bars">
          {items.map(item => (
            <li key={item.label}>
              <div className="m5-bar-heading">
                <span>{item.label}</span>
                <strong>
                  {item.value.toLocaleString()}
                  {item.detail ? ` · ${item.detail}` : ''}
                </strong>
              </div>
              <div
                className="m5-bar-track"
                role="meter"
                aria-label={item.label}
                aria-valuemin={0}
                aria-valuemax={maximum}
                aria-valuenow={item.value}
              >
                <span style={{ width: `${Math.max(3, (item.value / maximum) * 100)}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function EducationPage() {
  const [country, setCountry] = useState('DE');
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [education, setEducation] = useState<EducationData | null>(null);
  const [emerging, setEmerging] = useState<EmergingData | null>(null);
  const [companies, setCompanies] = useState<CompaniesData | null>(null);
  const [platforms, setPlatforms] = useState<PlatformData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    const query = `country=${encodeURIComponent(country)}&day=${encodeURIComponent(day)}`;
    setLoading(true);
    setError(null);

    Promise.all([
      getJson<EducationData>(`/education?country=${encodeURIComponent(country)}`, controller.signal),
      getJson<EmergingData>(`/emerging?${query}`, controller.signal),
      getJson<PlatformData>('/platforms', controller.signal),
      getJson<CompaniesData>(`/companies?${query}`, controller.signal).catch(error => {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }),
    ])
      .then(([educationResult, emergingResult, platformResult, companyResult]) => {
        setEducation(educationResult);
        setEmerging(emergingResult);
        setPlatforms(platformResult);
        setCompanies(companyResult);
      })
      .catch(error => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setError(error instanceof Error ? error.message : 'Unable to load dashboard data.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [country, day]);

  const workforceBars = (education?.workforce ?? []).flatMap(record => [
    ...(record.workforce_size === null
      ? []
      : [{ label: `${record.year} workforce`, value: record.workforce_size }]),
    ...(record.workforce_gap === null
      ? []
      : [{ label: `${record.year} workforce gap`, value: record.workforce_gap }]),
  ]);
  const emergingBars = (emerging?.items ?? []).slice(0, 8).map(item => ({
    label: item.mention,
    value: item.count,
    detail: item.max_error === undefined ? undefined : `±${item.max_error} max error`,
  }));

  return (
    <>
      <section className="page-heading">
        <span className="eyebrow">CYBERSECURITY OBSERVATORY · M5</span>
        <h1>Education &amp; workforce</h1>
        <p>
          Explore education pathways, workforce estimates, emerging skills and
          the evidence behind platform comparisons.
        </p>
      </section>

      <section className="m5-controls" aria-label="Dashboard filters">
        <label>
          Country
          <input
            aria-label="Country code"
            maxLength={2}
            value={country}
            onChange={event => setCountry(event.target.value.toUpperCase())}
          />
        </label>
        <label>
          Snapshot date
          <input
            aria-label="Snapshot date"
            type="date"
            value={day}
            onChange={event => setDay(event.target.value)}
          />
        </label>
        {loading && <span role="status">Loading live data…</span>}
      </section>

      {error && (
        <p className="m5-error" role="alert">
          Could not load the M5 dashboard: {error}. Check that the API and
          Cassandra are running.
        </p>
      )}

      {!error && !loading && (
        <>
          <section className="m5-metrics" aria-label="Hiring-company summary">
            <article className="m5-card">
              <span className="eyebrow">UNIQUE EMPLOYERS · DAY</span>
              <strong>{companies?.companies_day?.toLocaleString() ?? 'No data'}</strong>
              <p>{companies ? `Snapshot for ${companies.day}` : 'No snapshot for this date.'}</p>
            </article>
            <article className="m5-card">
              <span className="eyebrow">UNIQUE EMPLOYERS · 30 DAYS</span>
              <strong>{companies?.companies_30d?.toLocaleString() ?? 'No data'}</strong>
              <p>
                {companies
                  ? `${companies.missing_company} ads had no company name.`
                  : 'Waiting for an S10 snapshot.'}
              </p>
            </article>
            <article className="m5-card">
              <span className="eyebrow">EDUCATION PROGRAMMES</span>
              <strong>{education?.programmes.length.toLocaleString() ?? '—'}</strong>
              <p>Programmes recorded for {country || 'the selected country'}.</p>
            </article>
          </section>

          <section className="m5-charts" aria-label="Education and workforce charts">
            <BarChart title="Workforce by year" items={workforceBars} />
            <BarChart title="Emerging skill candidates" items={emergingBars} />
          </section>

          <section className="m5-card m5-section">
            <h2>Education programmes</h2>
            {!education?.programmes.length ? (
              <p className="m5-empty">No programme records are available for {country}.</p>
            ) : (
              <ul className="m5-programmes">
                {education.programmes.map(programme => (
                  <li key={programme.programme_id}>
                    <div>
                      <strong>{programme.name}</strong>
                      <span>
                        {programme.institution}
                        {programme.level ? ` · ${programme.level}` : ''}
                      </span>
                      {programme.ecsf_roles.length > 0 && (
                        <span>ECSF: {programme.ecsf_roles.join(', ')}</span>
                      )}
                    </div>
                    {programme.url && (
                      <a href={programme.url} target="_blank" rel="noreferrer">
                        Source
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="m5-card m5-section">
            <h2>Platform evidence</h2>
            {!platforms?.items.length ? (
              <p className="m5-empty">No reviewed platform evidence is available.</p>
            ) : (
              <div className="m5-table-wrap">
                <table className="m5-table">
                  <thead>
                    <tr>
                      <th scope="col">Platform</th>
                      <th scope="col">Feature</th>
                      <th scope="col">Evidence status</th>
                      <th scope="col">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {platforms.items.map(item => (
                      <tr key={`${item.platform}-${item.feature}`}>
                        <td>{item.platform}</td>
                        <td>{item.feature}</td>
                        <td>{item.supported ? 'Supported' : 'Not supported'}</td>
                        <td>{item.notes || 'No notes recorded.'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
