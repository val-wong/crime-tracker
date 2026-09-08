"""Chicago community-area code -> official name lookup.

The Chicago crime dataset's `community_area` field (stored on
`Incident.neighborhood`, see app/adapters/chicago.py) is a numeric code
(1-77), not a name -- see docs/sources/chicago.md §8. This module
resolves those codes to the City's own official community area names.

Source (first-party, authoritative): City of Chicago Data Portal,
"Boundaries - Community Areas" dataset (Socrata id `igwz-8jzy`),
<https://data.cityofchicago.org/Facilities-Geographic-Boundaries/Boundaries-Community-Areas/igwz-8jzy>,
published by the City of Chicago. Fetched directly via the dataset's
SODA API (`area_num_1`, `community` fields) on 2026-09-07; confirmed
77 rows, codes 1-77 with no gaps or duplicates. Chicago's 77 community
areas are a long-stable administrative geography (unchanged since
1980, when the last two -- O'Hare and Edgewater -- were added), so a
static mapping is appropriate rather than a live lookup.
"""

from typing import Optional

COMMUNITY_AREA_NAMES: dict[str, str] = {
    "1": "Rogers Park",
    "2": "West Ridge",
    "3": "Uptown",
    "4": "Lincoln Square",
    "5": "North Center",
    "6": "Lake View",
    "7": "Lincoln Park",
    "8": "Near North Side",
    "9": "Edison Park",
    "10": "Norwood Park",
    "11": "Jefferson Park",
    "12": "Forest Glen",
    "13": "North Park",
    "14": "Albany Park",
    "15": "Portage Park",
    "16": "Irving Park",
    "17": "Dunning",
    "18": "Montclare",
    "19": "Belmont Cragin",
    "20": "Hermosa",
    "21": "Avondale",
    "22": "Logan Square",
    "23": "Humboldt Park",
    "24": "West Town",
    "25": "Austin",
    "26": "West Garfield Park",
    "27": "East Garfield Park",
    "28": "Near West Side",
    "29": "North Lawndale",
    "30": "South Lawndale",
    "31": "Lower West Side",
    "32": "Loop",
    "33": "Near South Side",
    "34": "Armour Square",
    "35": "Douglas",
    "36": "Oakland",
    "37": "Fuller Park",
    "38": "Grand Boulevard",
    "39": "Kenwood",
    "40": "Washington Park",
    "41": "Hyde Park",
    "42": "Woodlawn",
    "43": "South Shore",
    "44": "Chatham",
    "45": "Avalon Park",
    "46": "South Chicago",
    "47": "Burnside",
    "48": "Calumet Heights",
    "49": "Roseland",
    "50": "Pullman",
    "51": "South Deering",
    "52": "East Side",
    "53": "West Pullman",
    "54": "Riverdale",
    "55": "Hegewisch",
    "56": "Garfield Ridge",
    "57": "Archer Heights",
    "58": "Brighton Park",
    "59": "McKinley Park",
    "60": "Bridgeport",
    "61": "New City",
    "62": "West Elsdon",
    "63": "Gage Park",
    "64": "Clearing",
    "65": "West Lawn",
    "66": "Chicago Lawn",
    "67": "West Englewood",
    "68": "Englewood",
    "69": "Greater Grand Crossing",
    "70": "Ashburn",
    "71": "Auburn Gresham",
    "72": "Beverly",
    "73": "Washington Heights",
    "74": "Mount Greenwood",
    "75": "Morgan Park",
    "76": "O'Hare",
    "77": "Edgewater",
}


def resolve_community_area_name(code: Optional[str]) -> Optional[str]:
    """The official name for a community area code, or `None` when
    `code` itself is `None` or isn't a recognized code -- callers
    should fall back to displaying the raw `code` in that case (see
    frontend/src/components/SummaryCards.tsx) rather than treating an
    unrecognized code as an error; the numeric code is always preserved
    separately alongside this resolved name (additive, not a
    replacement -- see NeighborhoodItem/SummaryResponse)."""
    if code is None:
        return None
    return COMMUNITY_AREA_NAMES.get(code)
