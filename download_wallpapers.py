#!/usr/bin/env python3
"""Télécharge un fond d'écran par capitale depuis Wikimedia Commons.

Le script récupère les 193 États membres de l'ONU depuis Wikidata, ajoute les
deux États observateurs, puis cherche pour chaque capitale une image ancrée sur
la ville: catégories de vues de Commons, catégorie de la ville, recherche par
coordonnées. Il sélectionne une image paysage proche du 16:9, la convertit en
JPEG 2560x1440 par défaut, puis écrit un manifeste avec les crédits et licences.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests
from PIL import Image, ImageOps
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
COMMONS_API_URL = "https://commons.wikimedia.org/w/api.php"
TARGET_RATIO = 16 / 9
EXPECTED_STATE_COUNT = 195  # 193 membres de l'ONU + Saint-Siège + État de Palestine

# Rayon de la recherche par coordonnées autour du centre de la capitale.
GEOSEARCH_RADIUS_M = 7000

# Un fond d'écran doit remplir l'écran sans être trop interpolé. Descendre ce
# plancher laissait remonter de vieilles vues nocturnes de 1800 px, presque
# noires une fois agrandies: mieux vaut refuser et laisser un choix imposé.
MIN_WIDTH = 1920
MIN_PIXELS = 2_300_000
MIN_RATIO = 1.3

# Au-delà de ce rapport, l'image n'est plus une vue mais une frise: même si le
# recadrage garde assez de pixels, le cadrage d'origine n'a plus de sens.
MAX_COMFORTABLE_RATIO = 3.0

# Les distinctions se cumulent et se recoupent: « image de l'année » implique
# « image remarquable ». Sans plafond, un quadruple lauréat écrase la pertinence.
MAX_ASSESSMENT_BONUS = 120

# Commons fait évaluer ses images par sa communauté. Ces distinctions, exposées
# par extmetadata.Assessments, restent un bon indice de beauté, mais elles pèsent
# moins qu'avant: la pertinence est désormais garantie par l'ancrage sur la
# ville, ce n'est plus au palmarès de la porter.
ASSESSMENT_BONUS = {
    "featured": 100,  # image remarquable, élue au vote
    "quality": 35,  # qualité technique validée par un relecteur
    "valued": 25,  # meilleure illustration de son sujet
    "poty": 50,  # image de l'année
    "potd": 20,  # image du jour
}

# Provenance du candidat, par ordre de confiance. Une image tirée d'une catégorie
# « Views of X » montre la ville par construction, là où un rayon de sept
# kilomètres attrape aussi bien un intérieur d'église qu'un panorama.
SOURCE_BONUS = {
    "view-category": 80,
    "wikidata": 55,
    "city-category": 20,
    "geosearch": 10,
    "search": 0,
}

# Noms employés par convention sur Commons pour regrouper les vues d'une ville.
# Les sonder directement rattrape les catégories que l'arbre ne rattache pas à
# la catégorie principale, comme « Views of Baghdad ».
VIEW_CATEGORY_PATTERNS = (
    "Views of {}", "Cityscapes of {}", "Cityscape of {}", "Skyline of {}",
    "Skylines of {}", "Aerial photographs of {}", "Aerial views of {}",
    "Panoramics of {}", "Panoramas of {}", "Night views of {}",
    "Sunsets in {}", "Sunrises in {}", "{} at night", "{} skyline",
)

# Même filet, appliqué aux sous-catégories réellement présentes sous la ville.
VIEW_CATEGORY_RE = re.compile(
    r"^(views?|cityscapes?|skylines?|panoramas?|panoramics?|aerial photographs?|"
    r"aerial views?|aerial photography|night views?|sunsets?|sunrises?) (of|in|from) "
    r"| at night$| skyline$| panorama$",
    re.I,
)

# Motifs rédhibitoires, lus dans le titre et les catégories du fichier. Une belle
# photo de cérémonie, d'intérieur ou d'attentat reste un mauvais fond d'écran.
REJECT_TERMS = (
    # Ni une photographie, ni exploitable en plein écran.
    "map", "maps", "plan", "atlas", "engraving", "etching", "lithograph",
    "woodcut", "painting", "drawing", "sketch", "illustration", "manuscript",
    "poster", "stamp", "banknote", "coin", "medal", "diagram", "chart",
    "coat of arms", "flag", "flags", "logo", "icon", "seal", "emblem", "blazon",
    "pd-art", "pd-old", "caricature", "postcard", "screenshot", "collage",
    "montage",
    # Reproductions d'ouvrages anciens: « AFR V2 D333 General view of Algiers »
    # est une planche de livre numérisée, pas une photographie de la ville.
    "scan", "scans", "internet archive", "digitised", "digitized",
    "black and white", "monochrome", "sepia", "glass plate", "archival",
    # Vues d'intérieur: pas de profondeur, pas de ciel.
    "interior", "interiors", "inside", "indoor", "ceiling", "staircase",
    "corridor", "nave", "altar", "exhibit", "exhibition", "museum object",
    # Sujets humains et événements: la ville n'y est qu'un décor.
    "portrait", "portraits", "celebration", "celebrations", "independence day",
    "festival", "parade", "march", "crowd", "crowds", "protest", "rally",
    "riot", "funeral", "wedding", "election", "campaign", "carnival",
    "concert", "conference", "meeting", "summit", "workshop", "ceremony",
    "visit", "visits", "secretary", "admiral", "ambassador", "delegation",
    "navy", "marine corps", "army", "troops", "soldiers", "police", "military",
    "dvids",
    # Guerre et catastrophes.
    "bomb", "bombing", "explosion", "attack", "destroyed", "destruction",
    "ruins", "damage", "damaged", "war", "airstrike", "shelling",
    # Vues orbitales: spectaculaires, mais ce n'est plus une ville.
    "astronaut", "satellite image", "from space", "earth observation",
    # Véhicules: rangés dans les catégories de vues, mais c'est l'engin le sujet.
    # Le bateau de plaisance « Capitan Morgan » illustrait ainsi Alger.
    "boat", "boats", "ship", "ships", "ferry", "yacht", "vessel", "aircraft",
    "airplane", "helicopter", "train", "trains", "locomotive", "railway",
    "railroad", "tram", "bus", "car", "cars", "motorcycle", "highway",
    "motorway",
    # Détails et chantiers.
    "close-up", "closeup", "detail", "plaque", "signage", "construction site",
    "under construction", "unidentified",
)

# « Mt Wellington » n'est pas Wellington, « Lake Victoria » n'est pas Victoria.
# Un nom de ville précédé de l'un de ces mots désigne un autre lieu.
QUALIFIER_PREFIXES = frozenset(
    {"mount", "mt", "mt.", "lake", "port", "fort", "cape", "gulf", "bay", "river"}
)

# Mots qui, dans le titre ou les catégories, annoncent une vue large et non un
# bâtiment isolé, et souvent une lumière flatteuse.
PREFERRED_TERMS = {
    "skyline": 14, "cityscape": 14, "panorama": 8, "panoramic": 8,
    "aerial": 8, "night": 10, "sunset": 10, "sunrise": 10, "dusk": 8,
    "dawn": 8, "twilight": 8, "downtown": 6, "overlooking": 8, "view": 5,
}

# Wikimedia demande un User-Agent descriptif pour les scripts automatisés.
USER_AGENT = (
    "WallpaperDownloader/1.0 "
    "(contact: eliot.christon.spam@gmail.com)"
)

# Les grands panoramas de Commons dépassent la limite anti-« bombe de
# décompression » par défaut de Pillow. La source est fiable, on relève le plafond.
Image.MAX_IMAGE_PIXELS = 300_000_000

# Q1065 = Organisation des Nations unies, Q237 = Saint-Siège, Q219060 = Palestine.
# P463 = membre de, P298 = code ISO 3166-1 alpha-3, P36 = capitale, P582 = date de fin.
# Le filtre sur P298 écarte les États historiques (Royaume du Népal, République
# d'Afghanistan, etc.) qui portent encore une adhésion à l'ONU sans date de fin.
# Les libellés anglais servent aux recherches : Commons est indexé en anglais,
# « Abou Dabi » n'y ramène rien alors que « Abu Dhabi » oui.
# P625 = coordonnées, P373 = catégorie Commons, P18 = image: ce sont eux qui
# ancrent la recherche sur la ville plutôt que sur une chaîne de caractères.
CAPITALS_QUERY = """
SELECT ?countryLabel ?code ?capitalLabel ?countryEn ?capitalEn
       ?coord ?commonscat ?image WHERE {
  {
    ?country p:P463 ?membership .
    ?membership ps:P463 wd:Q1065 .
    FILTER NOT EXISTS { ?membership pq:P582 ?endTime }
  } UNION {
    VALUES ?country { wd:Q237 wd:Q219060 }
  }
  ?country wdt:P298 ?code .
  ?country wdt:P36 ?capital .
  OPTIONAL { ?capital wdt:P625 ?coord }
  OPTIONAL { ?capital wdt:P373 ?commonscat }
  OPTIONAL { ?capital wdt:P18 ?image }
  OPTIONAL { ?country rdfs:label ?countryEn . FILTER(LANG(?countryEn) = "en") }
  OPTIONAL { ?capital rdfs:label ?capitalEn . FILTER(LANG(?capitalEn) = "en") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }
}
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("wallpapers")


@dataclass(frozen=True)
class Capital:
    country: str
    country_code: str
    capital: str
    # Libellés anglais utilisés pour interroger Commons, le français restant
    # affiché et enregistré dans le manifeste.
    country_en: str = ""
    capital_en: str = ""
    # Ancrages Wikidata: catégorie Commons de la ville, coordonnées du centre,
    # image de référence retenue par la communauté.
    commons_category: str = ""
    latitude: float | None = None
    longitude: float | None = None
    wikidata_image: str = ""

    @property
    def search_country(self) -> str:
        return self.country_en or self.country

    @property
    def search_capital(self) -> str:
        return self.capital_en or self.capital

    @property
    def city_category(self) -> str:
        """Catégorie Commons de la ville, avec le libellé anglais en repli."""
        return self.commons_category or self.search_capital

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def matches_name(self, needle: str) -> bool:
        folded = needle.casefold()
        return folded in {self.capital.casefold(), self.search_capital.casefold()}


@dataclass
class DownloadedWallpaper:
    country: str
    country_code: str
    capital: str
    filename: str
    search_query: str
    source_url: str
    source_page: str
    author: str
    license: str
    license_url: str
    assessment: str
    source_kind: str
    original_width: int
    original_height: int
    downloaded_at: str


# Wikidata rattache l'adhésion à l'ONU du Danemark à « Royaume de Danemark »
# (Q756617), entité dépourvue de code ISO alpha-3, alors que le code DNK et la
# capitale sont portés par « Danemark » (Q35). La requête ne peut donc pas le
# retrouver, on le complète explicitement.
EXTRA_STATES = (
    Capital(
        country="Danemark",
        country_code="DNK",
        capital="Copenhague",
        country_en="Denmark",
        capital_en="Copenhagen",
        commons_category="Copenhagen",
        latitude=55.6761,
        longitude=12.5683,
    ),
)


class ApiError(RuntimeError):
    pass


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "image"


def parse_capitals(entries: Iterable[dict[str, Any]]) -> list[Capital]:
    """Convertit les entrées de Wikidata ou du cache en objets Capital."""
    capitals: dict[tuple[str, str], Capital] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("country_code") or "").upper()
        country = str(entry.get("country") or "").strip()
        capital = str(entry.get("capital") or "").strip()
        if not code or not country or not capital:
            continue
        # Wikidata renvoie l'identifiant Qxx quand aucun libellé n'existe.
        if re.fullmatch(r"Q\d+", country) or re.fullmatch(r"Q\d+", capital):
            continue
        latitude = entry.get("latitude")
        longitude = entry.get("longitude")
        capitals[(code, capital.casefold())] = Capital(
            country=country,
            country_code=code,
            capital=capital,
            country_en=str(entry.get("country_en") or "").strip(),
            capital_en=str(entry.get("capital_en") or "").strip(),
            commons_category=str(entry.get("commons_category") or "").strip(),
            latitude=float(latitude) if latitude is not None else None,
            longitude=float(longitude) if longitude is not None else None,
            wikidata_image=str(entry.get("wikidata_image") or "").strip(),
        )

    for extra in EXTRA_STATES:
        capitals.setdefault((extra.country_code, extra.capital.casefold()), extra)

    return sorted(
        capitals.values(),
        key=lambda item: (item.capital.casefold(), item.country.casefold()),
    )


def parse_point(literal: str) -> tuple[float | None, float | None]:
    """Décode « Point(longitude latitude) », la forme des coordonnées Wikidata."""
    match = re.fullmatch(r"\s*Point\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)\s*", literal or "")
    if not match:
        return None, None
    return float(match.group(2)), float(match.group(1))


def file_title_from_url(url: str) -> str:
    """« …/Special:FilePath/Nom.jpg » devient « File:Nom.jpg »."""
    if not url:
        return ""
    name = unquote(url.rsplit("/", 1)[-1]).replace("_", " ").strip()
    return f"File:{name}" if name else ""


def fetch_capitals(session: requests.Session) -> list[dict[str, Any]]:
    """Interroge Wikidata et retourne des entrées sérialisables."""
    response = session.get(
        WIKIDATA_SPARQL_URL,
        params={"query": CAPITALS_QUERY, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
        timeout=90,
    )
    response.raise_for_status()
    bindings = response.json().get("results", {}).get("bindings", [])
    entries = []
    for binding in bindings:
        latitude, longitude = parse_point((binding.get("coord") or {}).get("value", ""))
        entries.append(
            {
                "country": (binding.get("countryLabel") or {}).get("value", ""),
                "country_code": (binding.get("code") or {}).get("value", ""),
                "capital": (binding.get("capitalLabel") or {}).get("value", ""),
                "country_en": (binding.get("countryEn") or {}).get("value", ""),
                "capital_en": (binding.get("capitalEn") or {}).get("value", ""),
                "commons_category": (binding.get("commonscat") or {}).get("value", ""),
                "latitude": latitude,
                "longitude": longitude,
                "wikidata_image": file_title_from_url(
                    (binding.get("image") or {}).get("value", "")
                ),
            }
        )
    if not entries:
        raise ApiError("Wikidata n'a retourné aucune ligne exploitable.")
    return entries


def get_capitals(session: requests.Session, cache_path: Path) -> list[Capital]:
    """Charge la liste ONU + observateurs et la met en cache dans le projet."""
    try:
        entries = fetch_capitals(session)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Liste des capitales récupérée depuis Wikidata")
    except (requests.RequestException, ApiError, ValueError) as exc:
        if not cache_path.exists():
            raise ApiError(
                "Impossible de récupérer la liste des capitales et aucun cache n'existe."
            ) from exc
        log.warning("Wikidata indisponible (%s), utilisation du cache %s", exc, cache_path)
        entries = json.loads(cache_path.read_text(encoding="utf-8"))
        # Un cache écrit avant l'ancrage géographique ne porte ni coordonnées ni
        # catégorie Commons: la recherche retomberait sur le texte seul.
        if entries and not any(entry.get("commons_category") for entry in entries):
            log.warning(
                "Cache antérieur à l'ancrage sur la ville: "
                "la sélection sera moins fiable, relance quand Wikidata répond"
            )

    capitals = parse_capitals(entries)
    if not capitals:
        raise ApiError("La liste récupérée ne contient aucune capitale exploitable.")

    states = {item.country_code for item in capitals}
    if len(states) != EXPECTED_STATE_COUNT:
        log.warning(
            "%d États trouvés au lieu des %d attendus, "
            "la modélisation Wikidata a peut-être changé",
            len(states),
            EXPECTED_STATE_COUNT,
        )
    log.info("%d États, %d capitale(s) au total", len(states), len(capitals))
    return capitals


# ---------------------------------------------------------------------------
# Constitution du vivier de candidats
#
# Chercher « Alger » en toutes lettres sur Commons ramenait le port de Sète:
# l'index de texte ne sait pas où une photo a été prise. On part donc des deux
# rattachements que Commons garantit, la catégorie du fichier et ses
# coordonnées, et on ne retombe sur la recherche textuelle qu'en dernier
# recours, pour les capitales trop peu photographiées pour être catégorisées.
# ---------------------------------------------------------------------------

# Nombre de catégories de vues explorées par ville, pour borner les requêtes.
MAX_VIEW_CATEGORIES = 8

IMAGE_PROPS: dict[str, Any] = {
    "prop": "imageinfo|globalusage",
    "iiprop": "url|size|mime|extmetadata",
    # Une image reprise dans les Wikipédias illustre vraiment la ville: c'est le
    # meilleur substitut automatique au jugement « cette vue est iconique ».
    "guprop": "url",
    "gulimit": 20,
    "gufilterlocal": 1,
}


def commons_api(
    session: requests.Session, pause: float, **params: Any
) -> dict[str, Any]:
    """Appelle l'API Commons et rend le JSON, en respectant la pause demandée."""
    params.update({"action": "query", "format": "json", "formatversion": 2})
    response = session.get(COMMONS_API_URL, params=params, timeout=45)
    response.raise_for_status()
    data = response.json()
    time.sleep(pause)
    error = data.get("error")
    if error:
        raise ApiError(f"API Wikimedia Commons: {error}")
    return data


def existing_categories(
    session: requests.Session, names: Iterable[str], pause: float
) -> list[str]:
    """Ne garde que les catégories qui existent et contiennent des fichiers."""
    wanted = list(dict.fromkeys(names))
    found: list[str] = []
    for offset in range(0, len(wanted), 40):
        chunk = wanted[offset : offset + 40]
        data = commons_api(
            session,
            pause,
            titles="|".join(f"Category:{name}" for name in chunk),
            prop="categoryinfo",
        )
        for page in data.get("query", {}).get("pages", []):
            info = page.get("categoryinfo") or {}
            if info.get("files"):
                found.append(str(page.get("title", "")).removeprefix("Category:"))
    # L'API rend les pages dans le désordre: on rétablit l'ordre de préférence.
    return sorted(found, key=lambda name: wanted.index(name) if name in wanted else 99)


def subcategories(session: requests.Session, category: str, pause: float) -> list[str]:
    data = commons_api(
        session,
        pause,
        list="categorymembers",
        cmtitle=f"Category:{category}",
        cmtype="subcat",
        cmlimit=200,
    )
    return [
        str(member.get("title", "")).removeprefix("Category:")
        for member in data.get("query", {}).get("categorymembers", [])
    ]


def category_files(
    session: requests.Session,
    category: str,
    pause: float,
    thumb_width: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    data = commons_api(
        session,
        pause,
        generator="categorymembers",
        gcmtitle=f"Category:{category}",
        gcmtype="file",
        gcmlimit=limit,
        iiurlwidth=thumb_width,
        **IMAGE_PROPS,
    )
    return data.get("query", {}).get("pages", [])


def geosearch_files(
    session: requests.Session,
    item: Capital,
    pause: float,
    thumb_width: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    data = commons_api(
        session,
        pause,
        generator="geosearch",
        ggscoord=f"{item.latitude}|{item.longitude}",
        ggsradius=GEOSEARCH_RADIUS_M,
        ggsnamespace=6,
        ggslimit=limit,
        iiurlwidth=thumb_width,
        **IMAGE_PROPS,
    )
    return data.get("query", {}).get("pages", [])


def files_by_title(
    session: requests.Session, titles: Iterable[str], pause: float, thumb_width: int
) -> list[dict[str, Any]]:
    wanted = [title for title in titles if title]
    if not wanted:
        return []
    data = commons_api(
        session, pause, titles="|".join(wanted), iiurlwidth=thumb_width, **IMAGE_PROPS
    )
    return data.get("query", {}).get("pages", [])


def category_parents(
    session: requests.Session, names: Iterable[str], pause: float
) -> dict[str, list[str]]:
    """Catégories parentes, pour vérifier qu'une catégorie parle du bon pays."""
    parents: dict[str, list[str]] = {}
    wanted = list(dict.fromkeys(names))
    for offset in range(0, len(wanted), 40):
        chunk = wanted[offset : offset + 40]
        data = commons_api(
            session,
            pause,
            titles="|".join(f"Category:{name}" for name in chunk),
            prop="categories",
            cllimit=500,
        )
        for page in data.get("query", {}).get("pages", []):
            title = str(page.get("title", "")).removeprefix("Category:")
            parents[title] = [
                str(entry.get("title", "")).removeprefix("Category:")
                for entry in page.get("categories") or []
            ]
    return parents


def keep_local_categories(
    session: requests.Session, names: list[str], item: Capital, pause: float
) -> list[str]:
    """Écarte les homonymes: « Views of Victoria » peut viser une autre Victoria.

    Une catégorie nommée d'après la catégorie Wikidata de la ville est fiable par
    construction. Pour les autres, tirées du nom courant, on demande qu'une
    catégorie parente nomme le pays ou la ville, comme « Cityscapes in New
    Zealand » au-dessus de « Cityscapes of Wellington ».
    """
    trusted = {
        pattern.format(item.city_category).casefold()
        for pattern in VIEW_CATEGORY_PATTERNS
    }
    doubtful = [name for name in names if name.casefold() not in trusted]
    if not doubtful:
        return names

    parents = category_parents(session, doubtful, pause)
    anchors = {
        text.casefold()
        for text in (item.search_country, item.country, item.city_category)
        if text
    }
    kept: list[str] = []
    for name in names:
        if name.casefold() in trusted:
            kept.append(name)
            continue
        lineage = " | ".join(parents.get(name, [])).casefold()
        if any(anchor in lineage for anchor in anchors):
            kept.append(name)
        else:
            log.debug("Catégorie écartée, rattachement incertain: %s", name)
    return kept


def view_categories(
    session: requests.Session, item: Capital, pause: float
) -> list[str]:
    """Catégories de vues de la ville: noms conventionnels puis sous-catégories."""
    city = item.city_category
    # Wikidata donne la catégorie désambiguïsée, « Wellington urban area », alors
    # que les catégories de vues suivent le nom courant, « Cityscapes of
    # Wellington ». Sans les deux graphies, 21 capitales perdent leur meilleure
    # source d'images.
    aliases = [city]
    for alias in (item.search_capital, item.capital):
        if alias and alias.casefold() not in {name.casefold() for name in aliases}:
            aliases.append(alias)
    try:
        found = existing_categories(
            session,
            (
                pattern.format(name)
                for name in aliases
                for pattern in VIEW_CATEGORY_PATTERNS
            ),
            pause,
        )
        found = keep_local_categories(session, found, item, pause)
    except (requests.RequestException, ApiError, ValueError) as exc:
        log.warning("Catégories de vues illisibles pour %s: %s", city, exc)
        found = []
    try:
        for name in subcategories(session, city, pause):
            if VIEW_CATEGORY_RE.search(name) and name not in found:
                found.append(name)
    except (requests.RequestException, ApiError, ValueError) as exc:
        log.debug("Sous-catégories illisibles pour %s: %s", city, exc)
    return found[:MAX_VIEW_CATEGORIES]


def load_overrides(path: Path) -> dict[str, str]:
    """Choix imposés à la main: { "Bichkek": "File:Une belle vue.jpg" }.

    Aucune heuristique ne rattrape une ville que Commons photographie mal. Plutôt
    que de tordre le classement pour un cas particulier, on laisse la main.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Fichier de choix imposés illisible (%s): %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("Le fichier de choix imposés doit être un objet JSON: %s", path)
        return {}

    overrides: dict[str, str] = {}
    for key, value in raw.items():
        title = str(value or "").strip()
        if not title:
            continue
        # « Une vue.jpg » et « File:Une vue.jpg » désignent le même fichier.
        if not title.lower().startswith("file:"):
            title = f"File:{title}"
        overrides[str(key).strip().casefold()] = title
    return overrides


def override_for(overrides: dict[str, str], item: Capital) -> str:
    """Choix imposé pour cette capitale, par son nom français, anglais ou son code."""
    for key in (item.capital, item.search_capital, item.country_code):
        if title := overrides.get(key.casefold()):
            return title
    return ""


def query_variants(item: Capital) -> list[str]:
    """Recherche textuelle de repli, pour les villes sans catégorie exploitable."""
    city = item.search_capital
    country = item.search_country
    return [
        f'"{city}" skyline {country}',
        f'"{city}" cityscape {country}',
        f'"{city}" panorama {country}',
        f'"{city}" aerial view {country}',
    ]


def commons_search(
    session: requests.Session,
    query: str,
    limit: int,
    thumb_width: int,
    pause: float,
) -> list[dict[str, Any]]:
    # Un seul fichier non miniaturisable, par exemple un TIFF satellite de
    # plusieurs gigapixels, fait échouer toute la requête. On réessaie alors
    # sans vignette pour ne pas perdre les autres résultats.
    for width in (thumb_width, 0):
        params: dict[str, Any] = {
            "generator": "search",
            # filetype:bitmap écarte les SVG et PDF dès la recherche.
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": 6,
            "gsrlimit": min(limit, 50),
            **IMAGE_PROPS,
        }
        if width:
            params["iiurlwidth"] = width
        try:
            data = commons_api(session, pause, **params)
        except ApiError as exc:
            if width and "urlparamnormal" in str(exc):
                log.debug("Vignette refusée par Commons pour « %s »", query)
                continue
            raise
        return data.get("query", {}).get("pages", [])
    return []


def image_info(page: dict[str, Any]) -> dict[str, Any] | None:
    infos = page.get("imageinfo") or []
    return infos[0] if infos else None


def assessments(info: dict[str, Any]) -> set[str]:
    """Distinctions Commons de l'image: featured, quality, valued, poty, potd."""
    metadata = info.get("extmetadata") or {}
    raw = str((metadata.get("Assessments") or {}).get("value") or "")
    return {part.strip().lower() for part in raw.split("|") if part.strip()}


def has_term(text: str, term: str) -> bool:
    """Cherche un mot entier, pour que « plan » n'attrape pas « planetarium »."""
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def mentions_place(text: str, name: str) -> bool:
    """Vrai si le lieu est cité pour lui-même, pas via « Mount X » ou « Lake X »."""
    for match in re.finditer(rf"\b{re.escape(name)}\b", text):
        before = text[: match.start()].rstrip().rsplit(" ", 1)
        previous = before[-1].strip(",.-\"'()") if before and before[-1] else ""
        if previous not in QUALIFIER_PREFIXES:
            return True
    return False


def subject_text(page: dict[str, Any], info: dict[str, Any]) -> str:
    """Titre et catégories du fichier: ce qui décrit le sujet, pas le photographe."""
    metadata = info.get("extmetadata") or {}
    categories = strip_html((metadata.get("Categories") or {}).get("value"))
    return f"{page.get('title', '')} {categories}".lower()


# Une année antérieure à 1995 dans le titre, comme « Asmara aerial view 1981 ».
OUTDATED_YEAR_RE = re.compile(r"\b(1[89]\d\d|199[0-4])\b")


def capture_year(page: dict[str, Any], info: dict[str, Any]) -> int | None:
    """Année de prise de vue. Le titre prime, car les dates de fichier mentent.

    « Asmara aerial view 1981 » porte une DateTimeOriginal de 2007: c'est la date
    de numérisation du tirage, pas celle de la photographie.
    """
    if match := OUTDATED_YEAR_RE.search(str(page.get("title", ""))):
        return int(match.group(1))
    metadata = info.get("extmetadata") or {}
    for key in ("DateTimeOriginal", "DateTime"):
        match = re.search(r"\b(1[89]\d\d|20\d\d)\b", metadata_value(metadata, key))
        if match:
            return int(match.group(1))
    return None


def score_candidate(
    page: dict[str, Any], source: str = "search", item: Capital | None = None
) -> float:
    info = image_info(page)
    if not info:
        return -10_000

    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    mime = str(info.get("mime") or "").lower()
    # Commons héberge aussi des vidéos et des SVG, que geosearch remonte comme
    # les photographies.
    if not width or not height or not mime.startswith("image/"):
        return -10_000
    if mime in {"image/svg+xml", "image/gif"}:
        return -10_000

    subject = subject_text(page, info)
    if any(has_term(subject, term) for term in REJECT_TERMS):
        return -10_000
    # Les photographies orbitales sont titrées « ISS064-E-412 » ou « STS-51 ».
    if re.search(r"\b(iss|sts)[-\s]?\d", subject):
        return -10_000
    # Une ville se transforme: une vue d'avant 1995 n'en donne plus l'image.
    if (year := capture_year(page, info)) is not None and year < 1995:
        return -10_000

    ratio = width / height
    # Ce qui compte n'est pas la largeur du fichier mais celle qui subsiste après
    # le recadrage centré en 16:9: un panorama 5061x1200 ne laisse qu'une bande
    # de 2133 px, là où un 7585x2217 en garde 3941.
    usable_width = min(width, height * TARGET_RATIO)
    if ratio < MIN_RATIO or usable_width < MIN_WIDTH or width * height < MIN_PIXELS:
        return -10_000

    score = float(SOURCE_BONUS.get(source, 0))
    awards = sum(ASSESSMENT_BONUS.get(award, 0) for award in assessments(info))
    score += min(awards, MAX_ASSESSMENT_BONUS)
    if "featured desktop backgrounds" in subject:
        score += 100

    # Cadrage: proximité du 16:9, et frises exclues.
    score += max(0, 30 - abs(ratio - TARGET_RATIO) * 26)
    if ratio > MAX_COMFORTABLE_RATIO:
        score -= (ratio - MAX_COMFORTABLE_RATIO) * 25

    # Définition: en dessous de la largeur d'un fond d'écran courant, l'image
    # devra être interpolée à l'agrandissement.
    score += min(usable_width / 300, 20)
    if usable_width < 2560:
        score -= 25

    # Iconicité: le nombre de pages Wikimédia qui reprennent l'image.
    score += min(len(page.get("globalusage") or []), 10) * 7

    score += sum(
        bonus for term, bonus in PREFERRED_TERMS.items() if has_term(subject, term)
    )
    # Le nom de la ville dans le titre distingue « Belmopan Sunset » d'un
    # « Aerials Belize WHwy 02 » rangé dans la même catégorie. Le bonus est
    # volontairement lourd, mais reste un bonus: les titres en cyrillique ou le
    # nom d'un monument connu ne doivent pas éliminer une bonne photo.
    if item is not None:
        title = str(page.get("title", "")).lower()
        names = {item.capital.casefold(), item.search_capital.casefold()}
        if any(mentions_place(title, name) for name in names):
            score += 55
    # Les versements Panoramio sont anciens et souvent de faible définition.
    if "panoramio" in subject:
        score -= 8
    return score


def collect_candidates(
    session: requests.Session,
    item: Capital,
    pause: float,
    thumb_width: int,
) -> dict[str, tuple[dict[str, Any], str]]:
    """Rassemble les candidats, du rattachement le plus sûr au plus lâche."""
    pool: dict[str, tuple[dict[str, Any], str]] = {}

    def absorb(pages: Iterable[dict[str, Any]], source: str) -> None:
        for page in pages:
            if image_info(page):
                # setdefault: un fichier vu dans plusieurs sources garde la
                # provenance la plus fiable, celle rencontrée en premier.
                pool.setdefault(str(page.get("title") or ""), (page, source))

    def attempt(label: str, action: Any, source: str) -> None:
        try:
            absorb(action(), source)
        except (requests.RequestException, ApiError, ValueError) as exc:
            log.warning("%s indisponible pour %s: %s", label, item.capital, exc)

    for category in view_categories(session, item, pause):
        attempt(
            f"Catégorie « {category} »",
            lambda category=category: category_files(
                session, category, pause, thumb_width
            ),
            "view-category",
        )

    if item.wikidata_image:
        attempt(
            "Image Wikidata",
            lambda: files_by_title(session, [item.wikidata_image], pause, thumb_width),
            "wikidata",
        )

    attempt(
        f"Catégorie « {item.city_category} »",
        lambda: category_files(session, item.city_category, pause, thumb_width),
        "city-category",
    )

    if item.has_coordinates:
        attempt(
            "Recherche par coordonnées",
            lambda: geosearch_files(session, item, pause, thumb_width),
            "geosearch",
        )
    return pool


def find_best_image(
    session: requests.Session,
    item: Capital,
    result_limit: int,
    pause: float,
    thumb_width: int,
    quality_only: bool = False,
    override_title: str = "",
) -> tuple[dict[str, Any], str] | None:
    """Choisit la meilleure image: choix imposé, puis catégories, puis texte."""
    if override_title:
        try:
            pages = files_by_title(session, [override_title], pause, thumb_width)
        except (requests.RequestException, ApiError, ValueError) as exc:
            log.warning("Choix imposé illisible pour %s: %s", item.capital, exc)
            pages = []
        for page in pages:
            if image_info(page):
                return page, "override"
        log.warning(
            "Choix imposé introuvable pour %s (%s), retour à la sélection normale",
            item.capital,
            override_title,
        )

    def best_of(
        pool: dict[str, tuple[dict[str, Any], str]],
    ) -> tuple[dict[str, Any], str] | None:
        entries = list(pool.values())
        if quality_only:
            entries = [
                entry for entry in entries if assessments(image_info(entry[0]) or {})
            ]
        scored = [
            (score_candidate(page, source, item), page, source)
            for page, source in entries
        ]
        # Tous les candidats peuvent être disqualifiés: vidéo, intérieur, carte.
        viable = [entry for entry in scored if entry[0] > 0]
        if not viable:
            return None
        _, page, source = max(viable, key=lambda entry: entry[0])
        return page, source

    best = best_of(collect_candidates(session, item, pause, thumb_width))
    if best:
        return best

    log.info("Aucun candidat catégorisé pour %s, repli sur la recherche", item.capital)
    fallback: dict[str, tuple[dict[str, Any], str]] = {}
    names = {item.capital.casefold(), item.search_capital.casefold()}
    for query in query_variants(item):
        try:
            pages = commons_search(session, query, result_limit, thumb_width, pause)
        except (requests.RequestException, ApiError, ValueError) as exc:
            log.warning(
                "Recherche impossible pour %s (%s): %s", item.capital, query, exc
            )
            continue
        for page in pages:
            info = image_info(page)
            if not info:
                continue
            # La recherche textuelle ne garantit rien: on exige que la ville soit
            # nommée dans le titre ou les catégories du fichier.
            subject = subject_text(page, info)
            if any(mentions_place(subject, name) for name in names):
                fallback.setdefault(str(page.get("title") or ""), (page, "search"))
    return best_of(fallback)


def save_as_wallpaper(raw_bytes: bytes, output_path: Path, target_width: int) -> None:
    """Recadre en 16:9 à la largeur demandée et enregistre en JPEG."""
    target_height = round(target_width / TARGET_RATIO)
    with Image.open(BytesIO(raw_bytes)) as image:
        oriented = ImageOps.exif_transpose(image) or image
        fitted = ImageOps.fit(
            oriented.convert("RGB"),
            (target_width, target_height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        fitted.save(output_path, format="JPEG", quality=92, optimize=True)


def metadata_value(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key) or {}
    if isinstance(value, dict):
        return strip_html(str(value.get("value") or ""))
    return strip_html(str(value))


def extension_for_mime(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "image/bmp": ".bmp",
        "image/tiff": ".tif",
    }.get(mime.lower(), ".img")


def manifest_filename(path: Path) -> str:
    """Chemin lisible pour le manifeste, relatif au projet quand c'est possible."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def existing_output(output_dir: Path, base_name: str, convert_to_jpeg: bool) -> Path | None:
    """Retrouve un fichier déjà téléchargé, quelle que soit son extension."""
    if convert_to_jpeg:
        candidate = output_dir / f"{base_name}.jpg"
        return candidate if candidate.exists() else None
    matches = sorted(output_dir.glob(f"{base_name}.*"))
    return matches[0] if matches else None


def download_one(
    session: requests.Session,
    item: Capital,
    output_dir: Path,
    target_width: int,
    result_limit: int,
    pause: float,
    force: bool,
    convert_to_jpeg: bool,
    quality_only: bool,
    override_title: str = "",
) -> DownloadedWallpaper | None:
    base_name = f"{slugify(item.capital)}_{slugify(item.country_code)}"
    already = existing_output(output_dir, base_name, convert_to_jpeg)
    if already and not force:
        log.info("Déjà présent, ignoré: %s", already.name)
        return None

    thumb_width = max(target_width, 640) if convert_to_jpeg else 1280
    selected = find_best_image(
        session, item, result_limit, pause, thumb_width, quality_only, override_title
    )
    if not selected:
        log.warning("Aucune image retenue pour %s, %s", item.capital, item.country)
        return None

    page, source_kind = selected
    info = image_info(page) or {}
    original_url = str(info.get("url") or "")
    mime = str(info.get("mime") or "")

    # En mode conversion, la vignette suffit et évite de rapatrier l'original.
    # Sinon il faut le fichier source, sans quoi l'extension mentirait sur le
    # contenu, la vignette de Commons étant réencodée en JPEG ou PNG.
    if convert_to_jpeg:
        image_url = str(info.get("thumburl") or original_url)
        output_path = output_dir / f"{base_name}.jpg"
    else:
        image_url = original_url
        output_path = output_dir / f"{base_name}{extension_for_mime(mime)}"

    if not image_url:
        log.warning("URL absente pour %s", page.get("title"))
        return None
    if output_path.exists() and not force:
        log.info("Déjà présent, ignoré: %s", output_path.name)
        return None

    try:
        response = session.get(
            image_url, timeout=90, headers={"Accept": "image/*,*/*;q=0.8"}
        )
        response.raise_for_status()
        output_dir.mkdir(parents=True, exist_ok=True)
        if convert_to_jpeg:
            save_as_wallpaper(response.content, output_path, target_width)
        else:
            output_path.write_bytes(response.content)
    except (requests.RequestException, OSError, ValueError) as exc:
        log.warning("Téléchargement impossible pour %s: %s", item.capital, exc)
        return None

    metadata = info.get("extmetadata") or {}
    awards = assessments(info)
    result = DownloadedWallpaper(
        country=item.country,
        country_code=item.country_code,
        capital=item.capital,
        filename=manifest_filename(output_path),
        search_query=item.city_category,
        source_url=original_url or image_url,
        source_page=str(
            info.get("descriptionurl")
            or f"https://commons.wikimedia.org/wiki/{page.get('title', '')}"
        ),
        author=metadata_value(metadata, "Artist"),
        license=metadata_value(metadata, "LicenseShortName"),
        license_url=metadata_value(metadata, "LicenseUrl"),
        assessment="|".join(sorted(awards)),
        source_kind=source_kind,
        original_width=int(info.get("width") or 0),
        original_height=int(info.get("height") or 0),
        downloaded_at=datetime.now(timezone.utc).isoformat(),
    )
    label = ", ".join(sorted(awards)) if awards else "sans distinction"
    log.info("Téléchargé: %s (%s, %s)", output_path.name, source_kind, label)
    return result


def load_manifest(manifest_path: Path) -> dict[tuple[Any, Any], dict[str, Any]]:
    """Indexe le manifeste existant, une entrée par pays et par capitale."""
    if not manifest_path.exists():
        return {}
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("Manifeste illisible, il sera recréé: %s", manifest_path)
        return {}
    if not isinstance(entries, list):
        return {}
    return {
        (entry.get("country_code"), entry.get("capital")): entry
        for entry in entries
        if isinstance(entry, dict)
    }


def write_manifest(
    manifest_path: Path, entries: dict[tuple[Any, Any], dict[str, Any]]
) -> None:
    ordered = sorted(
        entries.values(),
        key=lambda entry: (str(entry.get("capital", "")), str(entry.get("country", ""))),
    )
    # Écriture atomique: le manifeste est réécrit après chaque téléchargement, et
    # une synchronisation type OneDrive qui observerait le fichier tronqué peut
    # le restaurer dans son état précédent. Un remplacement en une opération lui
    # présente toujours un fichier complet.
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(manifest_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/wallpaper"),
        help="Dossier de sortie, par défaut data/wallpaper",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=Path("data/capitals_wikidata.json"),
        help="Cache de la liste des capitales",
    )
    parser.add_argument(
        "--limit", type=int, help="Limiter le nombre de capitales, utile pour tester"
    )
    parser.add_argument("--capital", help="Ne traiter qu'une capitale, par exemple Paris")
    parser.add_argument(
        "--country-code", help="Ne traiter qu'un code ISO alpha-3, par exemple FRA"
    )
    parser.add_argument(
        "--force", action="store_true", help="Remplacer les fichiers déjà présents"
    )
    parser.add_argument(
        "--width", type=int, default=2560, help="Largeur finale, par défaut 2560 px"
    )
    parser.add_argument(
        "--results", type=int, default=20, help="Résultats Commons par requête"
    )
    parser.add_argument(
        "--pause", type=float, default=0.6, help="Pause entre requêtes, en secondes"
    )
    parser.add_argument(
        "--quality-only",
        action="store_true",
        help="N'accepter que les images distinguées par la communauté Commons",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("data/overrides.json"),
        help="Fichier des choix imposés, { capitale: fichier Commons }",
    )
    parser.add_argument(
        "--keep-original-format",
        action="store_true",
        help="Ne pas convertir en JPEG 16:9, conserver le format téléchargé",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.width < 640:
        raise SystemExit("--width doit être supérieur ou égal à 640")

    session = create_session()
    capitals = get_capitals(session, args.cache_file)

    if args.capital:
        # Le nom français comme l'anglais sont acceptés: Abou Dabi ou Abu Dhabi.
        capitals = [item for item in capitals if item.matches_name(args.capital)]
    if args.country_code:
        code = args.country_code.upper()
        capitals = [item for item in capitals if item.country_code == code]
    if args.limit:
        capitals = capitals[: args.limit]

    if not capitals:
        log.error("Aucune capitale ne correspond aux filtres.")
        return 1

    overrides = load_overrides(args.overrides)
    if overrides:
        log.info("%d choix imposé(s) chargé(s) depuis %s", len(overrides), args.overrides)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    manifest_by_key = load_manifest(manifest_path)

    log.info("%d capitale(s) à traiter", len(capitals))
    skipped = 0
    for index, item in enumerate(capitals, start=1):
        log.info("[%d/%d] %s, %s", index, len(capitals), item.capital, item.country)
        result = download_one(
            session=session,
            item=item,
            output_dir=args.output_dir,
            target_width=args.width,
            result_limit=args.results,
            pause=args.pause,
            force=args.force,
            convert_to_jpeg=not args.keep_original_format,
            quality_only=args.quality_only,
            override_title=override_for(overrides, item),
        )
        if result:
            manifest_by_key[(result.country_code, result.capital)] = asdict(result)
            write_manifest(manifest_path, manifest_by_key)
        else:
            skipped += 1

    log.info("Terminé. Manifest: %s", manifest_path)
    if skipped:
        log.info(
            "%d capitale(s) sans nouvelle image, déjà présentes ou introuvables", skipped
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log.warning("Interrompu par l'utilisateur")
        raise SystemExit(130) from None
