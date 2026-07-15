# Quantification

Outil de quantification cellulaire sur coupes histologiques rat, combinant
imagerie Zeiss (`.czi`), atlas de référence (MRI/atlas) et pipeline de
segmentation/détection de noyaux (QuPath, deux passes). Interface graphique
Tkinter en 4 fenêtres (aperçu → masque → quantification → validation).

---

## Table des matières

- [Présentation](#présentation)
- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Lancement](#lancement)
- [Parcours utilisateur (4 fenêtres)](#parcours-utilisateur-4-fenêtres)
- [Structure du dépôt](#structure-du-dépôt)
- [Détails techniques](#détails-techniques)
- [Scripts utilitaires](#scripts-utilitaires)
- [Tests](#tests)
- [Maintenance](#maintenance)
- [À savoir (fichiers non versionnés)](#à-savoir-fichiers-non-versionnés)
- [Licence & crédits](#licence--crédits)

---

## Présentation

Le projet transforme une image Zeiss `.czi` (mosaïque de coupes coronales de
rat) en une **carte de densité cellulaire** alignée sur un atlas de référence,
via quatre étapes successives pilotées par une interface Tkinter :

1. **Aperçu** — chargement du `.czi`, visualisation des coupes, choix de la
   coupe à traiter.
2. **Masque** — délimitation de la zone d'intérêt (ROI) + alignement sur
   l'atlas (quadrants TL/TR/BL/BR).
3. **Quantification** — appel à QuPath (deux passes : détection puis
   quantification) pour compter les noyaux dans la ROI.
4. **Validation** — vérification visuelle, export CSV volumétrique.

Le code est organisé en packages (`app/`, `screens/`, `workers/`) ; `ui.py`
n'est plus qu'un point d'entrée qui amorce `App` sur `Window1Screen`.

---

## Architecture

```
ui.py                      → Point d'entrée (amorce App + Window1Screen)
app/
  app.py                   → App : possède tk.Tk(), gère le switch de vues (show/destroy)
  state.py                 → AppState : état partagé (chemin dossier .czi, ROI, masque…)
  base_screen.py           → BaseScreen : classe mère des 4 écrans (build/on_show/destroy)
  common_widgets.py        → Widgets réutilisables (boutons, labels, sliders…)
  image_utils.py           → Helpers image (chargement/redimensionnement PIL↔Tkinter)
  theme.py                 → Constantes de thème (couleurs, polices)
  __init__.py              → expose App, AppState
screens/
  window1_preview.py       → Fenêtre 1 : aperçu .czi / sélection de coupe
  window2_mask.py          → Fenêtre 2 : ROI + alignement atlas
  window3_quantify.py      → Fenêtre 3 : lancement QuPath (2 passes)
  window4_validate.py      → Fenêtre 4 : validation + export CSV
workers/
  czi_converter.py         → Worker de conversion .czi → JPEG (thread)
  __init__.py
tests/
  conftest.py              → fixtures pytest
  test_image_utils.py      → tests unitaires image_utils
  smoke_launch.py          → test de lancement (smoke)
  __init__.py
convert_czi_to_jpeg.py     → Conversion d'un .czi en tableau JPEG/array
mask_replacer.py           → Remplacement/édition de masque (alignement atlas)
atlas_position_getter.py   → Calcul de la position dans l'atlas (coordonnées)
quantification_wrapper.py  → Wrapper d'appel à QuPath (deux passes : detect + quantify)
atlas.temp.py              → Script temporaire d'exploration atlas (référence)
install.bat                → Crée le venv + installe les dépendances
lunch.bat                  → Lance l'appli dans le venv
```

**Flux de contrôle** : `App` détient le `tk.Tk()` racine. Chaque écran est une
sous-classe de `BaseScreen` construite dans son propre `frame`. `App.show()`
détruit proprement l'écran courant puis construit le suivant — pas de
`winfo_children()` global, donc pas de fuite de widgets. `AppState` transporte
les données entre les fenêtres (chemin du dossier, coupe choisie, masque,
résultats QuPath).

---

## Prérequis

- **Windows 10/11** (scripts `.bat` conçus pour Windows).
- **Python 3.12** (détecté/installé automatiquement par `install.bat`).
- **QuPath** installé séparément pour la quantification (le wrapper l'appelle
  en ligne de commande). Voir *Détails techniques*.
- Connexion internet lors de la première installation (téléchargement des
  paquets pip et, le cas échéant, de Python).

---

## Installation

Double-cliquez sur **`install.bat`** (à la racine de `Quantification_replacement`).

Ce que fait le script :

1. Détecte Python 3.12 (via `python` puis le lanceur `py -3.12`).
2. Si absent, tente une installation automatique (winget, sinon
   téléchargement de l'installateur officiel).
3. Crée un environnement virtuel `.venv/` dans le dossier du projet.
4. Active le venv, met à jour pip, installe les dépendances :
   `numpy`, `Pillow`, `matplotlib`, `nibabel`, `scikit-image`,
   `aicsimageio[czi]`, `aicspylibczi>=3.1.1`.

> Le venv est **régénérable** : il est ignoré par git (`.gitignore`). En cas de
> souci, supprimez `.venv/` et relancez `install.bat`.

---

## Lancement

Double-cliquez sur **`lunch.bat`** (à la racine). Il active le venv puis lance
`python ui.py`. La console reste ouverte pour afficher tout traceback éventuel.

Alternative manuelle :

```bat
call .venv\Scripts\activate.bat
python ui.py
```

---

## Parcours utilisateur (4 fenêtres)

| Fenêtre | Rôle | Sorties typiques |
|---------|------|------------------|
| **1 — Aperçu** | Charger le `.czi`, naviguer dans les coupes, choisir la coupe | Coupe sélectionnée dans `AppState` |
| **2 — Masque** | Dessiner la ROI, aligner sur l'atlas (quadrants TL/TR/BL/BR) | Masque + paramètres d'alignement |
| **3 — Quantification** | Lancer QuPath (détection puis quantification) dans un worker thread | Coordonnées des noyaux détectés |
| **4 — Validation** | Vérifier visuellement, corriger si besoin, exporter | `output/` : CSV volumétrique |

---

## Structure du dépôt

```
Quantification_replacement/
├── ui.py                  # point d'entrée
├── app/                   # cœur applicatif (App, AppState, BaseScreen, widgets, thème)
├── screens/               # les 4 fenêtres (Window1..4)
├── workers/               # workers thread (conversion .czi)
├── tests/                 # tests pytest (smoke + unitaires)
├── convert_czi_to_jpeg.py # conversion .czi → JPEG/array
├── mask_replacer.py       # édition/alignement de masque
├── atlas_position_getter.py
├── quantification_wrapper.py
├── atlas.temp.py          # exploration atlas (script temporaire)
├── install.bat / lunch.bat
├── .gitignore
└── README.md
```

Dossiers **ignorés par git** (non versionnés, régénérables ou lourds) :
`.venv/`, `__pycache__/`, `output/`, `input/`, `WorkInProgress/`,
`AtlasImgs/`, `Rat atlas/`, `ProjetQuantification.temp/`, et tous les binaires
(`*.czi`, `*.png`, `*.jpg`, `*.pdf`, `*.xlsx`…).

---

## Détails techniques

### Lecture des `.czi`
`convert_czi_to_jpeg.py` utilise `aicspylibczi` / `aicsimageio` pour lire les
mosaïques Zeiss. La lecture ne passe `Z=` que si le fichier possède réellement
une dimension Z (sinon `read_mosaic` lève « Coordinate for dimension 'Z' is not
expected »). Les images sont redimensionnées via PIL en préservant le ratio.

### Alignement atlas
`atlas_position_getter.py` et `mask_replacer.py` calculent la position de la
coupe dans l'atlas de référence (MRI/atlas rat) et alignent la ROI via 4
quadrants (TL = haut-gauche MRI/atlas, TR = haut-droit histologie, BL =
bas-gauche atlas, BR = bas-droit alignment).

### Quantification (QuPath, deux passes)
`quantification_wrapper.py` orchestre QuPath en **deux passes** :
1. **Détection** des noyaux (modèle/segmentation).
2. **Quantification** des objets détectés (mesures, comptage).

Le lancement se fait dans un worker thread (`workers/czi_converter.py` pour la
conversion ; la quantification utilise également un thread pour ne pas geler
l'interface) afin de garder l'UI réactive.

### Export
La fenêtre 4 exporte un **CSV volumétrique** (densité cellulaire par région)
dans `output/`.

---

## Scripts utilitaires

| Script | Fonction |
|--------|----------|
| `convert_czi_to_jpeg.py` | Convertit un `.czi` en tableau/image JPEG. CLI utilisable seul. |
| `mask_replacer.py` | Édite/remplace un masque et gère l'alignement atlas. |
| `atlas_position_getter.py` | Calcule la position dans l'atlas pour une coupe donnée. |
| `quantification_wrapper.py` | Wrapper d'appel à QuPath (2 passes). |
| `atlas.temp.py` | Script d'exploration atlas (référence temporaire, non utilisé en prod). |

Ces scripts peuvent être lancés individuellement (ex. `python convert_czi_to_jpeg.py`)
si leurs dépendances sont satisfaites dans le venv.

---

## Tests

Le projet utilise **pytest** (fixtures dans `tests/conftest.py`).

```bat
call .venv\Scripts\activate.bat
python -m pytest tests/ -v
```

- `tests/test_image_utils.py` — tests unitaires des helpers image.
- `tests/smoke_launch.py` — test de lancement (smoke) de l'application.
- `tests/__init__.py` / `tests/conftest.py` — initialisation et fixtures.

---

## Maintenance

### Règles de code
- **Docstrings** : tout le code actif est documenté au format **Google**
  (sections `Args:` / `Returns:`). Voir `app/`, `screens/`, `workers/`,
  `tests/` et les scripts racine.
- **Encodage** : fichiers en UTF-8 (scripts `.bat` en page de code 65001).
- **Packages** : la logique UI vit dans `app/` + `screens/` ; `ui.py` reste un
  simple amorçage.

### Régénérer le venv
Supprimez `.venv/` puis relancez `install.bat`.

### Ajouter une dépendance
Ajoutez-la dans la ligne `pip install ...` de `install.bat` **et** installez-la
dans le venv actif (`python -m pip install <pkg>`), sinon l'installation
propre ne la inclura pas.

### Débogage
- Lancez `ui.py` depuis une console (ou via `lunch.bat` qui garde la fenêtre
  ouverte) pour voir les tracebacks.
- Les workers tournent en thread : vérifiez les files d'attente (queue) et les
  callbacks pour diagnostiquer un blocage d'UI.

### Fichiers générés par l'IA (à conserver ou supprimer)
`Quantification_replacement/_add_google_docstrings.py` est un outil interne
ayant servi à insérer les docstrings Google. Il n'est **pas** requis pour
lancer l'application et peut être supprimé. Idem pour `_repair_orphan_docstrings.py`
et `_check_shadow.ps1` (outils de diagnostic ponctuels).

---

## À savoir (fichiers non versionnés)

- **`ui_backup.py` / `ui_original_full.py`** : doublons historiques de l'ancien
  `ui.py` monolithique (3357 lignes), conservés comme référence/sauvegarde.
  Ils ne font pas partie du code exécuté et ne sont **pas** commentés. Pour
  récupérer une version propre, un backup complet est disponible dans
  `C:\Users\Lioba\Documents\Quantification_backup\Quantification_replacement\`
  (version antérieure à l'actuel refactoring).
- **`ProjetQuantification.temp/`** : doublon historique ignoré par git.

---

## Licence & crédits

Projet de quantification histologique rat (stage / recherche). Dépendances
tierces : NumPy, Pillow, matplotlib, nibabel, scikit-image, aicsimageio,
aicspylibczi, QuPath (quantification). Voir les licences respectives des
bibliothèques utilisées.
