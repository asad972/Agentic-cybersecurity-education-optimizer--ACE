// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';

const chartRender = vi.hoisted(() => vi.fn());

vi.mock('../components/Chart', () => ({
  Chart: ({ label, option }: { label: string; option: unknown }) => {
    chartRender(label, option);
    return <div role="img" aria-label={label} />;
  },
}));

vi.mock('../components/CountryMap', () => ({
  CountryMap: ({ onSelect }: { onSelect: (code: string) => void }) => (
    <div role="img" aria-label="Role demand map">
      <button type="button" onClick={() => onSelect('FR')}>
        Select France on map
      </button>
    </div>
  ),
}));

import { RolePage } from './RolePage';

let rolesFailureStatus = 0;
let calls: URL[] = [];

const rolesListPayload = {
  country: 'DE',
  day: '2026-10-15',
  cassandra_connected: true,
  countries: [
    { code: 'DE', name: 'Germany' },
    { code: 'FR', name: 'France' },
  ],
  roles: [
    {
      role: 'Cyber Incident Responder',
      slug: 'cyber-incident-responder',
      summary: 'Analyses and responds to cybersecurity incidents.',
      skills_count: 2,
      certifications_count: 1,
      top_skills: ['SIEM', 'Incident Response'],
      demand: 42,
      supply: 19,
    },
    {
      role: 'Penetration Tester',
      slug: 'penetration-tester',
      summary: 'Assesses systems by simulating authorised attacks.',
      skills_count: 1,
      certifications_count: 1,
      top_skills: ['Penetration Testing'],
      demand: 27,
      supply: 14,
    },
  ],
};

function detailPayload(slug: string, country: string, day: string) {
  const isPentester = slug === 'penetration-tester';
  return {
    role: isPentester ? 'Penetration Tester' : 'Cyber Incident Responder',
    slug,
    summary: isPentester
      ? 'Assesses systems by simulating authorised attacks.'
      : 'Analyses and responds to cybersecurity incidents.',
    country,
    country_name: country === 'FR' ? 'France' : 'Germany',
    day,
    cassandra_connected: true,
    demand: isPentester ? 27 : 42,
    supply: isPentester ? 14 : 19,
    skills: [
      {
        skill: isPentester ? 'Penetration Testing' : 'SIEM',
        nice_ids: ['T0023'],
        aliases: ['Security Information and Event Management'],
        other_roles: ['Cybersecurity Auditor'],
        demand: 31,
        supply: 15,
      },
    ],
    certifications: [
      {
        certification: isPentester ? 'OSCP' : 'BTL1',
        issuer: isPentester ? 'OffSec' : 'Security Blue Team',
        aliases: [],
        demand: 12,
        supply: 6,
      },
    ],
    related_roles: [
      {
        role: isPentester ? 'Cyber Incident Responder' : 'Penetration Tester',
        slug: isPentester ? 'cyber-incident-responder' : 'penetration-tester',
        overlap: 0.45,
        shared_skills: ['SIEM'],
        source: 'cassandra',
      },
    ],
    programmes: [
      {
        programme_id: 'prog-1',
        name: 'MSc Incident Response & Forensics',
        institution: 'TU Darmstadt',
        level: 'master',
        ecsf_roles: ['Cyber Incident Responder'],
        url: 'https://example.edu/ir-msc',
      },
    ],
  };
}

beforeEach(() => {
  rolesFailureStatus = 0;
  calls = [];
  chartRender.mockClear();

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      calls.push(url);

      if (url.pathname === '/demand/latest-day') {
        return Response.json({ date: '2026-10-15' });
      }
      if (url.pathname === '/roles') {
        if (rolesFailureStatus > 0) {
          const status = rolesFailureStatus;
          rolesFailureStatus = 0;
          return Response.json({ detail: 'Cassandra is unavailable' }, { status });
        }
        return Response.json({
          ...rolesListPayload,
          country: url.searchParams.get('country') ?? 'DE',
          day: url.searchParams.get('day') ?? '2026-10-15',
        });
      }
      if (url.pathname === '/roles/cyber-incident-responder/map' || url.pathname === '/roles/penetration-tester/map') {
        const isPentester = url.pathname.includes('penetration-tester');
        return Response.json({
          metric: 'demand',
          date: url.searchParams.get('day') ?? '2026-10-15',
          item: isPentester ? 'Penetration Tester' : 'Cyber Incident Responder',
          unit: 'job ads',
          period: 'daily',
          note: 'Daily estimated job advertisements mapped to this ECSF role per country.',
          data: [
            { country: 'DE', value: 42 },
            { country: 'FR', value: 18 },
          ],
        });
      }
      if (url.pathname === '/roles/cyber-incident-responder') {
        return Response.json(
          detailPayload(
            'cyber-incident-responder',
            url.searchParams.get('country') ?? 'DE',
            url.searchParams.get('day') ?? '2026-10-15',
          ),
        );
      }
      if (url.pathname === '/roles/penetration-tester') {
        return Response.json(
          detailPayload(
            'penetration-tester',
            url.searchParams.get('country') ?? 'DE',
            url.searchParams.get('day') ?? '2026-10-15',
          ),
        );
      }
      return Response.json({ detail: 'Not found' }, { status: 404 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('RolePage', () => {
  it('renders the Role explorer heading, filters, and loading state', () => {
    const html = renderToStaticMarkup(<RolePage />);
    expect(html).toContain('Role explorer');
    expect(html).toContain('Filter ECSF roles');
    expect(html).toContain('Selected ECSF role');
    expect(html).toContain('Loading ECSF role profile');
  });

  it('renders role details, charts, related roles, and programmes from mocked API data', async () => {
    const user = userEvent.setup();
    render(<RolePage />);

    expect(
      (await screen.findAllByText('Analyses and responds to cybersecurity incidents.')).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByRole('img', {
        name: /Bar chart of top skills and certifications for Cyber Incident Responder in Germany/,
      }),
    ).toBeTruthy();
    expect(screen.getByRole('img', { name: 'Role demand map' })).toBeTruthy();
    const barOption = chartRender.mock.calls
      .flat()
      .find(
        (arg): arg is { series: Array<{ name: string; data: unknown[] }> } =>
          typeof arg === 'object' &&
          arg !== null &&
          Array.isArray((arg as { series?: unknown }).series) && (arg as { series: Array<{ name?: string }> }).series.some(s => s.name === 'Demand'),
      );
    expect(barOption).toBeTruthy();
    expect(barOption!.series.map(s => s.name)).toEqual(['Demand', 'Supply']);
    expect(barOption!.series[0].data.length).toBeGreaterThan(0);
    expect(barOption!.series[1].data.length).toBeGreaterThan(0);
    expect(screen.getByText('45% overlap')).toBeTruthy();
    expect(screen.getByText('MSc Incident Response & Forensics')).toBeTruthy();
    expect(screen.getByText('Security Blue Team')).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Select France on map' }));
    await waitFor(() =>
      expect(
        calls.some(
          url =>
            url.pathname === '/roles/cyber-incident-responder' &&
            url.searchParams.get('country') === 'FR',
        ),
      ).toBe(true),
    );
  });

  it('shows an error alert when the API fails and recovers on retry', async () => {
    rolesFailureStatus = 503;
    const user = userEvent.setup();
    render(<RolePage />);

    expect(await screen.findByText('Cassandra is unavailable')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /Try again/ }));

    expect(
      (await screen.findAllByText('Analyses and responds to cybersecurity incidents.')).length,
    ).toBeGreaterThan(0);
  });
});
