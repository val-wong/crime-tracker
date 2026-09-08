// Chicago community-area code -> official name, mirroring
// backend/app/chicago_community_areas.py (kept as a small duplicated
// static table rather than an extra API round-trip -- see that
// module's docstring for the authoritative source: City of Chicago
// Data Portal, "Boundaries - Community Areas" dataset, igwz-8jzy).
//
// Used to populate the neighborhood filter's dropdown with official
// names while still sending the numeric code to the API/URL.

export const COMMUNITY_AREA_NAMES: Record<string, string> = {
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
};

export interface CommunityAreaOption {
  code: string;
  name: string;
}

// All 77 official community areas, sorted alphabetically by name --
// used to populate the neighborhood filter dropdown so a user can pick
// by name without knowing the underlying numeric code.
export const COMMUNITY_AREA_OPTIONS: CommunityAreaOption[] = Object.entries(COMMUNITY_AREA_NAMES)
  .map(([code, name]) => ({ code, name }))
  .sort((a, b) => a.name.localeCompare(b.name));

export function resolveCommunityAreaName(code: string | null | undefined): string | null {
  if (!code) return null;
  return COMMUNITY_AREA_NAMES[code] ?? null;
}
