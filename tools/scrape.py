"""Coolshows Atlas scraper.

Reads every source listed in DATA/sources.json, cleans the listings, matches
venue spellings, finds map positions, merges everything with the archive in
DATA/shows.json and writes it back.

Usage: python3 -I scrape.py DATA_DIR
"""
import csv, difflib, hashlib, html, io, json, math, re, sys, time, unicodedata
import urllib.parse, urllib.request, urllib.robotparser
from datetime import date, datetime, timezone

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # Windows without tzdata: fall back to a fixed offset
    ET = None

UA = "coolshows-atlas/2.0 (+https://myceliation.github.io/shows/)"
DATA = sys.argv[1]
NOW = datetime.now(ET) if ET else datetime.now()
TODAY = NOW.date()
STATE_NAMES = {"NJ": "New Jersey", "NY": "New York", "PA": "Pennsylvania", "DE": "Delaware", "CT": "Connecticut", "MD": "Maryland"}
REGION_BOX = "-80.6,42.6,-71.8,38.8"  # lon/lat box around NJ, NY, PA


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- fetching
_robots, _last_hit = {}, {}


def allowed(url):
    p = urllib.parse.urlparse(url)
    host = f"{p.scheme}://{p.netloc}"
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        _robots[host] = rp
    rp = _robots[host]
    return rp is None or rp.can_fetch(UA, url)


def fetch(url, check_robots=True, gap=1.0, tries=3):
    """GET a URL as text, obeying robots.txt and waiting `gap` seconds between hits on one host."""
    if check_robots and not allowed(url):
        raise PermissionError("robots.txt does not allow " + url)
    host = urllib.parse.urlparse(url).netloc
    wait = _last_hit.get(host, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=40) as r:
                body = r.read().decode("utf-8", "replace")
            _last_hit[host] = time.time()
            return body
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (400, 401, 403, 404, 410):
                break
        except Exception as e:
            err = e
        time.sleep(3 * (i + 1))
    raise err


# ---------------------------------------------------------------- text helpers
def clean(s):
    return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()


def strip_html(s):
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s, flags=re.I)
    return clean(re.sub(r"<[^>]+>", " ", s))


def fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def sha(s, n=10):
    return hashlib.sha1(s.encode()).hexdigest()[:n]


# ---------------------------------------------------------------- places
CITY_ALIASES = {
    "nb": "New Brunswick", "new bruns": "New Brunswick", "new brunswick nj": "New Brunswick",
    "philly": "Philadelphia", "phila": "Philadelphia", "phl": "Philadelphia", "philadelphia pa": "Philadelphia",
    "jc": "Jersey City", "bk": "Brooklyn", "bklyn": "Brooklyn", "brooklyn ny": "Brooklyn",
    "nyc": "New York City", "ny": "New York City", "new york": "New York City", "manhattan ny": "Manhattan",
    "lic": "Long Island City", "les": "Lower East Side", "the bronx": "Bronx", "bronx ny": "Bronx",
    "ap": "Asbury Park", "-": "", "n/a": "", "na": "", "?": "",
}
NYC_PLACES = {
    "new york city": "Manhattan", "manhattan": "Manhattan", "brooklyn": "Brooklyn", "queens": "Queens",
    "bronx": "Bronx", "staten island": "Staten Island",
    "ridgewood": "Queens", "astoria": "Queens", "long island city": "Queens", "jackson heights": "Queens",
    "flushing": "Queens", "sunnyside": "Queens", "woodside": "Queens", "maspeth": "Queens", "jamaica": "Queens",
    "bushwick": "Brooklyn", "williamsburg": "Brooklyn", "greenpoint": "Brooklyn", "bed-stuy": "Brooklyn",
    "bedford-stuyvesant": "Brooklyn", "crown heights": "Brooklyn", "gowanus": "Brooklyn", "sunset park": "Brooklyn",
    "park slope": "Brooklyn", "flatbush": "Brooklyn", "red hook": "Brooklyn", "clinton hill": "Brooklyn",
    "east williamsburg": "Brooklyn", "lower east side": "Manhattan", "east village": "Manhattan",
    "harlem": "Manhattan", "chinatown": "Manhattan", "soho": "Manhattan",
}
# Cities whose state is never in doubt; fixes rows filed under the wrong state.
CITY_STATE = {"new brunswick": "NJ", "jersey city": "NJ", "asbury park": "NJ", "hoboken": "NJ", "newark": "NJ",
              "philadelphia": "PA", "pittsburgh": "PA", "bethlehem": "PA", "allentown": "PA"}
CITY_STATE.update({k: "NY" for k in NYC_PLACES})


def norm_place(city, state):
    city = clean(city)
    city = CITY_ALIASES.get(city.lower().strip(" ."), city)
    state = clean(state).upper().strip(".")
    if len(state) != 2 or not state.isalpha():
        state = CITY_STATE.get(city.lower(), state[:2] if state else "")
    if city.lower() in CITY_STATE and CITY_STATE[city.lower()] != state:
        state = CITY_STATE[city.lower()]
    if not city and state == "NY":
        city = "New York City"
    return city, state


def metro(city, state):
    """Venue-matching area: all five boroughs count as one, elsewhere the city itself."""
    c = city.lower()
    return "nyc" if state == "NY" and c in NYC_PLACES else c


# ---------------------------------------------------------------- location notes
DM_RE = re.compile(
    r"\b(?:dm|d\.m\.|message|msg|text|email|e-mail|ask|hit (?:us|me|up)|inbox)\b[^.\n]{0,25}?\b(?:addy|address|addr|location|loc|deets)\b"
    r"|\bdms?\b[^.\n]{0,10}\b(?:info|details)\b"
    r"|\b(?:addy|address|location)\b[^.\n]{0,20}?\b(?:by|via|in|on|upon)\s+(?:dm|request|ig|instagram|insta|bio|rsvp)\b"
    r"|\bfigure it out\b|\bask a punk\b|\bsecret (?:location|spot|show)\b|\bundisclosed\b"
    r"|\b(?:info|address|location|addy)\s+(?:will be\s+)?(?:provided|given|posted|sent|announced|released)\b"
    r"|\b(?:location|address|venue)\s*(?:is\s*)?(?:tba|tbd|tbc)\b|^(?:tba|tbd|tbc)$", re.I)
NONE_RE = re.compile(r"^(?:|-+|n/?a|none|\?+|unknown|venue)$", re.I)
ADDR_RE = re.compile(
    r"^\d{1,5}[a-z]?(?:-\d{1,4})?\s+(?:[nsew]\.?\s+)?[\w.']+(?:\s+[\w.']+){0,3}\s+"
    r"(?:st|street|ave|av|avenue|rd|road|blvd|boulevard|pl|place|ln|lane|dr|drive|ct|court|sq|square|way|ter|terrace|pkwy|parkway|hwy|highway)\b\.?", re.I)


def location_note(venue_raw, other_text):
    """('hidden', phrase) when the address is only given out privately, ('none', None) when no venue
    is given, ('address', None) when the venue is a street address, else (None, None)."""
    v = clean(venue_raw)
    m = DM_RE.search(v)
    if m:
        return "hidden", v
    if NONE_RE.match(v):
        return "none", None
    m = DM_RE.search(other_text or "")
    if m:
        return "ask", m.group(0)
    if ADDR_RE.match(v):
        return "address", None
    return None, None


# ---------------------------------------------------------------- show details
CANCEL_RE = re.compile(r"\b(cancel+ed|postponed|rescheduled|called off)\b", re.I)
NOTAFLOF_RE = re.compile(r"notaflof|no ?one turned away|nobody turned away", re.I)
OTHER_RE = re.compile(
    r"\b(?:dj|djs|vj|karaoke|trivia|comedy|stand-?up|open mic|poetry|reading|book (?:launch|club)|workshop|class|"
    r"market|flea|brunch|screening|film night|movie|meeting|potluck|packing party|soul club|drag|burlesque|yoga|"
    r"craft|panel|talk|lecture|vigil|wordsmith|bingo|game night|art show|opening reception|exhibition)\b", re.I)


def parse_price(*texts):
    """(lowest price in dollars or None, kind) from free text: kind is free, paid, donation or unknown."""
    s = " ".join(t for t in texts if t).lower()
    amounts = [float(a) for a in re.findall(r"\$\s*(\d{1,3}(?:\.\d{2})?)", s)]
    amounts += [float(a) for a in re.findall(r"\b(\d{1,3})\s*(?:dollars|bucks)\b", s)]
    amounts += [float(a) for b in re.findall(r"\b(\d{1,2})\s*-\s*\d{1,2}\b(?=\s*(?:\$|sliding|donation|suggested))", s) for a in [b]]
    if amounts:
        return min(amounts), "paid"
    if re.search(r"\bfree\b", s):
        return 0, "free"
    if re.search(r"donat|pwyc|pay what|sliding|suggested", s) or NOTAFLOF_RE.search(s):
        return None, "donation"
    return None, "unknown"


def parse_age(*texts):
    s = " ".join(t for t in texts if t).lower()
    if re.search(r"\b21\s*\+|\b21 and (?:up|over)|\b21\s*plus|\bover 21\b", s):
        return "21+"
    if re.search(r"\b18\s*\+|\b18 and (?:up|over)|\b18\s*plus", s):
        return "18+"
    if re.search(r"\ball\s*ages\b|\ba/a\b", s):
        return "all ages"
    return "unknown"


TIME_RE = r"(\d{1,2})(?::([0-5]\d))?\s*(a\.?m\.?|p\.?m\.?)?"


def parse_start(*texts):
    """Minutes after midnight that the music starts, from free text like 'doors 7, music 8pm'."""
    s = " ".join(t for t in texts if t).lower()
    s = re.sub(r"\$\s*\d+(?:\.\d+)?", " ", s)            # prices that ended up in the time column
    s = re.sub(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", " ", s)  # dates
    s = re.sub(r"\b(1[0-9]|2[0-9])\s*\+", " ", s)          # ages
    if re.search(r"\bnoon\b", s) and not re.search(r"\d", s):
        return 12 * 60
    m = (re.search(r"(?:music|show|bands?|set|starts?|start time)\D{0,8}" + TIME_RE, s)
         or re.search(TIME_RE + r"\s*(?:music|show|bands?|start)", s))
    if not m:
        m = re.search(r"\b" + TIME_RE, s)
        if not m:
            return None
    h, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if h > 23:
        return None
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 11:
        h += 12  # shows are evening events; a bare "7" means 7pm
    return h * 60 + mm


def split_bands(text, is_title=False):
    text = clean(text)
    if is_title:  # event titles: "A w/ B + C", "A/B/C"
        text = re.sub(r"\s+(?:w/|with|feat\.?|featuring|\+)\s+", ", ", text, flags=re.I)
        if "," not in text and text.count("/") >= 1:
            text = re.sub(r"(?<=\S)/(?=\S)", ", ", text)
    parts = re.split(r",|\s/\s|\s\+\s|\n|\s•\s|\s\|\s", text)
    return [p.strip(" -–—") for p in parts if p.strip(" -–—")]


def flier_url(u):
    u = clean(u).replace(" ", "")
    if not u.startswith("http"):
        return ""
    m = re.match(r"https?://(?:www\.)?imgur\.com/([A-Za-z0-9]{5,8})$", u)  # imgur page link, not the image
    if m:
        return f"https://i.imgur.com/{m.group(1)}.jpeg"
    return u


# ---------------------------------------------------------------- stable ids
# Show ids from version 1 are kept so plans people saved keep working.
L_CITY = {"nb": "New Brunswick", "philly": "Philadelphia", "phila": "Philadelphia", "jc": "Jersey City", "bk": "Brooklyn", "-": "", "": ""}
L_VENUE = {"gold soudns": "Gold Sounds", "cousin dannys": "Cousin Danny's", "barge inn": "The Barge", "strung up ": "Strung Up"}
L_HIDDEN = re.compile(r"dm for|figure it out|ask a punk|tba|secret", re.I)


MOVED_RE = re.compile(r"^\s*moved to\s+(.+?)(?:,?\s+(?:was|from)\s+(?:at\s+|in\s+)?(.+))?$", re.I)
ROOM_RE = re.compile(r"(?:,\s*|\s+)(?:the\s+)?(upstairs|downstairs|basement|back ?room|rooftop|roof|patio|backyard|back yard|parking lot|front room|main room|side room)\s*$", re.I)
BANDLIST_RE = re.compile(r"\(([A-Z]{2}|[a-z ]+(?:core|punk|metal|emo|skramz|noise|rock|pop))[^)]*\)", re.I)


def repair_row(r):
    """Fix the common slips in hand-entered sheet rows. Returns (row, extra note)."""
    r = dict(r)
    note = []
    city, venue = clean(r.get("city")), clean(r.get("venue"))
    m = MOVED_RE.match(city)
    if m:
        city = m.group(1)
    m = MOVED_RE.match(venue)
    if m:
        venue = m.group(1)
        if m.group(2):
            note.append(f"Moved from {m.group(2)}")
    city = re.sub(r"\s*\([^)]*\)\s*", " ", city).strip()            # "Lake Como (next to Asbury Park)"
    city = re.sub(r"^mt\.?\s+", "Mount ", city, flags=re.I)
    if DM_RE.search(city):                                              # "Location TBA" typed into the city column
        note.append(city)
        city = ""
        if NONE_RE.match(venue):
            venue = "location tba"
    # columns slid one to the left: band list in the venue column, venue in the city column
    if not clean(r.get("bands")) and (venue.count(",") >= 2 or BANDLIST_RE.search(venue)):
        r["bands"], venue, city = venue, city, ""
    # venue and city swapped ("Footlight Presents" / "Queens")
    if venue.lower() in NYC_PLACES or venue.lower() in CITY_STATE:
        if city.lower() not in NYC_PLACES and city.lower() not in CITY_STATE:
            venue, city = city, venue
    # "Pet Shop, 193 Newark Ave" and "Pet Shop, upstairs"
    if "," in venue:
        head, tail = venue.split(",", 1)
        if ADDR_RE.match(tail.strip()) and len(head.strip()) >= 3:
            r["_address"] = tail.strip()
            venue = head.strip()
    rm = ROOM_RE.search(venue)
    if rm and len(venue[:rm.start()].strip()) >= 3:
        note.append(rm.group(1).capitalize())
        venue = venue[:rm.start()].strip(" ,")
    r["city"], r["venue"] = city, venue
    return r, "; ".join(note)


def legacy_id(row, y, m, d):
    c = lambda s: re.sub(r"\s+", " ", (s or "").strip())
    state = c(row.get("state")).upper()
    city_raw = c(row.get("city"))
    city = L_CITY.get(city_raw.lower(), city_raw)
    if not city and state == "NY":
        city = "Manhattan" if "night club 101" in (row.get("venue") or "").lower() else "New York City"
    venue_raw = c(row.get("venue"))
    venue = L_VENUE.get(venue_raw.lower(), venue_raw)
    vk = f"{state}|{city.lower()}|{'~hidden' if L_HIDDEN.search(venue) else venue.lower()}"
    return "s" + sha(f"{y}-{m}-{d}|{vk}|{row.get('bands', '')}")


# ---------------------------------------------------------------- one listing
def make_show(src, *, y, m, d, venue_raw, city, state, bands_text, title=None, time_text="", door_text="",
              notes_text="", flier="", link="", url="", start=None, sid, kind="show", address=None):
    city, state = norm_place(city, state)
    all_text = " ".join([venue_raw or "", bands_text or "", title or "", time_text, door_text, notes_text])
    loc, loc_note = location_note(venue_raw, " ".join([bands_text or "", time_text, door_text, notes_text]))
    price, price_kind = parse_price(door_text) if door_text else (None, "unknown")
    if price_kind == "unknown":
        price, price_kind = parse_price(time_text, notes_text, title or "")
    if start is None:
        start = parse_start(time_text) if re.search(r"\d|noon", time_text) else None
        if start is None and re.search(r"door|music|show|\d\s*(?:am|pm)", door_text, re.I):
            start = parse_start(door_text)
    age = parse_age(notes_text, door_text, time_text, title or "")
    notes = clean(notes_text)
    if notes.lower() in ("all ages", "21+", "18+", "aa", "-"):
        notes = ""
    status = None
    cm = CANCEL_RE.search(all_text)
    if cm:
        status = "postponed" if re.search(r"postpon|reschedul", cm.group(0), re.I) else "cancelled"
    bands = split_bands(bands_text, is_title=src != "coolshows") if bands_text else []
    if src != "coolshows" and len(notes) > 280:
        notes = notes[:277].rsplit(" ", 1)[0] + "…"
    return {
        "id": sid, "src": src, "date": f"{y:04d}-{m:02d}-{d:02d}", "start": start,
        "title": clean(title) if title else None, "bands": bands,
        "venueRaw": clean(venue_raw), "city": city, "state": state, "address": address,
        "loc": loc, "locNote": loc_note,
        "time": clean(time_text), "door": clean(door_text), "price": price, "priceKind": price_kind,
        "notaflof": bool(NOTAFLOF_RE.search(all_text)), "age": age, "notes": notes,
        "status": status, "kind": kind, "flier": flier_url(flier), "link": clean(link), "url": url,
    }


# ---------------------------------------------------------------- sources
def infer_year(month):
    y = TODAY.year
    if month < TODAY.month - 2:
        y += 1
    elif month > TODAY.month + 2:
        y -= 1
    return y


def valid_date(y, m, d):
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def read_coolshows(src):
    sheet = src["sheet"]
    view = fetch(f"https://docs.google.com/spreadsheets/d/{sheet}/htmlview")
    tabs = re.findall(r'\{name: "([^"]+)", pageUrl: "[^"]*", gid: "(\d+)"', view)
    pick = re.compile(src.get("tabs", r"^(current|upcoming|\d{4}Past)$"))
    tabs = [(n, g) for n, g in tabs if pick.match(n)] or [("current", "0")]
    shows = []
    for name, gid in tabs:
        text = fetch(f"https://docs.google.com/spreadsheets/d/{sheet}/export?format=csv&gid={gid}")
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            continue
        head = [h.strip().lower() for h in rows[0]]
        if head and not head[0]:
            head[0] = "month"  # one archive tab has a blank header over the month column
        ym = re.match(r"(\d{4})past", name.lower())
        n = 0
        for raw in rows[1:]:
            r = dict(zip(head, raw))
            try:
                m, d = int(r.get("month", "").strip()), int(r.get("day", "").strip())
            except ValueError:
                continue
            y = int(ym.group(1)) if ym else infer_year(m)
            if not valid_date(y, m, d):
                continue
            sid = legacy_id(r, y, m, d)   # computed from the row as typed, so ids never change
            fixed, extra = repair_row(r)
            notes = "; ".join(x for x in (clean(fixed.get("accessability")), extra) if x)
            shows.append(make_show(
                "coolshows", y=y, m=m, d=d, venue_raw=fixed.get("venue", ""), city=fixed.get("city", ""),
                state=fixed.get("state", ""), bands_text=fixed.get("bands", ""), time_text=fixed.get("time", ""),
                door_text=fixed.get("door", ""), notes_text=notes, flier=fixed.get("flier", ""),
                link=fixed.get("ig", ""), url="https://coolshows.life/", sid=sid, address=fixed.get("_address")))
            n += 1
        log(f"  coolshows tab {name}: {n} rows")
    return shows


def title_bands(title):
    t = clean(title)
    t = re.sub(r"^(?:(?:punk|hardcore|punk/hardcore|noise|metal|diy|benefit)\s+)*show\s*[–—:-]\s*", "", t, flags=re.I)
    t = re.sub(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*[–—:-]\s*", "", t)
    t = re.sub(r"^[^:]{3,60}(?:presents|benefit|fest|night)\s*:\s*", "", t, flags=re.I)
    return t


def local_dt(ms):
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.astimezone(ET) if ET else dt


def read_squarespace(src, since):
    """A Squarespace events page, read as the plain HTML page a visitor sees. (Squarespace's
    robots.txt asks bots to stay off its ?format=json and ?format=ical feeds, so those are not used.)"""
    page = fetch(src["url"].split("?")[0])
    root = re.match(r"https?://[^/]+", src["url"]).group(0)
    shows = []
    for art in re.findall(r'<article class="eventlist-event[^"]*".*?</article>', page, re.S):
        m = re.search(r'eventlist-title[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', art, re.S)
        day = re.search(r'<time class="event-date" datetime="(\d{4}-\d{2}-\d{2})"', art)
        if not m or not day:
            continue
        href, title = m.group(1), clean(strip_html(m.group(2)))
        y, mo, d = (int(x) for x in day.group(1).split("-"))
        if date(y, mo, d) < since:
            continue
        start = None
        g = re.search(r"dates=(\d{8}T\d{6})Z", art)  # the "add to Google Calendar" link carries the exact start in UTC
        if g:
            dt = datetime.strptime(g.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            dt = dt.astimezone(ET) if ET else dt
            if dt.date() == date(y, mo, d):
                start = dt.hour * 60 + dt.minute
        if start is None:
            t = re.search(r'<time class="event-time-(?:12hr|localized)(?:-start)?"[^>]*>([^<]+)<', art)
            start = parse_start(t.group(1).replace("\u202f", " ")) if t else None
        img = re.search(r'data-src="(https://images\.squarespace-cdn\.com/[^"]+)"', art)
        addr = re.search(r'maps\.google\.com\?q=([^"]+)"', art)
        excerpt = re.search(r'class="eventlist-excerpt"[^>]*>(.*?)</div>', art, re.S)
        shows.append(make_show(
            src["id"], y=y, m=mo, d=d, venue_raw=src["venue"], city=src["city"], state=src["state"],
            bands_text=title_bands(title), title=title, notes_text=strip_html(excerpt.group(1)) if excerpt else "",
            flier=img.group(1) if img else "", url=root + href, start=start,
            sid="v" + sha(src["id"] + "|" + href), kind="other" if OTHER_RE.search(title) else "show",
            address=clean(html.unescape(addr.group(1))).replace(" United States", "") if addr else None))
    return shows


def ics_events(text):
    text = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)END:VEVENT", text, re.S):
        ev = {}
        for line in block.split("\n"):
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            name, params = k.split(";")[0].upper(), k
            v = v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
            ev.setdefault(name, (v, params))
        yield ev


def ics_time(v, params):
    v = v.strip()
    if re.fullmatch(r"\d{8}", v):
        return datetime.strptime(v, "%Y%m%d"), False
    dt = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    if v.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc)
        return (dt.astimezone(ET) if ET else dt), True
    return dt, True  # floating or TZID time: venue feeds here use local Eastern time


KNOWN_CITIES = ["Brooklyn", "Queens", "Manhattan", "Bronx", "Staten Island", "New York", "Philadelphia",
                "Jersey City", "Hoboken", "Newark", "New Brunswick", "Asbury Park", "Ridgewood", "Paterson"]


def read_ics(src, since):
    shows = []
    for ev in ics_events(fetch(src["url"])):
        if "DTSTART" not in ev:
            continue
        dt, has_time = ics_time(*ev["DTSTART"])
        if dt.date() < since:
            continue
        title = clean(ev.get("SUMMARY", ("", ""))[0])
        location = clean(ev.get("LOCATION", ("", ""))[0])
        venue, city, address = src.get("venue") or "", src.get("city", ""), None
        if location and not src.get("venue"):
            parts = [p.strip() for p in location.split(",")]
            venue = parts[0]
            rest = [p for p in parts[1:] if p and p.lower() not in ("united states", "usa", "us")]
            for c in KNOWN_CITIES:
                if any(p.lower() == c.lower() for p in rest):
                    city = "New York City" if c == "New York" else c
                    break
            street = next((p for p in rest if re.match(r"^\d", p)), None)
            if street:
                address = f"{street}, {city}, {src.get('state', '')}"
        attach = ev.get("ATTACH", ("", ""))[0]
        desc = clean(ev.get("DESCRIPTION", ("", ""))[0])[:1500]
        uid = ev.get("UID", (title + dt.isoformat(), ""))[0]
        shows.append(make_show(
            src["id"], y=dt.year, m=dt.month, d=dt.day, venue_raw=venue, city=city, state=src.get("state", ""),
            bands_text=title_bands(title), title=title, notes_text=desc,
            flier=attach if re.search(r"\.(jpe?g|png|gif|webp)(\?|$)", attach, re.I) else "",
            url=clean(ev.get("URL", (src.get("home", ""), ""))[0]), start=dt.hour * 60 + dt.minute if has_time else None,
            sid="v" + sha(src["id"] + "|" + uid), kind="other" if OTHER_RE.search(title) else "show", address=address))
    return shows


# ---------------------------------------------------------------- venues
GENERIC_TAIL = re.compile(r"\s+(?:bar and restaurant|bar and grill|restaurant|bar|pub|lounge|nyc|bk|philly|venue|space)$")


def vnorm(name, city=""):
    s = fold(name).lower().replace("&", " and ")
    s = re.sub(r"[’'`.]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\bcoffee house\b", "coffeehouse", s)
    s = re.sub(r"\bstreet\b", "st", s)
    s = re.sub(r"\bavenue\b", "ave", s)
    s = re.sub(r"\bsouth\b", "s", s)
    s = re.sub(r"\bnorth\b", "n", s)
    s = re.sub(r"^the\s+", "", s)
    c = fold(city).lower()
    if c and s.endswith(" " + c) and len(s) > len(c) + 2:
        s = s[: -len(c) - 1]
    t = GENERIC_TAIL.sub("", s)
    return t if len(t) >= 3 else s


class Venues:
    def __init__(self, registry):
        self.registry = registry
        self.alias = {}
        for i, r in enumerate(registry):
            mk = metro(*norm_place(r["city"], r["state"]))
            for a in [r["name"]] + r.get("aliases", []):
                self.alias[(mk, vnorm(a, r["city"]))] = i
        self.groups = {}   # gkey -> dict

    def key_for(self, show):
        city, state = show["city"], show["state"]
        mk = metro(city, state)
        if show["loc"] in ("hidden", "none"):
            return f"{state}|{mk}|~{show['loc']}"
        n = vnorm(show["venueRaw"], city)
        reg = self.alias.get((mk, n))
        if reg is not None:
            return f"{state}|{mk}|{vnorm(self.registry[reg]['name'], self.registry[reg]['city'])}"
        return f"{state}|{mk}|{n}"

    def add(self, show):
        k = self.key_for(show)
        g = self.groups.setdefault(k, {"key": k, "state": show["state"], "metro": k.split("|")[1], "spellings": {},
                                       "cities": {}, "addresses": {}, "loc": show["loc"], "ask": 0})
        if show["loc"] == "ask":
            g["ask"] += 1
        g["spellings"][show["venueRaw"]] = g["spellings"].get(show["venueRaw"], 0) + 1
        g["cities"][show["city"]] = g["cities"].get(show["city"], 0) + 1
        if show.get("address"):
            g["addresses"][show["address"]] = g["addresses"].get(show["address"], 0) + 1
        show["vkey"] = k

    def fuzzy_merge(self):
        """Fold together spellings in one city that are nearly the same (typos)."""
        keys = sorted(self.groups, key=lambda k: -sum(self.groups[k]["spellings"].values()))
        remap = {}
        for i, a in enumerate(keys):
            if a in remap or "~" in a:
                continue
            for b in keys[i + 1:]:
                if b in remap or "~" in b or a.rsplit("|", 1)[0] != b.rsplit("|", 1)[0]:
                    continue
                na, nb = a.rsplit("|", 1)[1], b.rsplit("|", 1)[1]
                if min(len(na), len(nb)) < 5 or ADDR_RE.match(na) or ADDR_RE.match(nb):
                    continue
                if difflib.SequenceMatcher(None, na, nb).ratio() >= 0.9:
                    remap[b] = a
        return remap

    def apply(self, remap, shows):
        for b, a in remap.items():
            gb, ga = self.groups.pop(b), self.groups[a]
            for f in ("spellings", "cities", "addresses"):
                for s, n in gb[f].items():
                    ga[f][s] = ga[f].get(s, 0) + n
            ga["ask"] += gb["ask"]
        for s in shows:
            while s["vkey"] in remap:
                s["vkey"] = remap[s["vkey"]]

    def finish(self):
        out = {}
        for k, g in self.groups.items():
            name = max(g["spellings"], key=lambda s: (g["spellings"][s], not ADDR_RE.match(s), len(s)))
            city = max(g["cities"], key=lambda c: g["cities"][c])
            reg = None
            for (mk, n), i in self.alias.items():
                if f"{g['state']}|{mk}|{vnorm(self.registry[i]['name'], self.registry[i]['city'])}" == k:
                    reg = self.registry[i]
                    break
            if reg:
                name, city = reg["name"], reg["city"]
            hidden = g["loc"] in ("hidden", "none")
            if hidden:
                name = "Address not public" if g["loc"] == "hidden" else "Venue not listed"
            address = (reg or {}).get("address") or (max(g["addresses"], key=g["addresses"].get) if g["addresses"] else None)
            if not address and ADDR_RE.match(name):
                address = name
            out[k] = {"key": k, "name": name, "city": city, "state": g["state"], "hidden": hidden,
                      "private": bool(g.get("ask")) and not reg,
                      "address": address, "aliases": sorted(s for s in g["spellings"] if s != name),
                      "lat": (reg or {}).get("lat"), "lng": (reg or {}).get("lng")}
        return out


# ---------------------------------------------------------------- geocoding
try:
    CACHE = json.load(open(f"{DATA}/geocache.json", encoding="utf-8"))
except Exception:
    CACHE = {}


def api_json(url, gap):
    return json.loads(fetch(url, check_robots=False, gap=gap))


def km(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def cached(key, fn):
    if key not in CACHE:
        try:
            CACHE[key] = fn()
        except Exception as e:
            log("  geocoder error:", key, e)
            return None
    return CACHE[key]


def nominatim(q, limit=5):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": limit, "countrycodes": "us", "viewbox": REGION_BOX, "bounded": 1})
    return cached("nom5:" + q, lambda: [[float(r["lat"]), float(r["lon"]), r.get("name") or "", r.get("display_name", "")]
                                         for r in api_json(url, 1.1)])


def photon(q, near):
    url = "https://photon.komoot.io/api/?" + urllib.parse.urlencode({"q": q, "lat": near[0], "lon": near[1], "limit": 5})
    return cached("pho:" + q, lambda: [[f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0],
                                        f["properties"].get("name") or "", f["properties"].get("street") or ""]
                                       for f in api_json(url, 1.0).get("features", [])])


def census(address):
    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?" + urllib.parse.urlencode(
        {"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    def go():
        m = api_json(url, 0.5)["result"]["addressMatches"]
        return [m[0]["coordinates"]["y"], m[0]["coordinates"]["x"], m[0]["matchedAddress"]] if m else None
    return cached("cen:" + address, go)


def city_center(city, state):
    if not city:
        return None
    sn = STATE_NAMES.get(state, state)
    borough = NYC_PLACES.get(city.lower()) if state == "NY" else None
    q = (f"{city}, {borough}, New York" if borough and borough.lower() != city.lower() and city.lower() != "new york city"
         else "New York, New York" if city.lower() == "new york city" else f"{city}, {sn}")
    hits = nominatim(q, 1)
    return hits[0][:2] if hits else None


def name_ok(venue, found, city=""):
    a, b = vnorm(venue), vnorm(found or "")
    if not a or not b or b == vnorm(city) or set(b.split()) <= set(fold(city).lower().split()):
        return False  # the geocoder returned the town itself
    ta, tb = set(a.split()), set(b.split())
    if len(ta) == 1:
        return a == b  # one-word names ("Century", "The Attic") must match exactly
    if ta <= tb and len(ta & tb) / len(tb) >= 0.5:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.9


def locate(v):
    """Set lat/lng/precision on a venue: venue (exact place), address, city (pinned at the town center) or none."""
    center = city_center(v["city"], v["state"])
    near = lambda p: center is None or km(p, center) <= 25
    if v.get("lat") is not None:
        return v.update(precision="venue")
    if v["hidden"] or v.get("private"):  # address given out privately: never guess a spot
        return v.update(lat=center[0] if center else None, lng=center[1] if center else None,
                        precision="city" if center else "none")
    if v.get("address"):
        addr = v["address"]
        if not re.search(r"\b(NJ|NY|PA)\b|New Jersey|New York|Pennsylvania", addr):
            addr = f"{addr}, {v['city']}, {v['state']}"
        hit = census(addr)
        if hit and near(hit[:2]):
            return v.update(lat=hit[0], lng=hit[1], precision="address")
    sn = STATE_NAMES.get(v["state"], v["state"])
    if not ADDR_RE.match(v["name"]):
        one_word = len(vnorm(v["name"]).split()) == 1
        nom = [h for h in (nominatim(f"{v['name']}, {v['city']}, {sn}") or [])
               if near(h[:2]) and name_ok(v["name"], h[2] or h[3].split(",")[0], v["city"])]
        pho = [h for h in (photon(f"{v['name']} {v['city']}", center) if center else []) or []
               if near(h[:2]) and name_ok(v["name"], h[2], v["city"])]
        if one_word:
            # A one-word name ("Century") can match an unrelated business, so it has to sit within
            # 12 km of the town center. Wrong pins can be corrected in data/venues.json.
            nom = [h for h in nom if center is None or km(h[:2], center) <= 12]
            pho = [h for h in pho if center is None or km(h[:2], center) <= 12]
        if nom or pho:
            h = (nom or pho)[0]
            return v.update(lat=h[0], lng=h[1], precision="venue")
    if center:
        return v.update(lat=center[0], lng=center[1], precision="city")
    v.update(lat=None, lng=None, precision="none")


def fix_cities(shows):
    """Typos in town names ("Felmington", "Brooklkyn") and odd capitals ("lakewood") are matched to the
    usual spelling. A name the geocoder knows is left alone, so real places like West Long Branch stay."""
    count = {}
    for s in shows:
        if s["city"]:
            count[(s["state"], s["city"])] = count.get((s["state"], s["city"]), 0) + 1
    by_fold = {}
    for (st, c), n in count.items():
        by_fold.setdefault((st, c.lower()), []).append((n, c))
    best_case = {k: max(v)[1] for k, v in by_fold.items()}
    common = {}
    for (st, low), c in best_case.items():
        n = sum(count[(st, x)] for _, x in by_fold[(st, low)])
        if n >= 3:
            common.setdefault(st, []).append(c)
    fix = {}
    for (st, c) in count:
        good = best_case[(st, c.lower())]
        if good != c:
            fix[(st, c)] = good
        if good in common.get(st, []) or city_center(good, st):
            continue
        known = set(common.get(st, [])) | {x for (st2, _), x in best_case.items() if st2 == st and x != good and city_center(x, st)}
        m = difflib.get_close_matches(good, sorted(known), 1, 0.8)
        if m:
            fix[(st, c)] = m[0]
    for s in shows:
        s["city"] = fix.get((s["state"], s["city"]), s["city"])
    # a listing with no town gets the town its venue has everywhere else
    town = {}
    for s in shows:
        if s["city"] and s["loc"] not in ("hidden", "none"):
            k = (s["state"], vnorm(s["venueRaw"]))
            town.setdefault(k, {}).setdefault(s["city"], 0)
            town[k][s["city"]] += 1
    for s in shows:
        if not s["city"] or (s["state"] == "NY" and s["city"] == "New York City"):
            t = town.get((s["state"], vnorm(s["venueRaw"])))
            if t:
                s["city"] = max(t, key=t.get)
    if fix:
        log(f"  fixed {len(fix)} town spellings: " + ", ".join(f"{a[1]} -> {b}" for a, b in list(fix.items())[:12]))


# ---------------------------------------------------------------- merging sources
def band_set(s):
    return {vnorm(b) for b in s["bands"] if len(vnorm(b)) >= 3}


SRC_RANK = {"coolshows": 0}


def dedupe(shows):
    """One record per real show: listings from different sources for the same night at the same venue
    are folded into the coolshows entry (or the first one), keeping every source link."""
    groups = {}
    for s in shows:
        groups.setdefault((s["date"], s["vkey"]), []).append(s)
    out = []
    for (_, vkey), group in groups.items():
        if "~" in vkey or len(group) == 1:
            out += group
            continue
        group.sort(key=lambda s: SRC_RANK.get(s["src"], 1))
        kept = []
        for s in group:
            target = None
            others = [k for k in kept if k["src"] != s["src"] and k["kind"] == s["kind"]]
            for k in others:
                if band_set(k) & band_set(s):
                    target = k
                    break
            if target is None and len(others) == 1 and sum(1 for g in group if g["src"] == s["src"] and g["kind"] == s["kind"]) == 1:
                target = others[0]
            if target is None:
                kept.append(s)
                continue
            target.setdefault("also", []).append({"src": s["src"], "url": s["url"]})
            for f in ("flier", "start", "address"):
                if not target.get(f) and s.get(f):
                    target[f] = s[f]
            if target["priceKind"] == "unknown" and s["priceKind"] != "unknown":
                target["price"], target["priceKind"] = s["price"], s["priceKind"]
            if target["age"] == "unknown":
                target["age"] = s["age"]
        out += kept
    return out


def from_archive(old):
    """Turn a version-1 archive record back into a raw listing so it goes through today's cleanup."""
    y, m, d = (int(x) for x in old["date"].split("-"))
    venue = old.get("venueRaw") or old.get("venue", "")
    if venue == "Address by DM":
        venue = "dm for address"
    return make_show(old.get("src", "coolshows"), y=y, m=m, d=d, venue_raw=venue, city=old.get("city", ""),
                     state=old.get("state", ""), bands_text=", ".join(old.get("bands", [])), title=old.get("title"),
                     time_text=old.get("time", ""), door_text=old.get("door", ""),
                     notes_text=old.get("notes", old.get("ageRaw", "")), flier=old.get("flier", ""),
                     link=old.get("link", ""), url=old.get("url", "https://coolshows.life/"), start=old.get("start"),
                     sid=old["id"], kind=old.get("kind", "show"), address=old.get("address"))


# ---------------------------------------------------------------- main
def main():
    cfg = json.load(open(f"{DATA}/sources.json", encoding="utf-8"))
    try:
        registry = json.load(open(f"{DATA}/venues.json", encoding="utf-8"))["venues"]
    except Exception:
        registry = []
    try:
        prev = json.load(open(f"{DATA}/shows.json", encoding="utf-8"))
    except Exception:
        prev = {"shows": [], "venues": []}

    since = date(2023, 1, 1)
    shows, status = [], []
    for src in cfg["sources"]:
        if src.get("enabled") is False:
            continue
        log(f"reading {src['name']} ({src['type']})")
        try:
            got = {"coolshows": read_coolshows, "squarespace": lambda s: read_squarespace(s, since),
                   "ics": lambda s: read_ics(s, since)}[src["type"]](src)
            status.append({"id": src["id"], "name": src["name"], "home": src.get("home"), "ok": True,
                           "checked": NOW.isoformat(timespec="minutes"), "count": len(got),
                           "upcoming": sum(1 for s in got if s["date"] >= TODAY.isoformat())})
            shows += got
            log(f"  {len(got)} listings")
        except Exception as e:
            log(f"  failed: {e}")
            old = next((x for x in prev.get("sources", []) if x.get("id") == src["id"]), {})
            status.append({"id": src["id"], "name": src["name"], "home": src.get("home"), "ok": False,
                           "error": str(e)[:200], "checked": NOW.isoformat(timespec="minutes"),
                           "lastGood": old.get("checked") if old.get("ok") else old.get("lastGood")})
    seen, uniq = set(), []
    for s in shows:  # a row can sit in both the current tab and an archive tab
        if s["id"] not in seen:
            seen.add(s["id"])
            uniq.append(s)
    shows = uniq
    if not any(s["src"] == "coolshows" for s in shows):
        log("coolshows could not be read; keeping the previous data unchanged")
        sys.exit(1)

    # Keep earlier listings that no source shows any more, but only for dates already past
    # (an upcoming show that vanished was most likely cancelled or moved).
    have = {s["id"] for s in shows}
    failed = {x["id"] for x in status if not x["ok"]}
    for old in prev.get("shows", []):
        if old["id"] in have:
            continue
        if old["date"] < TODAY.isoformat() or old.get("src", "coolshows") in failed:
            try:
                shows.append(from_archive(old))
                have.add(old["id"])
            except Exception as e:
                log("  skipped archive row", old.get("id"), e)

    fix_cities(shows)
    ven = Venues(registry)
    for s in shows:
        ven.add(s)
    ven.apply(ven.fuzzy_merge(), shows)
    venues = ven.finish()

    log(f"locating {len(venues)} venues")
    for i, v in enumerate(venues.values()):
        locate(v)
        if i % 50 == 49:
            json.dump(CACHE, open(f"{DATA}/geocache.json", "w", encoding="utf-8"), indent=0)
    json.dump(CACHE, open(f"{DATA}/geocache.json", "w", encoding="utf-8"), indent=0)

    # Venues that land on the same spot are the same place under two names (e.g. a name and its street address).
    spots = {}
    remap = {}
    for k, v in sorted(venues.items(), key=lambda kv: -len(kv[1]["aliases"])):
        if v["precision"] not in ("venue", "address") or v["hidden"]:
            continue
        spot = (v["state"], round(v["lat"], 4), round(v["lng"], 4))
        if spot in spots and spots[spot] != k:
            remap[k] = spots[spot]
        else:
            spots[spot] = k
    for b, a in remap.items():
        vb, va = venues.pop(b), venues[a]
        if ADDR_RE.match(va["name"]) and not ADDR_RE.match(vb["name"]):
            va["name"], vb["name"] = vb["name"], va["name"]
        va["aliases"] = sorted(set(va["aliases"] + vb["aliases"] + [vb["name"]]) - {va["name"]})
        if va["precision"] == "venue" and vb["precision"] == "address":
            va["precision"] = "address"
    for s in shows:
        s["vkey"] = remap.get(s["vkey"], s["vkey"])

    shows = dedupe(shows)
    for s in shows:
        s["venue"] = venues[s["vkey"]]["name"]
        if s["loc"] == "hidden":
            s["locNote"] = s["locNote"] or s["venueRaw"]

    used = {s["vkey"] for s in shows}
    out = {"generated": NOW.isoformat(timespec="minutes"), "scraped": TODAY.isoformat(), "sources": status,
           "shows": sorted(shows, key=lambda s: (s["date"], s["start"] if s["start"] is not None else 2000, s["id"])),
           "venues": sorted((v for k, v in venues.items() if k in used), key=lambda v: v["key"])}
    json.dump(out, open(f"{DATA}/shows.json", "w", encoding="utf-8"), indent=0, ensure_ascii=False)
    prec = {}
    for v in out["venues"]:
        prec[v["precision"]] = prec.get(v["precision"], 0) + 1
    up = sum(1 for s in shows if s["date"] >= TODAY.isoformat())
    log(f"{len(shows)} shows ({up} upcoming), {len(out['venues'])} venues {prec}")


if __name__ == "__main__":
    main()
