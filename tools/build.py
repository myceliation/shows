"""Assemble the single-file atlas page. Usage: python3 -I build.py TEMPLATE LEAFLET_CSS SHOWS_JSON BASEMAP_JSON OUT_HTML"""
import json, sys
tpl, css, shows, base, out = sys.argv[1:6]
html = open(tpl, encoding="utf-8").read()
html = html.replace("/*__LEAFLET_CSS__*/", open(css, encoding="utf-8").read())
safe = lambda o: json.dumps(o, separators=(",", ":")).replace("</", "<\\/")
html = html.replace("/*__DATA__*/null", safe(json.load(open(shows, encoding="utf-8"))))
html = html.replace("/*__BASE__*/null", safe(json.load(open(base, encoding="utf-8"))))
open(out, "w", encoding="utf-8").write(html)
print(out, len(html))
