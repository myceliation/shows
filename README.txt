COOLSHOWS ATLAS -> https://myceliation.github.io/shows/

How it stays current, with nothing running on your PC:
  - The page reads the coolshows.life sheet itself every time someone opens it.
  - A GitHub Action (.github/workflows/refresh.yml) runs every morning:
    it rescrapes, looks up exact spots for new venues, saves past shows that
    coolshows' rolling calendar drops (data/shows.json), rebuilds the page and
    deploys it. It also runs on every push, and on demand from the repo's
    Actions tab ("Refresh shows" > "Run workflow").

One-time setup (needs Git for Windows):
  Double-click deploy-shows.bat. If the "shows" repo doesn't exist yet it
  offers to install GitHub CLI (via winget), has you sign in to GitHub once in
  the browser, creates the public repo, turns on Pages, and pushes everything.
  The site is live at the address above a couple of minutes later; progress is
  on the repo's Actions tab. If you'd rather not install GitHub CLI, answer N
  and create an empty public "shows" repo at https://github.com/new yourself,
  then set Settings > Pages > Source to "GitHub Actions" after the push.

Folders
  tools\     scrape.py (pulls the sheet, geocodes venues), build.py (assembles
             the single-file page), template.html, leaflet.css
  data\      shows.json (every show seen, past ones kept), geocache.json (saved
             venue lookups), basemap.json (state and county lines)
  .github\   the refresh-and-deploy Action

GitHub pauses scheduled Actions in a repo with no activity for 60 days. The
Action's own commits count as activity, so it only pauses if coolshows goes
quiet that long; re-enable it from the Actions tab if that happens.
