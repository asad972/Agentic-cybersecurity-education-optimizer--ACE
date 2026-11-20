import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { RolePage } from './RolePage';

afterEach(() => {
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
});