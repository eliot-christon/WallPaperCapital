# Fonds d'écran par capitale

Ce projet Python fait deux choses :

1. **Constituer la banque d'images** — `download_wallpapers.py` télécharge un fond
   d'écran par capitale pour les 193 États membres de l'ONU, ainsi que le Saint-Siège
   et l'État de Palestine, soit 195 États et 203 capitales (certains pays en comptent
   plusieurs, comme l'Afrique du Sud ou la Bolivie).
2. **Habiller l'écran de verrouillage** — `lockscreen.py` tire une image au hasard,
   y incruste le nom de la capitale et du pays en bas à droite, et la pose comme
   écran de verrouillage Windows à chaque ouverture de session.

La liste des pays et des capitales vient de Wikidata. Les images viennent de
Wikimedia Commons. Par défaut, elles sont converties en JPEG 2560 × 1440 et
enregistrées dans `data/wallpaper/`.

Le fond de bureau n'est jamais modifié.

## Comment le script trouve une image de la bonne ville

Chercher le nom de la ville en toutes lettres ne marche pas : l'index de texte
de Commons ignore où une photo a été prise. Une recherche sur « Algiers »
rapportait ainsi une image primée du port de Sète, et « Belmopan » une
autoroute du Belize.

Le script part donc de ce que Commons rattache vraiment à un lieu, et constitue
un vivier de candidats par ordre de fiabilité décroissante :

1. les catégories de vues de la ville, `Views of X`, `Cityscapes of X`,
   `Aerial photographs of X`, `X at night`… Elles sont sondées par leur nom,
   puis complétées par les sous-catégories de la ville qui suivent la même
   forme. Une image qui s'y trouve montre la ville par construction ;
2. l'image de référence retenue par Wikidata pour la ville (propriété P18) ;
3. les fichiers rangés directement dans la catégorie de la ville ;
4. la recherche par coordonnées, dans un rayon de sept kilomètres autour du
   centre ;
5. en dernier recours seulement, pour les capitales trop peu photographiées
   pour être catégorisées, la recherche textuelle d'autrefois.

La provenance retenue est enregistrée dans le manifeste, champ `source_kind`.

## Comment le script choisit une belle image

Le classement combine ensuite plusieurs signaux :

- la provenance ci-dessus, une catégorie de vues valant bien plus qu'un rayon
  de sept kilomètres ;
- les distinctions décernées par la communauté Commons — *featured picture*,
  *quality image*, *valued image*, *picture of the year*, *picture of the day*
  et la catégorie « Commons featured desktop backgrounds ». Leur total est
  plafonné : elles se recoupent, et sans plafond un quadruple lauréat
  l'emportait quel que soit son sujet ;
- le nombre de pages Wikimédia qui reprennent l'image, seul indice disponible
  du caractère emblématique d'une vue ;
- le nom de la ville dans le titre du fichier, qui distingue
  « Belmopan Sunset » d'un « Aerials Belize WHwy 02 » rangé dans la même
  catégorie ;
- le cadrage et la définition. Ce n'est pas la largeur du fichier qui compte,
  mais celle qui subsiste après le recadrage centré en 16:9 : un panorama
  5061 × 1200 ne laisse qu'une bande de 2133 px, là où un 7585 × 2217 en garde
  3941.

Sont écartés d'office : ce qui n'est pas une photographie (cartes, gravures,
collages, planches de livres numérisées), les vues d'intérieur, les cérémonies
et visites officielles, les scènes de guerre, les véhicules, les vues
orbitales, et les photographies antérieures à 1995, qui ne montrent plus la
ville d'aujourd'hui.

## Corriger un choix à la main

Aucune heuristique ne rattrape une capitale que Commons photographie mal. Pour
ces cas, `data/overrides.json` impose un fichier précis :

```json
{
  "Bichkek": "Bishkek City's business center.jpg",
  "Basseterre": "File:Une belle vue de Basseterre.jpg"
}
```

La clé accepte le nom français, le nom anglais ou le code ISO alpha-3 du pays ;
le préfixe `File:` est facultatif. Un fichier introuvable est signalé et la
sélection normale reprend la main. Le chemin se change avec `--overrides`.

Le script crée aussi :

- `data/capitals_wikidata.json`, le cache de la liste des pays et capitales,
  avec les coordonnées et la catégorie Commons de chaque ville ;
- `data/wallpaper/manifest.json`, avec le pays, la capitale, la source,
  l'auteur et la licence de chaque image.

## Installation

Le projet est géré avec [uv](https://docs.astral.sh/uv/). Depuis la racine du projet, une seule commande crée l'environnement virtuel et installe les dépendances aux versions verrouillées dans `uv.lock` :

```bash
uv sync
```

Le groupe `dev` (ruff) est inclus par défaut. Pour ne garder que les dépendances d'exécution :

```bash
uv sync --no-dev
```

`uv` crée l'environnement dans `.venv/`. Dans VSCode, sélectionne cet interpréteur avec **Python: Select Interpreter**, puis `.venv/Scripts/python.exe` sous Windows ou `.venv/bin/python` sous macOS et Linux.

## Utilisation

Toutes les commandes passent par `uv run`, qui garantit l'environnement à jour sans avoir à l'activer.

Tester avec trois capitales :

```bash
uv run python download_wallpapers.py --limit 3
```

Télécharger toutes les capitales :

```bash
uv run python download_wallpapers.py
```

Traiter une seule capitale, en français ou en anglais :

```bash
uv run python download_wallpapers.py --capital "Abou Dabi"
uv run python download_wallpapers.py --capital "Abu Dhabi"
```

Traiter toutes les capitales d'un pays :

```bash
uv run python download_wallpapers.py --country-code ZAF
```

Forcer le remplacement des fichiers existants :

```bash
uv run python download_wallpapers.py --force
```

N'accepter que les images distinguées par la communauté, sans repli sur une image ordinaire. Les capitales sans image primée sont alors laissées de côté :

```bash
uv run python download_wallpapers.py --quality-only
```

Conserver le fichier original au lieu de convertir en JPEG 16:9 :

```bash
uv run python download_wallpapers.py --keep-original-format
```

Les autres options sont `--output-dir`, `--cache-file`, `--overrides`, `--width`, `--results` et `--pause` ; `--help` les détaille.

Après un changement de critères de sélection, il faut ajouter `--force` pour que les images déjà téléchargées soient réévaluées.

Une exécution complète prend une trentaine de minutes, la pause entre les requêtes étant volontaire. Le script est relançable : les capitales déjà téléchargées sont ignorées, sauf avec `--force`.

## Écran de verrouillage

`lockscreen.py` choisit une image de `data/wallpaper/`, dessine en bas à droite une
pilule translucide portant `CAPITALE · Pays`, puis l'applique comme écran de
verrouillage via l'API WinRT `Windows.System.UserProfile.LockScreen`. Aucun droit
administrateur n'est nécessaire : l'API agit sur l'utilisateur courant.

Quand les libellés anglais diffèrent du français, ils apparaissent sur une seconde
ligne plus discrète (« BUDAPEST · Hongrie » puis « Hungary »).

### Installer le déclenchement au démarrage

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Install-StartupTask.ps1
```

La tâche planifiée `WallPaperCapital-LockScreen` se déclenche à chaque ouverture de
session, après 30 secondes de délai, et s'exécute sans console via `pythonw.exe`.
Pour la retirer :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Install-StartupTask.ps1 -Unregister
```

### Lancer à la main

Générer l'image sans toucher au système, pour juger du rendu :

```bash
uv run python lockscreen.py --capital Budapest --dry-run
```

Appliquer une capitale précise, ou un tirage aléatoire :

```bash
uv run python lockscreen.py --capital Budapest
uv run python lockscreen.py
```

Revenir à l'écran de verrouillage qui était en place avant la première exécution :

```bash
uv run python lockscreen.py --restore
```

Les autres options sont `--source-dir`, `--cache-file`, `--output`, `--country-code`,
`--margin-x`, `--margin-y`, `--no-history`, `--disable-spotlight` et `--verbose` ;
`--help` les détaille.

### Fichiers produits

Tout l'état vit dans `%LOCALAPPDATA%\WallPaperCapital\`, hors du dépôt : écrire une
image neuve à chaque ouverture de session dans `data/` déclencherait une
resynchronisation OneDrive inutile.

- `lockscreen.jpg` — l'image taguée effectivement appliquée ;
- `state.json` — l'historique des 40 derniers tirages, et le chemin de l'écran de
  verrouillage d'origine que `--restore` remet en place ;
- `lockscreen.log` — le journal, indispensable puisque la tâche tourne sans console.

Les 20 derniers tirages sont exclus du tirage suivant, pour éviter de revoir la même
capitale deux jours de suite.

### Ce qu'il faut savoir

Les marges par défaut placent la pilule à 7 % du bord droit et 6 % du bas. Ce n'est
pas un choix esthétique : Windows recadre l'image 16:9 en « cover » vers le format de
l'écran principal, et un écran 16:10 perd environ 5 % de la largeur de chaque côté. Un
tag collé au bord serait tout simplement coupé. Sur un écran plus étroit encore,
augmenter `--margin-x`.

Si Windows Spotlight est actif, il remplace l'image personnalisée. Le script le détecte
et le signale ; `--disable-spotlight` le désactive pour l'utilisateur courant.

Windows 11 applique un flou acrylique sur l'**écran de connexion**, celui qui apparaît
après avoir appuyé sur une touche. Le tag reste net sur l'écran de verrouillage
lui-même.

## Vérifier le code

```bash
uv run ruff check .
```

## Points importants

Le téléchargement de l'image n'emporte pas automatiquement tous les droits d'utilisation. Consulte `data/wallpaper/manifest.json` avant une utilisation publique ou commerciale, et respecte la licence indiquée pour chaque image. Le script conserve les liens de la page source et les informations d'attribution pour faciliter cette vérification.

Le script envoie un User-Agent descriptif à Wikimedia et utilise une pause entre les requêtes pour rester raisonnable vis-à-vis des API.

Les libellés français servent à l'affichage et aux noms de fichiers, mais les recherches sur Commons utilisent les libellés anglais, car c'est la langue d'indexation du site.
