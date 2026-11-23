"""The countries on the Europe map and in the Skills Scope: EU27, the EEA
countries outside the EU, the UK and Switzerland.

This module imports nothing, so routers can use the list without loading
the Cassandra driver or another router's code.
"""

# Code -> name, sorted by name.
COUNTRIES = dict(sorted({
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia",
    "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia",
    "FI": "Finland", "FR": "France", "DE": "Germany", "GR": "Greece",
    "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia",
    "SI": "Slovenia", "ES": "Spain", "SE": "Sweden", "IS": "Iceland",
    "LI": "Liechtenstein", "NO": "Norway", "GB": "United Kingdom", "CH": "Switzerland",
}.items(), key=lambda item: item[1]))
