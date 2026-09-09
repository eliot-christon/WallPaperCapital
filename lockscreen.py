#!/usr/bin/env python3
"""Applique un écran de verrouillage Windows tiré au hasard dans la banque d'images.

Choisit un fichier de `data/wallpaper/`, incruste en bas à droite le nom de la capitale
et du pays, puis pose l'image comme écran de verrouillage via l'API WinRT (pont
PowerShell `scripts/Set-LockScreen.ps1`). Prévu pour être lancé à chaque ouverture de
session par une tâche planifiée.

Le fond de bureau n'est jamais touché.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

# `slugify` est la fonction qui a produit les noms de fichiers de la banque d'images.
# La réutiliser garantit que l'index reste aligné sur download_wallpapers.py.
from download_wallpapers import EXTRA_STATES, slugify

REPO_ROOT = Path(__file__).resolve().parent
BRIDGE_SCRIPT = REPO_ROOT / "scripts" / "Set-LockScreen.ps1"

# Tout l'état runtime vit hors du repo : écrire une image neuve à chaque ouverture de
# session dans data/ déclencherait une resynchronisation OneDrive inutile.
APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
APP_DIR = APP_DIR / "WallPaperCapital"
DEFAULT_OUTPUT = APP_DIR / "lockscreen.jpg"
STATE_FILE = APP_DIR / "state.json"
LOG_FILE = APP_DIR / "lockscreen.log"

DEFAULT_SOURCE_DIR = REPO_ROOT / "data" / "wallpaper"
DEFAULT_CACHE_FILE = REPO_ROOT / "data" / "capitals_wikidata.json"

TARGET_SIZE = (2560, 1440)

# L'écran principal est en 16:10 : Windows recadre l'image 16:9 en « cover » et rogne
# environ 5 % de la largeur de chaque côté. Un tag collé au bord serait coupé.
DEFAULT_MARGIN_X = 0.07
DEFAULT_MARGIN_Y = 0.06

PILL_FILL = (0, 0, 0, 115)
TEXT_PRIMARY = (255, 255, 255, 255)
TEXT_SECONDARY = (255, 255, 255, 190)
SEPARATOR = " · "

FONTS_DIR = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts"
PRIMARY_FONTS = ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "calibrib.ttf")
SECONDARY_FONTS = ("segoeui.ttf", "arial.ttf", "calibri.ttf")

# Nombre de tirages récents exclus, et taille max de l'historique conservé.
HISTORY_EXCLUDE = 20
HISTORY_MAX = 40

SPOTLIGHT_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
SPOTLIGHT_VALUE = "RotatingLockScreenEnabled"

log = logging.getLogger("lockscreen")


@dataclass(frozen=True)
class Labels:
    """Libellés à incruster pour une image donnée."""

    capital: str
    country: str
    capital_en: str = ""
    country_en: str = ""
    country_code: str = ""

    def lines(self) -> tuple[str, str | None]:
        """Renvoie la ligne principale et, si elle apporte quelque chose, la secondaire."""
        primary = f"{self.capital.upper()}{SEPARATOR}{self.country}"
        extras: list[str] = []
        if self.capital_en and not _same_label(self.capital_en, self.capital):
            extras.append(self.capital_en)
        if self.country_en and not _same_label(self.country_en, self.country):
            extras.append(self.country_en)
        return primary, SEPARATOR.join(extras) or None


def _same_label(left: str, right: str) -> bool:
    return left.strip().casefold() == right.strip().casefold()


def _capitalized(value: str) -> str:
    """Force la majuscule initiale sans toucher au reste.

    Wikidata renvoie quelques libellés en minuscule (« république démocratique du
    Congo ») ; `.title()` casserait les particules, `.capitalize()` le reste du texte.
    """
    return value[:1].upper() + value[1:] if value else value


# ---------------------------------------------------------------------------
# Journalisation
# ---------------------------------------------------------------------------


def setup_logging(verbose: bool) -> None:
    """Journalise sur stderr et dans un fichier : au logon il n'y a aucune console."""
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    # download_wallpapers appelle logging.basicConfig a l'import : sans cela chaque
    # message serait emis deux fois, avec deux formats differents.
    log.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    log.addHandler(stream)

    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=256 * 1024, backupCount=1, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - dépend du système de fichiers
        log.warning("Journal fichier indisponible (%s)", exc)


# ---------------------------------------------------------------------------
# État persistant
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    try:
        with STATE_FILE.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("État illisible, on repart de zéro (%s)", exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_state(state: dict[str, Any]) -> None:
    """Écriture atomique, pour ne jamais laisser un JSON tronqué derrière soi."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        tmp.replace(STATE_FILE)
    except OSError as exc:
        log.warning("Impossible d'enregistrer l'état (%s)", exc)


# ---------------------------------------------------------------------------
# Index des libellés
# ---------------------------------------------------------------------------


def build_index(cache_file: Path) -> dict[str, Labels]:
    """Associe chaque nom de fichier (sans extension) aux libellés de la capitale.

    La clé est reconstruite avec le même `slugify` que celui qui a nommé les fichiers,
    ce qui rend l'index insensible aux doublons de capitale entre pays.
    """
    try:
        with cache_file.open(encoding="utf-8") as handle:
            entries = json.load(handle)
    except FileNotFoundError:
        log.error("Cache des capitales introuvable : %s", cache_file)
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Cache des capitales illisible (%s)", exc)
        return {}

    index: dict[str, Labels] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        capital = str(entry.get("capital") or "").strip()
        country = str(entry.get("country") or "").strip()
        code = str(entry.get("country_code") or "").strip().upper()
        if not capital or not country or not code:
            continue
        stem = f"{slugify(capital)}_{slugify(code)}"
        index[stem] = Labels(
            capital=capital,
            country=_capitalized(country),
            capital_en=str(entry.get("capital_en") or "").strip(),
            country_en=str(entry.get("country_en") or "").strip(),
            country_code=code,
        )
    # Même correctif que download_wallpapers (ligne 336) : le Danemark est absent de la
    # requête SPARQL, son image existe pourtant dans la banque.
    for extra in EXTRA_STATES:
        stem = f"{slugify(extra.capital)}_{slugify(extra.country_code)}"
        index.setdefault(
            stem,
            Labels(
                capital=extra.capital,
                country=_capitalized(extra.country),
                capital_en=extra.capital_en,
                country_en=extra.country_en,
                country_code=extra.country_code,
            ),
        )

    log.debug("%d entrées indexées depuis %s", len(index), cache_file.name)
    return index


def labels_for(stem: str, index: dict[str, Labels]) -> Labels:
    """Retrouve les libellés d'un fichier, avec un repli si le cache ne le connaît pas."""
    known = index.get(stem)
    if known is not None:
        return known

    # Repli : les noms suivent `<capitale>_<iso3>`, l'ISO-3 est le dernier segment.
    capital_slug, _, code_slug = stem.rpartition("_")
    code = code_slug.upper()
    country = next(
        (item.country for item in index.values() if item.country_code == code),
        code or "?",
    )
    capital = capital_slug.replace("_", " ").title() or stem
    log.warning("Aucune entrée de cache pour « %s », libellés déduits du nom de fichier", stem)
    return Labels(capital=capital, country=country, country_code=code)


# ---------------------------------------------------------------------------
# Choix de l'image
# ---------------------------------------------------------------------------


def list_images(source_dir: Path) -> list[Path]:
    images = sorted(p for p in source_dir.glob("*.jpg") if p.is_file())
    log.debug("%d image(s) dans %s", len(images), source_dir)
    return images


def filter_images(images: list[Path], capital: str | None, country_code: str | None) -> list[Path]:
    """Restreint la sélection à une capitale ou un pays (utile pour tester un rendu)."""
    selected = images
    if country_code:
        suffix = f"_{slugify(country_code)}"
        selected = [p for p in selected if p.stem.endswith(suffix)]
    if capital:
        prefix = f"{slugify(capital)}_"
        selected = [p for p in selected if p.stem.startswith(prefix)]
    return selected


def pick_image(images: list[Path], history: list[str]) -> Path:
    """Tire au sort en évitant les derniers affichages."""
    recent = set(history[-HISTORY_EXCLUDE:])
    pool = [p for p in images if p.stem not in recent]
    if not pool:
        # Sélection plus petite que la fenêtre d'exclusion : on repart sur tout.
        pool = images
    return random.choice(pool)


# ---------------------------------------------------------------------------
# Rendu du tag
# ---------------------------------------------------------------------------


def load_font(candidates: tuple[str, ...], size: int) -> Any:
    for name in candidates:
        path = FONTS_DIR / name
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    log.warning("Aucune police TrueType trouvée dans %s, rendu dégradé", FONTS_DIR)
    return ImageFont.load_default()


def draw_tag(image: Image.Image, labels: Labels, margin_x: float, margin_y: float) -> Image.Image:
    """Incruste une pilule translucide portant les libellés, en bas à droite."""
    width, height = image.size
    primary_text, secondary_text = labels.lines()

    size_primary = max(14, round(height * 0.030))
    size_secondary = max(11, round(height * 0.019))
    font_primary = load_font(PRIMARY_FONTS, size_primary)
    font_secondary = load_font(SECONDARY_FONTS, size_secondary)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    rows = [(primary_text, font_primary, TEXT_PRIMARY)]
    if secondary_text:
        rows.append((secondary_text, font_secondary, TEXT_SECONDARY))

    # textbbox renvoie des bornes relatives à l'ancre : on garde les décalages pour
    # positionner le glyphe au pixel près (textsize a disparu de Pillow 10+).
    measured: list[dict[str, Any]] = []
    for text, font, color in rows:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        measured.append(
            {
                "text": text,
                "font": font,
                "color": color,
                "offset": (left, top),
                "size": (right - left, bottom - top),
            }
        )

    gap = round(size_secondary * 0.35)
    block_width = max(item["size"][0] for item in measured)
    block_height = sum(item["size"][1] for item in measured) + gap * (len(measured) - 1)

    padding_x = round(size_primary * 0.75)
    padding_y = round(size_primary * 0.50)
    pill_width = block_width + 2 * padding_x
    pill_height = block_height + 2 * padding_y

    pill_right = width - round(width * margin_x)
    pill_bottom = height - round(height * margin_y)
    pill_left = max(0, pill_right - pill_width)
    pill_top = max(0, pill_bottom - pill_height)

    radius = min(round(pill_height * 0.28), pill_height // 2)
    draw.rounded_rectangle(
        (pill_left, pill_top, pill_right, pill_bottom), radius=radius, fill=PILL_FILL
    )

    cursor_y = pill_top + padding_y
    for item in measured:
        text_width, text_height = item["size"]
        offset_x, offset_y = item["offset"]
        text_x = pill_right - padding_x - text_width
        draw.text(
            (text_x - offset_x, cursor_y - offset_y),
            item["text"],
            font=item["font"],
            fill=item["color"],
        )
        cursor_y += text_height + gap

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def render(source: Path, labels: Labels, output: Path, margin_x: float, margin_y: float) -> None:
    """Recadre en 16:9 puis écrit le JPEG tagué, de façon atomique."""
    with Image.open(source) as raw:
        oriented = ImageOps.exif_transpose(raw) or raw
        fitted = ImageOps.fit(
            oriented.convert("RGB"),
            TARGET_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    tagged = draw_tag(fitted, labels, margin_x, margin_y)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tagged.save(tmp, format="JPEG", quality=92, optimize=True)
    tmp.replace(output)
    log.debug("Image écrite : %s", output)


# ---------------------------------------------------------------------------
# Pont WinRT
# ---------------------------------------------------------------------------


def _decode(raw: bytes) -> str:
    """PowerShell 5.1 écrit dans la page de code console : on essaie plusieurs encodages."""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace").strip()


def run_bridge(arguments: list[str]) -> tuple[int, str, str]:
    if not BRIDGE_SCRIPT.is_file():
        log.error("Script PowerShell introuvable : %s", BRIDGE_SCRIPT)
        return 1, "", "pont manquant"
    command = [
        # powershell.exe (5.1) obligatoire : la projection WinRT s'appuie sur
        # System.Runtime.WindowsRuntime, absente de PowerShell 7.
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(BRIDGE_SCRIPT),
        *arguments,
    ]
    completed = subprocess.run(command, capture_output=True, check=False)
    return completed.returncode, _decode(completed.stdout), _decode(completed.stderr)


def query_current_lockscreen() -> str | None:
    code, out, err = run_bridge(["-Query"])
    if code != 0:
        log.warning("Lecture de l'écran de verrouillage courant impossible (%s)", err)
        return None
    return out or None


def apply_lockscreen(image_path: Path) -> bool:
    code, _, err = run_bridge(["-ImagePath", str(image_path)])
    if code != 0:
        log.error("Échec de l'application de l'écran de verrouillage : %s", err)
        return False
    return True


def spotlight_enabled() -> bool | None:
    """True si Windows Spotlight risque d'écraser l'image personnalisée."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - hors Windows
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SPOTLIGHT_KEY) as key:
            value, _ = winreg.QueryValueEx(key, SPOTLIGHT_VALUE)
    except OSError:
        return None
    return bool(value)


def disable_spotlight() -> bool:
    """Désactive Spotlight pour l'utilisateur courant (HKCU : aucune élévation requise)."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - hors Windows
        return False
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, SPOTLIGHT_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, SPOTLIGHT_VALUE, 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "RotatingLockScreenOverlayEnabled", 0, winreg.REG_DWORD, 0)
    except OSError as exc:
        log.error("Impossible de désactiver Spotlight (%s)", exc)
        return False
    log.info("Windows Spotlight désactivé")
    return True


def remember_original(state: dict[str, Any], output: Path) -> None:
    """Mémorise l'écran de verrouillage d'origine une seule fois, pour permettre --restore."""
    if state.get("original_lockscreen"):
        return
    current = query_current_lockscreen()
    if not current:
        return
    # Ne jamais mémoriser notre propre image ni la copie interne de Windows.
    normalized = current.casefold()
    if "systemdata" in normalized or normalized == str(output).casefold():
        return
    state["original_lockscreen"] = current
    log.info("Écran de verrouillage d'origine mémorisé : %s", current)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Applique un écran de verrouillage aléatoire tagué capitale/pays."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Dossier des images (défaut : data/wallpaper)",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_FILE,
        help="Cache Wikidata fournissant les libellés",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Chemin du JPEG tagué généré"
    )
    parser.add_argument("--capital", help="Forcer une capitale (test de rendu)")
    parser.add_argument("--country-code", help="Forcer un pays par code ISO-3")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Générer l'image sans toucher à l'écran de verrouillage",
    )
    parser.add_argument(
        "--margin-x",
        type=float,
        default=DEFAULT_MARGIN_X,
        help="Marge droite, en fraction de la largeur",
    )
    parser.add_argument(
        "--margin-y",
        type=float,
        default=DEFAULT_MARGIN_Y,
        help="Marge basse, en fraction de la hauteur",
    )
    parser.add_argument(
        "--no-history", action="store_true", help="Ne pas enregistrer le tirage dans l'historique"
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restaurer l'écran de verrouillage d'origine et quitter",
    )
    parser.add_argument(
        "--disable-spotlight",
        action="store_true",
        help="Désactiver Windows Spotlight (HKCU) puis continuer",
    )
    parser.add_argument("--verbose", action="store_true", help="Journalisation détaillée")
    return parser.parse_args(argv)


def do_restore(state: dict[str, Any]) -> int:
    original = state.get("original_lockscreen")
    if not original:
        log.error("Aucun écran de verrouillage d'origine mémorisé")
        return 1
    path = Path(original)
    if not path.is_file():
        log.error("L'image d'origine n'existe plus : %s", path)
        return 1
    if not apply_lockscreen(path):
        return 1
    log.info("Écran de verrouillage restauré : %s", path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    state = load_state()

    if args.restore:
        return do_restore(state)

    if args.disable_spotlight:
        disable_spotlight()
    elif spotlight_enabled():
        log.warning(
            "Windows Spotlight est actif et remplacera l'image : "
            "relancer avec --disable-spotlight"
        )

    if not args.source_dir.is_dir():
        log.error("Dossier d'images introuvable : %s", args.source_dir)
        return 1

    images = list_images(args.source_dir)
    if not images:
        log.error("Aucune image dans %s — lancer download_wallpapers.py d'abord", args.source_dir)
        return 1

    candidates = filter_images(images, args.capital, args.country_code)
    if not candidates:
        log.error("Aucune image ne correspond aux filtres demandés")
        return 1

    index = build_index(args.cache_file)
    history = [item for item in state.get("history", []) if isinstance(item, str)]
    chosen = pick_image(candidates, history)
    labels = labels_for(chosen.stem, index)

    try:
        render(chosen, labels, args.output, args.margin_x, args.margin_y)
    except (OSError, ValueError) as exc:
        log.error("Échec du rendu de %s (%s)", chosen.name, exc)
        return 1

    primary, secondary = labels.lines()
    log.info("Sélection : %s%s", primary, f" ({secondary})" if secondary else "")

    if args.dry_run:
        log.info("Mode --dry-run : écran de verrouillage inchangé, image dans %s", args.output)
    else:
        remember_original(state, args.output)
        if not apply_lockscreen(args.output):
            return 1
        log.info("Écran de verrouillage appliqué")

    # Un --dry-run n'a rien affiche : il ne doit pas consommer une capitale, sinon
    # regler les marges par tatonnement viderait la fenetre d'exclusion.
    if not args.no_history and not args.dry_run:
        history.append(chosen.stem)
        state["history"] = history[-HISTORY_MAX:]
    state["last_applied"] = {
        "stem": chosen.stem,
        "capital": labels.capital,
        "country": labels.country,
        "dry_run": bool(args.dry_run),
    }
    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log.warning("Interrompu par l'utilisateur")
        raise SystemExit(130) from None
