import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
# UF_DATA_DIR / UF_OUTPUT_DIR let the packaged launcher (launcher.py) keep writable data in a
# per-user folder; unset (start.sh / start.bat / Docker) they default to ./data and ./output.
DATA = Path(os.environ["UF_DATA_DIR"]).expanduser() if os.environ.get("UF_DATA_DIR") else ROOT / "data"
STATIC = DATA / "static"
SNAPSHOTS = DATA / "snapshots"
CACHE = DATA / "cache"
OUTPUT = Path(os.environ["UF_OUTPUT_DIR"]).expanduser() if os.environ.get("UF_OUTPUT_DIR") else ROOT / "output"
METRICS_CSV = DATA / "daily_metrics.csv"

TZ = ZoneInfo("Europe/Paris")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

DEEPSTATE_HISTORY = "https://deepstatemap.live/api/history/public"
DEEPSTATE_GEOJSON = "https://deepstatemap.live/api/history/{id}/geojson"
DEEPSTATE_VIEW = "https://deepstatemap.live/en#{z}/{lat}/{lon}"

NE_COUNTRIES = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries_ukr.zip"
GEONAMES_UA = "https://download.geonames.org/export/dump/UA.zip"
GEONAMES_ADMIN1 = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"

# ISW's own site sits behind a Cloudflare challenge for scripted clients; the
# same reports are co-published by AEI's Critical Threats Project (ISW partner).
CTP_BASE = "https://www.criticalthreats.org"
CTP_LIST = CTP_BASE + "/analysis"
ISW_URL_GUESS = "https://understandingwar.org/research/russia-ukraine/russian-offensive-campaign-assessment-{slugdate}/"
GNEWS_ISW = ("https://news.google.com/rss/search?q=%22Russian+Offensive+Campaign+Assessment%22"
             "&hl=en-US&gl=US&ceid=US:en")

NEWS_FEEDS = [
    ("Kyiv Independent", "https://kyivindependent.com/news-archive/rss/", False),
    ("BBC News Europe", "https://feeds.bbci.co.uk/news/world/europe/rss.xml", True),
    ("Google News 搜索 (Ukraine war, 含 Reuters 等)",
     "https://news.google.com/rss/search?q=Ukraine+war+Reuters+when:2d&hl=en-US&gl=US&ceid=US:en", True),
]
NEWS_KEYWORDS = ("ukrain", "russia", "kyiv", "kremlin", "putin", "zelensk", "donetsk",
                 "kharkiv", "zaporizh", "front", "drone", "missile")

# Area computations: World Cylindrical Equal Area (metres)
EQUAL_AREA_CRS = "EPSG:6933"
# Local metric CRS for distances / buffering around Ukraine (UTM 37N covers the front)
LOCAL_CRS = "EPSG:32637"

# Changes smaller than this (km^2) are treated as drawing noise in the "top areas" list
MIN_CHANGE_KM2 = 0.05
SECTOR_CLUSTER_KM = 15.0
