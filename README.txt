COOLSHOWS ATLAS -> https://myceliation.github.io/shows/

A map and calendar of DIY shows in New Jersey, New York and Pennsylvania.

WHERE THE LISTINGS COME FROM
  - coolshows.life: its Google Sheet, including the archive tabs back to 2023.
  - Venue calendars: ABC No Rio, The Broadway, Mama Tried, Property Is Theft,
    Khyber Pass and Prototype 237.
  The list is in data/sources.json. To add a calendar, copy an entry and change
  it. Supported types: "ics" (any public iCalendar feed) and "squarespace" (a
  Squarespace events page). The scraper obeys each site's robots.txt and skips
  any source whose site asks bots to stay away.
  Not included: nyc-noise.com, Philly Happenings, phillymetal.com, DICE and
  most Squarespace data feeds, because their robots.txt files ask automated
  tools (or AI tools in particular) not to collect them.

HOW IT STAYS CURRENT (nothing runs on your PC)
  - A GitHub Action (.github/workflows/refresh.yml) runs every morning. It reads
    every source, cleans the listings, places new venues on the map, saves the
    archive to data/shows.json, rebuilds the site and deploys it. It also runs
    on every push and from the Actions tab ("Refresh shows" > "Run workflow").
  - The page re-reads the coolshows sheet each time someone opens it, so shows
    added during the day show up right away.

WHAT THE CLEANUP DOES
  - "dm for address", "ask a punk" and similar notes are kept and shown. Those
    shows are placed at the center of the town, never at a guessed address.
  - Spellings of one venue are merged ("Maggie's" / "Maggies Bar & Restaurant",
    "The P.I.T." / "411 S 5th St"). Fixes go in data/venues.json.
  - Prices, start times and ages are read even when they sit in the wrong column.
  - Cancelled and postponed shows are marked rather than silently dropped.
  - The same show listed by coolshows and a venue is shown once, with both links.
  - Venue positions come from OpenStreetMap (Nominatim, Photon) and the U.S.
    Census geocoder for street addresses. Unknown spots sit at the town center
    and are drawn with a dashed outline.

UPDATING THE SITE CODE
  Double-click deploy-shows.bat (needs Git for Windows). It copies the tools,
  settings and Action into %USERPROFILE%\coolshows-site and pushes them. The
  show archive in the repo is kept. First time only: it can create the repo
  and turn on Pages using GitHub CLI.

FOLDERS
  tools\     scrape.py (reads and cleans sources), build.py (builds the site),
             template.html (the page), leaflet.css (map styles)
  data\      sources.json, venues.json (your settings); shows.json (archive),
             geocache.json (saved map lookups), basemap.json (state outlines)
  .github\   the daily refresh-and-deploy Action

GitHub pauses scheduled Actions in a repo with no commits for 60 days. The
Action commits the archive whenever listings change, so that only happens if
coolshows goes quiet that long; turn it back on from the Actions tab.
