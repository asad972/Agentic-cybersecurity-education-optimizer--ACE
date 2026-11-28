import { useMemo, useState } from 'react';
import { BarChart } from 'echarts/charts';
import { GridComponent, LegendComponent } from 'echarts/components';
import { use as registerCharts } from 'echarts/core';
import type { EChartsOption } from 'echarts';
import {
  Award,
  Briefcase,
  ExternalLink,
  GitCompareArrows,
  GraduationCap,
  Layers,
  Search,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { Chart } from '../components/Chart';
import { CountryMap } from '../components/CountryMap';
import { ErrorState, Loading } from '../components/Status';
import { useQuery } from '../hooks/useQuery';
import type { Country, MapData } from '../services/api';
import { countLabel, yesterday } from '../services/dates';

registerCharts([BarChart, GridComponent, LegendComponent]);

type RoleSummary = {
  role: string;
  slug: string;
  summary: string | null;
  skills_count: number;
  certifications_count: number;
  top_skills: string[];
  demand: number | null;
  supply: number | null;
};

type RoleListResponse = {
  country: string | null;
  day: string | null;
  cassandra_connected: boolean;
  countries: Country[];
  roles: RoleSummary[];
};

type RoleSkill = {
  skill: string;
  nice_ids: string[];
  aliases: string[];
  other_roles: string[];
  demand: number | null;
  supply: number | null;
};

type RoleCertification = {
  certification: string;
  issuer: string;
  aliases: string[];
  demand: number | null;
  supply: number | null;
};

type RelatedRole = {
  role: string;
  slug: string;
  overlap: number;
  shared_skills: string[];
  source: 'cassandra' | 'taxonomy';
};

type RoleProgramme = {
  programme_id: string;
  name: string;
  institution: string;
  level: string | null;
  ecsf_roles: string[];
  url: string | null;
};

type RoleDetailResponse = {
  role: string;
  slug: string;
  summary: string | null;
  country: string;
  country_name: string;
  day: string;
  cassandra_connected: boolean;
  demand: number | null;
  supply: number | null;
  skills: RoleSkill[];
  certifications: RoleCertification[];
  related_roles: RelatedRole[];
  programmes: RoleProgramme[];
};

type TopCapabilityRow = {
  kind: 'Skill' | 'Certification';
  name: string;
  demand: number | null;
  supply: number | null;
};

export function RolePage() {
  const [selectedSlug, setSelectedSlug] = useState('cyber-incident-responder');
  const [country, setCountry] = useState('DE');
  const [dayChoice, setDayChoice] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  const latestDay = useQuery<{ date: string }>('/demand/latest-day');
  const day = dayChoice || (latestDay.loading ? '' : (latestDay.data?.date ?? yesterday()));

  const rolesQuery = useQuery<RoleListResponse>(day ? '/roles' : null, { country, day });
  const roles = rolesQuery.data?.roles ?? [];
  const resolvedSlug =
    roles.length > 0 && !roles.some(r => r.slug === selectedSlug)
      ? roles[0].slug
      : selectedSlug;

  const detailQuery = useQuery<RoleDetailResponse>(
    day && resolvedSlug ? `/roles/${encodeURIComponent(resolvedSlug)}` : null,
    { country, day },
  );
  const mapQuery = useQuery<MapData>(
    day && resolvedSlug ? `/roles/${encodeURIComponent(resolvedSlug)}/map` : null,
    { day },
  );

  const detail = detailQuery.data ?? null;
  const roleMap = mapQuery.data ?? null;

  const filteredRoles = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return roles;
    return roles.filter(
      item =>
        item.role.toLowerCase().includes(q) ||
        item.top_skills.some(skill => skill.toLowerCase().includes(q)),
    );
  }, [roles, searchQuery]);

  const maxSkillDemand = useMemo(() => {
    const values = (detail?.skills ?? [])
      .map(s => s.demand ?? 0)
      .filter(v => v > 0);
    return Math.max(1, ...values);
  }, [detail]);

  const topCapabilities = useMemo<TopCapabilityRow[]>(() => {
    if (!detail) return [];
    const combined: TopCapabilityRow[] = [
      ...detail.skills.slice(0, 7).map(s => ({
        kind: 'Skill' as const,
        name: s.skill,
        demand: s.demand,
        supply: s.supply,
      })),
      ...detail.certifications.slice(0, 5).map(c => ({
        kind: 'Certification' as const,
        name: c.certification,
        demand: c.demand,
        supply: c.supply,
      })),
    ];
    return combined
      .sort(
        (a, b) =>
          (b.demand ?? -1) - (a.demand ?? -1) ||
          (b.supply ?? -1) - (a.supply ?? -1) ||
          a.name.localeCompare(b.name),
      )
      .slice(0, 10);
  }, [detail]);

  const topCapabilitiesOption = useMemo<EChartsOption>(
    () => ({
      animationDurationUpdate: 250,
      grid: { left: 16, right: 24, top: 36, bottom: 12, containLabel: true },
      legend: { top: 4, textStyle: { color: '#617580', fontSize: 10 } },
      tooltip: { trigger: 'axis', confine: true, renderMode: 'richText' },
      xAxis: {
        type: 'value',
        min: 0,
        axisLabel: { color: '#71827a' },
        splitLine: { lineStyle: { color: '#edf0ee' } },
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: topCapabilities.map(item => `${item.name} (${item.kind === 'Skill' ? 'Skill' : 'Cert'})`),
        axisLabel: { color: '#52675f', width: 175, overflow: 'truncate' },
      },
      series: [
        {
          name: 'Demand',
          type: 'bar',
          data: topCapabilities.map(item => item.demand ?? '-'),
          itemStyle: { color: '#297d70' },
        },
        {
          name: 'Supply',
          type: 'bar',
          data: topCapabilities.map(item => item.supply ?? '-'),
          itemStyle: { color: '#d4a94f' },
        },
      ],
    }),
    [topCapabilities],
  );

  const resolvedCountries = useMemo(
    () =>
      rolesQuery.data?.countries && rolesQuery.data.countries.length > 0
        ? rolesQuery.data.countries
        : [{ code: 'DE', name: 'Germany' }],
    [rolesQuery.data],
  );

  const countryName = (code: string) =>
    resolvedCountries.find(item => item.code === code)?.name ?? code;

  const activeError = rolesQuery.error ?? detailQuery.error ?? mapQuery.error;
  const retryAll = () => {
    latestDay.retry();
    rolesQuery.retry();
    detailQuery.retry();
    mapQuery.retry();
  };

  const usesTaxonomyRelatedFallback =
    detail !== null &&
    detail.related_roles.length > 0 &&
    detail.related_roles.some(rel => rel.source === 'taxonomy');

  return (
    <>
      <section className="page-heading">
        <span className="eyebrow">CYBERSECURITY OBSERVATORY &middot; M1</span>
        <h1>Role explorer</h1>
        <p>
          Explore the 12 ENISA European Cybersecurity Skills Framework (ECSF) profiles,
          compare required skills and certifications, and inspect career transition pathways.
        </p>
      </section>

      <section className="panel filters" aria-label="Role explorer filters">
        <label>
          <span>
            <Search size={13} />
            Filter ECSF roles
          </span>
          <input
            type="search"
            placeholder="e.g. Incident, SIEM, Cloud..."
            value={searchQuery}
            onChange={event => setSearchQuery(event.target.value)}
          />
          <small>
            Showing {filteredRoles.length} of {roles.length || 12} ECSF profiles
          </small>
        </label>

        <label>
          <span>
            <ShieldCheck size={13} />
            Selected ECSF role
          </span>
          <select
            aria-label="Selected ECSF role"
            value={resolvedSlug}
            onChange={event => setSelectedSlug(event.target.value)}
          >
            {(filteredRoles.length > 0 ? filteredRoles : roles).map(item => (
              <option key={item.slug} value={item.slug}>
                {item.role}
              </option>
            ))}
          </select>
          <small>Canonical ENISA ECSF profile</small>
        </label>

        <label>
          <span>
            <Briefcase size={13} />
            Country
          </span>
          <select
            aria-label="Country"
            value={country}
            onChange={event => setCountry(event.target.value)}
          >
            {resolvedCountries.map(item => (
              <option key={item.code} value={item.code}>
                {item.name} ({item.code})
              </option>
            ))}
          </select>
          <small>European Skills Scope (EU27, EEA, UK, CH)</small>
        </label>

        <label>
          <span>
            <Layers size={13} />
            Snapshot date
          </span>
          <input
            aria-label="Snapshot date"
            type="date"
            value={day}
            onChange={event => setDayChoice(event.target.value)}
          />
          <small>Daily demand &amp; 365-day supply window</small>
        </label>
      </section>

      {activeError && <ErrorState message={activeError} retry={retryAll} />}

      {!activeError && (latestDay.loading || rolesQuery.loading || detailQuery.loading) && !detail && (
        <Loading text="Loading ECSF role profileâ€¦" />
      )}

      {!activeError && detail && (
        <>
          <section className="panel role-hero" aria-label="Selected role summary">
            <div className="role-hero-top">
              <div>
                <span className="eyebrow">ENISA ECSF PROFILE</span>
                <h2>{detail.role}</h2>
              </div>
              <span className="pill">
                {detail.country_name} &middot; {detail.day}
              </span>
            </div>
            {detail.summary && <p className="role-summary">{detail.summary}</p>}
          </section>

          <section className="stat-grid" aria-label="Role key metrics">
            <article className="stat-card">
              <span>Taxonomy capabilities</span>
              <strong>
                {detail.skills.length}{' '}
                <small>skills &middot; {detail.certifications.length} certifications</small>
              </strong>
              <small>Closed ACE skill &amp; certification taxonomy</small>
            </article>

            <article className="stat-card">
              <span>
                <Briefcase size={12} style={{ display: 'inline', marginRight: 5 }} />
                Role demand ({detail.country})
              </span>
              <strong>
                {detail.demand !== null ? (
                  <>
                    {detail.demand.toLocaleString()} <small>job ads</small>
                  </>
                ) : (
                  'No snapshot'
                )}
              </strong>
              <small>
                {detail.demand !== null
                  ? `Advertisements mapped to this role on ${detail.day}`
                  : 'No role demand row stored for this date'}
              </small>
            </article>

            <article className="stat-card">
              <span>
                <Users size={12} style={{ display: 'inline', marginRight: 5 }} />
                Practitioner supply ({detail.country})
              </span>
              <strong>
                {detail.supply !== null ? (
                  <>
                    {detail.supply.toLocaleString()} <small>profiles</small>
                  </>
                ) : (
                  'No snapshot'
                )}
              </strong>
              <small>
                {detail.supply !== null
                  ? `365-day active profile stock as of ${detail.day}`
                  : 'No 365-day supply snapshot for this date'}
              </small>
            </article>
          </section>

          <section className="explore-grid" aria-label="Role visual analytics">
            <article className="panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">TOP CAPABILITIES</span>
                  <h2>Top skills &amp; certifications ({detail.country})</h2>
                </div>
                <span className="pill">{detail.country_name}</span>
              </div>
              {topCapabilities.length === 0 ? (
                <div className="panel-section">
                  <p className="empty-inline">No skills or certifications available for this role.</p>
                </div>
              ) : (
                <>
                  <Chart
                    option={topCapabilitiesOption}
                    label={`Bar chart of top skills and certifications for ${detail.role} in ${detail.country_name}`}
                    className="role-bar-chart"
                  />
                  <details className="data-details">
                    <summary>View as table</summary>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th scope="col">Type</th>
                            <th scope="col">Capability</th>
                            <th scope="col">Demand</th>
                            <th scope="col">Supply</th>
                          </tr>
                        </thead>
                        <tbody>
                          {topCapabilities.map(row => (
                            <tr key={`${row.kind}:${row.name}`}>
                              <td>{row.kind}</td>
                              <td>{row.name}</td>
                              <td>{countLabel(row.demand)}</td>
                              <td>{countLabel(row.supply)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </>
              )}
            </article>

            <article className="panel map-panel" aria-label="Demand per country map">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">EUROPEAN ROLE DEMAND</span>
                  <h2>Demand per country</h2>
                </div>
                <span className="pill">{detail.role}</span>
              </div>
              {mapQuery.loading && !roleMap ? (
                <Loading text="Loading European demand mapâ€¦" />
              ) : (
                roleMap && (
                  <>
                    <CountryMap
                      response={roleMap}
                      countries={resolvedCountries}
                      selected={country}
                      onSelect={setCountry}
                    />
                    <div className="map-explanation">
                      <strong>Click a country to inspect</strong>
                      <p>{roleMap.note}</p>
                    </div>
                    <details className="data-details">
                      <summary>View as table</summary>
                      <div className="table-scroll">
                        <table>
                          <thead>
                            <tr>
                              <th scope="col">Country</th>
                              <th scope="col">{roleMap.unit}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {roleMap.data.map(row => (
                              <tr key={row.country}>
                                <td>
                                  <button
                                    type="button"
                                    className="text-button"
                                    onClick={() => setCountry(row.country)}
                                  >
                                    {countryName(row.country)}
                                  </button>
                                </td>
                                <td>{countLabel(row.value)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  </>
                )
              )}
            </article>
          </section>

          <section className="explore-grid" style={{ marginTop: 16 }}>
            <div className="role-column">
              <article className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">TAXONOMY &amp; MARKET DEMAND</span>
                    <h2>Required skills ({detail.skills.length})</h2>
                  </div>
                  <span className="pill">{detail.country_name}</span>
                </div>
                <div className="panel-section">
                  {detail.skills.length === 0 ? (
                    <p className="empty-inline">No skills mapped to this role.</p>
                  ) : (
                    <ul className="role-skill-list">
                      {detail.skills.map(item => (
                        <li key={item.skill} className="role-skill-item">
                          <div className="role-skill-header">
                            <div>
                              <strong>{item.skill}</strong>
                              {item.nice_ids.length > 0 && (
                                <span className="role-badge">
                                  NICE: {item.nice_ids.join(', ')}
                                </span>
                              )}
                            </div>
                            <span className="role-counts">
                              {item.demand !== null && (
                                <span>Demand: {item.demand.toLocaleString()}</span>
                              )}
                              {item.supply !== null && (
                                <span>Supply: {item.supply.toLocaleString()}</span>
                              )}
                              {item.demand === null && item.supply === null && (
                                <span>Taxonomy baseline</span>
                              )}
                            </span>
                          </div>
                          {item.demand !== null && item.demand > 0 && (
                            <div className="rank-track">
                              <i
                                style={{
                                  width: `${Math.max(5, (item.demand / maxSkillDemand) * 100)}%`,
                                }}
                              />
                            </div>
                          )}
                          {item.aliases.length > 0 && (
                            <p className="role-aliases">
                              Aliases: {item.aliases.join(' · ')}
                            </p>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </article>

              <article className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">CAREER PATHWAYS</span>
                    <h2>Related ECSF roles</h2>
                  </div>
                  <span className="pill">
                    <GitCompareArrows size={11} style={{ display: 'inline', marginRight: 4 }} />
                    Skill overlap
                  </span>
                </div>
                <div className="panel-section">
                  {usesTaxonomyRelatedFallback && (
                    <p className="role-aliases" style={{ marginBottom: 10 }}>
                      Computed from shared taxonomy skills (no batch role-relation snapshot stored for this role yet).
                    </p>
                  )}
                  {detail.related_roles.length === 0 ? (
                    <p className="empty-inline">
                      No overlapping ECSF roles found for this profile.
                    </p>
                  ) : (
                    <ul className="role-related-list">
                      {detail.related_roles.map(rel => (
                        <li key={rel.role} className="role-related-item">
                          <div className="role-related-top">
                            <button
                              type="button"
                              className="text-button role-switch-btn"
                              onClick={() => setSelectedSlug(rel.slug)}
                            >
                              {rel.role}
                            </button>
                            <strong>{Math.round(rel.overlap * 100)}% overlap</strong>
                          </div>
                          <div className="rank-track">
                            <i
                              style={{
                                width: `${Math.max(6, Math.min(100, rel.overlap * 100))}%`,
                              }}
                            />
                          </div>
                          {rel.shared_skills.length > 0 && (
                            <p className="role-aliases">
                              Shared skills: {rel.shared_skills.join(', ')}
                            </p>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </article>
            </div>

            <div className="role-column">
              <article className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">CREDENTIALS</span>
                    <h2>Certifications ({detail.certifications.length})</h2>
                  </div>
                  <span className="pill">
                    <Award size={11} style={{ display: 'inline', marginRight: 4 }} />
                    Taxonomy
                  </span>
                </div>
                <div className="panel-section">
                  {detail.certifications.length === 0 ? (
                    <p className="empty-inline">
                      No specific certifications mapped to this ECSF role in the taxonomy.
                    </p>
                  ) : (
                    <ul className="role-cert-list">
                      {detail.certifications.map(cert => (
                        <li key={cert.certification} className="role-cert-item">
                          <div>
                            <strong>{cert.certification}</strong>
                            <span>{cert.issuer}</span>
                          </div>
                          <div className="role-cert-meta">
                            {cert.demand !== null && (
                              <small>Demand: {cert.demand.toLocaleString()}</small>
                            )}
                            {cert.supply !== null && (
                              <small>Supply: {cert.supply.toLocaleString()}</small>
                            )}
                            {cert.demand === null && cert.supply === null && (
                              <small>Taxonomy</small>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </article>

              <article className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">ACADEMIC PATHWAYS</span>
                    <h2>Education programmes ({detail.country})</h2>
                  </div>
                  <span className="pill">
                    <GraduationCap size={11} style={{ display: 'inline', marginRight: 4 }} />
                    CYBERHEAD
                  </span>
                </div>
                <div className="panel-section">
                  {detail.programmes.length === 0 ? (
                    <p className="empty-inline">
                      No education programmes in {detail.country_name} are currently tagged
                      with {detail.role}.
                    </p>
                  ) : (
                    <ul className="m5-programmes">
                      {detail.programmes.map(prog => (
                        <li key={prog.programme_id}>
                          <div>
                            <strong>{prog.name}</strong>
                            <span>
                              {prog.institution}
                              {prog.level ? ` · ${prog.level}` : ''}
                            </span>
                          </div>
                          {prog.url && (
                            <a href={prog.url} target="_blank" rel="noreferrer">
                              Source <ExternalLink size={11} style={{ display: 'inline' }} />
                            </a>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </article>
            </div>
          </section>

          <section className="panel role-directory" aria-label="All 12 ENISA ECSF roles">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">ENISA FRAMEWORK DIRECTORY</span>
                <h2>All 12 ECSF role profiles</h2>
              </div>
              <span className="pill">{roles.length} roles</span>
            </div>
            <div className="role-grid">
              {filteredRoles.map(item => (
                <button
                  key={item.slug}
                  type="button"
                  className={
                    'role-card-btn' + (item.slug === detail.slug ? ' is-selected' : '')
                  }
                  onClick={() => setSelectedSlug(item.slug)}
                >
                  <span className="eyebrow">ENISA ECSF</span>
                  <strong>{item.role}</strong>
                  {item.summary && <p>{item.summary}</p>}
                  <div className="role-card-footer">
                    <span>{item.skills_count} skills</span>
                    <span>{item.certifications_count} certs</span>
                    {item.demand !== null && <span>{item.demand} ads</span>}
                  </div>
                </button>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}