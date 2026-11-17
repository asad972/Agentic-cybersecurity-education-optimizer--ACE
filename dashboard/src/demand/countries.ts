// Skills Scope: EU27, the EEA countries outside the EU, the UK and Switzerland (ISO 3166-1 alpha-2).
export const countryNames: Record<string, string> = {
  AT: 'Austria', BE: 'Belgium', BG: 'Bulgaria', CH: 'Switzerland', CY: 'Cyprus', CZ: 'Czechia',
  DE: 'Germany', DK: 'Denmark', EE: 'Estonia', ES: 'Spain', FI: 'Finland', FR: 'France',
  GB: 'United Kingdom', GR: 'Greece', HR: 'Croatia', HU: 'Hungary', IE: 'Ireland', IS: 'Iceland',
  IT: 'Italy', LI: 'Liechtenstein', LT: 'Lithuania', LU: 'Luxembourg', LV: 'Latvia', MT: 'Malta',
  NL: 'Netherlands', NO: 'Norway', PL: 'Poland', PT: 'Portugal', RO: 'Romania', SE: 'Sweden',
  SI: 'Slovenia', SK: 'Slovakia',
};

export const scopeCountries = Object.keys(countryNames).sort((a, b) => countryNames[a].localeCompare(countryNames[b]));

export function countryName(code: string) {
  return countryNames[code] ?? code;
}
