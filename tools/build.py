"""Build the site from the data.

Usage: python3 -I build.py TEMPLATE LEAFLET_CSS SHOWS_JSON BASEMAP_JSON OUT
OUT is a folder (or a path ending in .html). It gets index.html with the last few
weeks and everything upcoming built in, archive/<year>.json for older shows that
the page loads when you browse back, and shows.json with everything.
"""
import json, os, sys
from datetime import date, timedelta

tpl, css, shows_path, base_path, out = sys.argv[1:6]
out_dir, page = (os.path.dirname(out) or ".", out) if out.endswith(".html") else (out, os.path.join(out, "index.html"))
os.makedirs(os.path.join(out_dir, "archive"), exist_ok=True)

data = json.load(open(shows_path, encoding="utf-8"))
today = date.fromisoformat(data.get("scraped") or date.today().isoformat())
since = (today - timedelta(days=45)).isoformat()

SHOW_KEYS = ["id", "src", "date", "start", "title", "bands", "vkey", "loc", "locNote", "time", "door", "price",
             "priceKind", "notaflof", "age", "notes", "status", "kind", "flier", "link", "url", "also", "venueRaw", "city", "state"]
VENUE_KEYS = ["key", "name", "city", "state", "hidden", "private", "address", "aliases", "lat", "lng", "precision"]


def slim(s):
    o = {}
    for k in SHOW_KEYS:
        v = s.get(k)
        if v in (None, "", [], False) and k not in ("start", "price"):
            continue
        o[k] = v
    o.setdefault("bands", [])
    o.setdefault("age", "unknown")
    o.setdefault("priceKind", "unknown")
    o.setdefault("kind", "show")
    return o


shows = [slim(s) for s in data["shows"]]
venues = [{k: v.get(k) for k in VENUE_KEYS if v.get(k) not in (None, "", [])} for v in data["venues"]]
for v in venues:
    for k in ("name", "city", "state"):
        v.setdefault(k, "")
    v.setdefault("aliases", [])
years = sorted({int(s["date"][:4]) for s in shows})
recent = [s for s in shows if s["date"] >= since]
from_year = int(since[:4])

for y in years:
    part = [s for s in shows if s["date"].startswith(str(y))]
    json.dump({"year": y, "shows": part}, open(os.path.join(out_dir, "archive", f"{y}.json"), "w", encoding="utf-8"),
              separators=(",", ":"), ensure_ascii=False)
json.dump(data, open(os.path.join(out_dir, "shows.json"), "w", encoding="utf-8"), separators=(",", ":"), ensure_ascii=False)

embed = {"generated": data.get("generated"), "scraped": data.get("scraped"), "sources": data.get("sources", []),
         "years": years, "embeddedFrom": since, "embeddedYears": [y for y in years if y > from_year],
         "venues": venues, "shows": recent}
safe = lambda o: json.dumps(o, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
html = open(tpl, encoding="utf-8").read()
html = html.replace("/*__LEAFLET_CSS__*/", open(css, encoding="utf-8").read())
html = html.replace("/*__DATA__*/null", safe(embed))
html = html.replace("/*__BASE__*/null", safe(json.load(open(base_path, encoding="utf-8"))))
# The template is written as a page fragment (it also works as a Claude artifact). For the
# website, wrap it in a full HTML document: head tags up top, the rest in the body.
cut = html.index('<div class="app">')
head, body = html[:cut], html[cut:]
html = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<style>html{-webkit-text-size-adjust:100%}body{margin:0}img{max-width:100%}[hidden]{display:none!important}'
        ':root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}</style>\n'
        + head + '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
open(page, "w", encoding="utf-8").write(html)
print(f"{page}: {len(html)//1024} KB, {len(recent)} shows built in; archive years {years}; {len(shows)} shows in all")
