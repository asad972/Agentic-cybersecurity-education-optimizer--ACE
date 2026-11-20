import { useEffect, useMemo, useState } from 'react';
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
import { getJSON } from '../services/api';
import type { Country } from '../services/api';

type RoleSummary = {
  role: string;
  slug: string;
  focus_area: string;
  summary: string;
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
  focus_area: string;
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
  focus_area: string;
  summary: string;
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

function defaultSnapshotDate() {
  const yesterday = new Date();
  yesterday.setUTCDate(yesterday.getUTCDate() - 1);
  return yesterday.toISOString().slice(0, 10);
}

export function RolePage() {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [countries, setCountries] = useState<Country[]>([]);
  const [selectedSlug, setSelectedSlug] = useState('cyber-incident-responder');
  const [country, setCountry] = useState('DE');
  const [day, setDay] = useState(defaultSnapshotDate);
  const [searchQuery, setSearchQuery] = useState('');
  const [detail, setDetail] = useState<RoleDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getJSON<RoleListResponse>('/roles', { country, day }, controller.signal)
      .then(data => {
        setRoles(data.roles);
        setCountries(data.countries);
        if (data.roles.length > 0 && !data.roles.some(r => r.slug === selectedSlug)) {
          setSelectedSlug(data.roles[0].slug);
        }
      })
      .catch(err => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Unable to load ECSF roles.');
      });
    return () => controller.abort();
  }, [country, day]);

  useEffect(() => {
    if (!selectedSlug) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    getJSON<RoleDetailResponse>(
      `/roles/${encodeURIComponent(selectedSlug)}`,
      { country, day },
      controller.signal,
    )
      .then(data => setDetail(data))
      .catch(err => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Unable to load role details.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [selectedSlug, country, day]);

  const filteredRoles = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return roles;
    return roles.filter(
      item =>
        item.role.toLowerCase().includes(q) ||
        item.focus_area.toLowerCase().includes(q) ||
        item.top_skills.some(skill => skill.toLowerCase().includes(q)),
    );
  }, [roles, searchQuery]);

  const maxSkillDemand = useMemo(() => {
    const values = (detail?.skills ?? [])
      .map(s => s.demand ?? 0)
      .filter(v => v > 0);
    return Math.max(1, ...values);
  }, [detail]);

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
            value={selectedSlug}
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
            {(countries.length > 0
              ? countries
              : [{ code: 'DE', name: 'Germany' }]
            ).map(item => (
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
            onChange={event => setDay(event.target.value)}
          />
          <small>Daily demand &amp; 365-day supply window</small>
        </label>
      </section>

      {error && (
        <div className="panel state-box error-box" role="alert">
          <strong>Could not load Role Explorer</strong>
          <p>{error}</p>
        </div>
      )}

      {!error && loading && !detail && (
        <div className="panel state-box" role="status">
          <strong>Loading ECSF role profile&hellip;</strong>
          <p>Fetching taxonomy skills, certifications, and country metrics.</p>
        </div>
      )}

      {!error && detail && (
        <>
          <section className="panel role-hero" aria-label="Selected role summary">
            <div className="role-hero-top">
              <div>
                <span className="eyebrow">{detail.focus_area.toUpperCase()}</span>
                <h2>{detail.role}</h2>
              </div>
              <span className="pill">
                {detail.country_name} &middot; {detail.day}
              </span>
            </div>
            <p className="role-summary">{detail.summary}</p>
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

          <section className="explore-grid">
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
                    Verified
                  </span>
                </div>
                <div className="panel-section">
                  {detail.certifications.length === 0 ? (
                    <p className="empty-inline">
                      No specific certifications mapped to this ECSF role.
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
                  <span className="eyebrow">{item.focus_area}</span>
                  <strong>{item.role}</strong>
                  <p>{item.summary}</p>
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