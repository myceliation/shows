"""Scrape coolshows.life (Google Sheet via opensheet), normalize, geocode venues.

Usage: python3 -I scrape.py OUT_DIR
Writes OUT_DIR/shows.json and keeps OUT_DIR/geocache.json between runs.
"""
import hashlib, json, math, re, sys, time, urllib.parse, urllib.request
from datetime import date

SHEET = "https://opensheet.elk.sh/1vkGXNAsJAb4BDYOHGgpi-HAydM7RVgssoBZfFsH0ark/current"
UA = "coolshows-map/1.0 (personal show planner; myceliation)"
OUT = sys.argv[1]

CITY_ALIASES = {"nb": "New Brunswick", "philly": "Philadelphia", "phila": "Philadelphia",
                "jc": "Jersey City", "bk": "Brooklyn", "-": "", "": ""}
VENUE_ALIASES = {"gold soudns": "Gold Sounds", "cousin dannys": "Cousin Danny's",
                 "barge inn": "The Barge", "strung up ": "Strung Up"}
HIDDEN = re.compile(r"dm for|figure it out|ask a punk|tba|secret", re.I)
# NYC boroughs/neighborhoods geocode better with the borough as the "city"
STATE_NAMES = {"NJ": "New Jersey", "NY": "New York", "PA": "Pennsylvania"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def infer_year(month, today):
    # The live calendar runs from today to the end of next month; rows from
    # earlier months than (today - 2 months) belong to next year (Dec→Jan rollover).
    y = today.year
    if month < today.month - 2:
        y += 1
    elif month > today.month + 2:
        y -= 1
    return y


def parse_price(s):
    s = (s or "").lower()
    if not s or s in {"-", "?", "tba"}:
        return None, "unknown"
    if "free" in s:
        return 0, "free"
    nums = [float(n) for n in re.findall(r"\$?\s*(\d+(?:\.\d+)?)", s)]
    if nums:
        return min(nums), "paid"
    if "donat" in s or "pwyc" in s or "sliding" in s:
        return None, "donation"
    return None, "unknown"


def parse_age(s):
    s = (s or "").lower()
    if "21" in s:
        return "21+"
    if "18" in s:
        return "18+"
    if "all" in s:
        return "all ages"
    return "unknown"


def parse_start(s):
    """Return minutes after midnight for music start (fallback: first time listed)."""
    s = (s or "").lower()
    s = re.sub(r"\$\s*\d+", "", s)  # prices sometimes land in the time column
    times = re.findall(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not times:
        return None
    m = re.search(r"(music|show|bands?)\D{0,6}(\d{1,2})(?::(\d{2}))?", s)
    if m:
        h, mm = int(m.group(2)), int(m.group(3) or 0)
    else:
        h, mm = int(times[0][0]), int(times[0][1] or 0)
    # Shows are evening events; read bare 1-11 as pm unless "am" is written
    if 1 <= h <= 11 and not re.search(r"\d\s*am\b", s):
        h += 12
    return h * 60 + mm


STOP = {"the", "of", "and", "&", "nyc", "llc", "inc"}


def toks(s):
    s = s.lower().replace("st.", "street")
    t = [w for w in re.split(r"[^a-z0-9']+", s) if w and w not in STOP]
    return {"street" if w == "st" else w for w in t}


def plausible(venue, match):
    """Reject Nominatim hits whose name doesn't actually match the venue."""
    first = match.split(",")[0]
    if re.match(r"^\d", venue):  # street address: house number must match
        return first.strip() == venue.split()[0]
    vt, mt = toks(venue), toks(first)
    return bool(vt) and vt <= mt and len(vt & mt) / max(len(mt), 1) >= 0.5


def km(a, b):
    R = 6371
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


try:
    cache = json.load(open(f"{OUT}/geocache.json", encoding="utf-8"))
except Exception:
    cache = {}


def nominatim(q):
    if q in cache:
        return cache[q]
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "us",
         "viewbox": "-80.6,42.6,-71.8,38.8", "bounded": 1})
    time.sleep(1.1)  # Nominatim usage policy: max 1 req/s
    try:
        res = get(url)
        hit = [float(res[0]["lat"]), float(res[0]["lon"]), res[0].get("display_name", "")] if res else None
    except Exception as e:
        print("geocode error", q, e, file=sys.stderr)
        hit = None
    cache[q] = hit
    return hit


def main():
    today = date.today()
    rows = get(SHEET)
    shows, venues = [], {}
    for i, r in enumerate(rows):
        try:
            m, d = int(r.get("month", "")), int(r.get("day", ""))
        except ValueError:
            continue
        state = clean(r.get("state")).upper()
        city_raw = clean(r.get("city"))
        city = CITY_ALIASES.get(city_raw.lower(), city_raw)
        if not city and state == "NY" and "night club 101" in (r.get("venue") or "").lower():
            city = "Manhattan"
        elif not city and state == "NY":
            city = "New York City"
        venue_raw = clean(r.get("venue"))
        venue = VENUE_ALIASES.get(venue_raw.lower(), venue_raw)
        hidden = bool(HIDDEN.search(venue))
        y = infer_year(m, today)
        price, price_kind = parse_price(r.get("door"))
        vkey = f"{state}|{city.lower()}|{venue.lower() if not hidden else '~hidden'}"
        venues.setdefault(vkey, {"key": vkey, "state": state, "city": city,
                                 "venue": "Address by DM" if hidden else venue,
                                 "hidden": hidden})
        shows.append({
            "id": "s" + hashlib.sha1(f"{y}-{m}-{d}|{vkey}|{r.get('bands','')}".encode()).hexdigest()[:10],
            "date": f"{y:04d}-{m:02d}-{d:02d}",
            "bands": [b.strip() for b in re.split(r",|\s/\s|\n", r.get("bands", "")) if b.strip()],
            "venue": venues[vkey]["venue"], "city": city, "state": state, "vkey": vkey,
            "time": clean(r.get("time")), "start": parse_start(r.get("time")),
            "door": clean(r.get("door")), "price": price, "priceKind": price_kind,
            "age": parse_age(r.get("accessability")), "ageRaw": clean(r.get("accessability")),
            "flier": clean(r.get("flier")), "link": clean(r.get("IG")),
        })

    for v in venues.values():
        cityq = f"{v['city']}, {STATE_NAMES.get(v['state'], v['state'])}" if v["city"] else STATE_NAMES.get(v["state"], v["state"])
        center = nominatim(cityq) if v["city"] else None
        hit = None
        if not v["hidden"]:
            hit = nominatim(f"{v['venue']}, {cityq}")
            # reject venue hits that land far from the stated city (wrong match)
            if hit and center and km(hit[:2], center[:2]) > 25:
                hit = None
            if hit and not plausible(v["venue"], hit[2]):
                hit = None
        if hit:
            v.update(lat=hit[0], lng=hit[1], precision="venue", match=hit[2])
        elif center:
            v.update(lat=center[0], lng=center[1], precision="city", match=center[2])
        else:
            v.update(lat=None, lng=None, precision="none", match="")

    # Keep shows that have rolled off coolshows' live calendar (it only lists
    # today → end of next month), so the atlas accumulates its own archive.
    try:
        prev = json.load(open(f"{OUT}/shows.json", encoding="utf-8"))
    except Exception:
        prev = {"shows": [], "venues": []}
    ids = {s["id"] for s in shows}
    carried = [s for s in prev["shows"] if s["id"] not in ids and s["date"] < today.isoformat()]
    shows.extend(carried)
    for pv in prev["venues"]:
        if pv["key"] not in venues and any(s["vkey"] == pv["key"] for s in carried):
            venues[pv["key"]] = pv

    json.dump(cache, open(f"{OUT}/geocache.json", "w", encoding="utf-8"), indent=1)
    out = {"source": "https://coolshows.life/", "scraped": today.isoformat(),
           "shows": sorted(shows, key=lambda s: (s["date"], s["start"] or 0)),
           "venues": list(venues.values())}
    json.dump(out, open(f"{OUT}/shows.json", "w", encoding="utf-8"), indent=1)
    prec = {}
    for v in venues.values():
        prec[v["precision"]] = prec.get(v["precision"], 0) + 1
    print(len(shows), "shows (", len(carried), "carried from earlier scrapes ),", len(venues), "venues", prec)


main()
