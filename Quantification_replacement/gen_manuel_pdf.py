"""Genere un manuel utilisateur PDF (francais, simple, non technique)
expliquant comment utiliser les 4 fenetres de l'application Quantification.

Usage :
    .venv\Scripts\python gen_manuel_pdf.py
Sortie : C:/Users/Lioba/Documents/Quantification/Manuel_Quantification.pdf
"""

from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem,
    Table, TableStyle, HRFlowable, KeepTogether,
)

OUT = Path(r"C:\Users\Lioba\Documents\Quantification\Manuel_Quantification.pdf")
OUT.parent.mkdir(parents=True, exist_ok=True)

# Couleurs douces (palette "accent" de l'appli)
BLEU = colors.HexColor("#2a6fb0")
VERT = colors.HexColor("#1f9d55")
ROUGE = colors.HexColor("#cc3333")
GRIS = colors.HexColor("#555555")
FOND_BLEU = colors.HexColor("#eef4fb")
FOND_GRIS = colors.HexColor("#f4f4f4")

styles = getSampleStyleSheet()

styles.add(ParagraphStyle(
    "Titre", parent=styles["Title"], fontSize=22, textColor=BLEU,
    spaceAfter=4, leading=26))
styles.add(ParagraphStyle(
    "SousTitre", parent=styles["Normal"], fontSize=11, textColor=GRIS,
    spaceAfter=10, leading=14))
styles.add(ParagraphStyle(
    "H2", parent=styles["Heading2"], fontSize=14, textColor=colors.white,
    backColor=BLEU, borderPadding=(5, 6, 5, 6), spaceBefore=12, spaceAfter=8,
    leading=18))
styles.add(ParagraphStyle(
    "H3", parent=styles["Heading3"], fontSize=11.5, textColor=BLEU,
    spaceBefore=8, spaceAfter=3, leading=14))
styles.add(ParagraphStyle(
    "Corps", parent=styles["Normal"], fontSize=10, leading=14,
    alignment=TA_LEFT, spaceAfter=5))
styles.add(ParagraphStyle(
    "Puce", parent=styles["Normal"], fontSize=10, leading=13.5))
styles.add(ParagraphStyle(
    "Note", parent=styles["Normal"], fontSize=9.5, leading=13,
    textColor=GRIS, backColor=FOND_GRIS, borderPadding=(5, 6, 5, 6),
    spaceBefore=4, spaceAfter=6))
styles.add(ParagraphStyle(
    "Btn", parent=styles["Normal"], fontSize=9.5, leading=12,
    textColor=colors.white, backColor=VERT, borderPadding=(2, 4, 2, 4)))
styles.add(ParagraphStyle(
    "BtnR", parent=styles["Normal"], fontSize=9.5, leading=12,
    textColor=colors.white, backColor=ROUGE, borderPadding=(2, 4, 2, 4)))
styles.add(ParagraphStyle(
    "Cellule", parent=styles["Normal"], fontSize=9.5, leading=12.5))
styles.add(ParagraphStyle(
    "CelluleH", parent=styles["Normal"], fontSize=9.5, leading=12.5,
    textColor=colors.white, fontName="Helvetica-Bold"))

S = styles  # alias court


def par(texte, style="Corps"):
    return Paragraph(texte, S[style])


def puce(items, style="Puce"):
    return ListFlowable(
        [ListItem(Paragraph(t, S[style]), leftIndent=6) for t in items],
        bulletType="bullet", start="•", leftIndent=14, bulletColor=BLEU,
    )


def btn_label(texte, rouge=False):
    st = S["BtnR"] if rouge else S["Btn"]
    return Paragraph(texte, st)


def bloc_fenetre(num, nom, role, contenu, boutons):
    """Construit un bloc complet pour une fenetre."""
    flow = []
    flow.append(Paragraph(f"Fenêtre {num} — {nom}", S["H2"]))
    flow.append(par(f"<b>À quoi elle sert :</b> {role}"))
    for c in contenu:
        flow.append(c)
    # Tableau des boutons
    if boutons:
        data = [[Paragraph("Bouton", S["CelluleH"]), Paragraph("Ce qu'il fait", S["CelluleH"])]]
        for nom_btn, desc in boutons:
            data.append([btn_label(nom_btn), Paragraph(desc, S["Cellule"])])
        t = Table(data, colWidths=[42 * mm, 120 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLEU),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, FOND_BLEU]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]))
        flow.append(Spacer(1, 4))
        flow.append(t)
    flow.append(Spacer(1, 6))
    return flow


story = []

# ----- Page de titre -----
story.append(par("Manuel d'utilisation", "Titre"))
story.append(par("Application <b>Quantification</b> — comptage de cellules sur coupes histologiques de rat", "SousTitre"))
story.append(HRFlowable(width="100%", thickness=1.2, color=BLEU, spaceAfter=8))
story.append(par(
    "Ce guide explique, sans termes techniques, comment se servir des 4 fenêtres "
    "de l'application. L'application s'utilise dans l'ordre, de la fenêtre 1 à la "
    "fenêtre 4, en cliquant sur le bouton <b>Next</b> (Suivant) en bas à droite de "
    "chaque fenêtre. Prenez votre temps : à chaque étape, l'écran attend que vous "
    "ayez fini avant de passer à la suite."))
story.append(par(
    "<b>En résumé, le parcours est :</b>"))
story.append(puce([
    "<b>Fenêtre 1</b> — choisir les images à traiter (aperçu des coupes).",
    "<b>Fenêtre 2</b> — dessiner la zone à mesurer et l'aligner sur l'atlas de référence.",
    "<b>Fenêtre 3</b> — lancer le comptage automatique des cellules.",
    "<b>Fenêtre 4</b> — vérifier le résultat à l'œil et l'enregistrer.",
]))
story.append(par(
    "Astuce générale : en bas de chaque fenêtre se trouvent les boutons de navigation "
    "<b>Previous</b> (retour en arrière) et <b>Next</b> (fenêtre suivante). Si vous "
    "vous trompez, vous pouvez toujours revenir en arrière.", "Note"))

# ----- Fenêtre 1 -----
story += bloc_fenetre(
    1, "Aperçu des coupes",
    "elle charge vos images (fichiers .czi issues du microscope Zeiss) et vous permet "
    "de les regarder avant de commencer. C'est le point de départ.",
    [
        par("Sur cette fenêtre, vous voyez :"),
        puce([
            "<b>À gauche</b> : la liste de vos fichiers .czi et deux petites cases à cocher pour "
            "indiquer où ils se trouvent (dans le dossier « Input » du programme, ou dans un "
            "autre dossier de votre choix).",
            "<b>En haut à gauche de la liste</b> : un champ « Slice depth (µm) » où vous pouvez "
            "indiquer l'épaisseur de la coupe en micromètres (la valeur par défaut est 40).",
            "<b>À droite</b> : la grande image d'aperçu de la coupe sélectionnée.",
        ]),
        par("<b>Comment faire :</b>"),
        puce([
            "Cliquez sur le nom d'un fichier dans la liste à gauche : son aperçu apparaît à droite.",
            "Si une coupe est composée de plusieurs « tranches », utilisez les boutons "
            "<b>Précédant</b> / <b>Suivant</b> (sous l'aperçu) pour naviguer entre elles.",
            "L'image peut mettre quelques secondes à apparaître : une conversion est lancée "
            "automatiquement en arrière-plan. Le message « Conversion en cours... » s'efface "
            "quand l'image est prête.",
        ]),
        par("Quand vous avez choisi la coupe à traiter, cliquez sur <b>Next</b> en bas à droite "
            "pour passer à la fenêtre suivante.", "Note"),
    ],
    [
        ("Next", "Passe à la fenêtre 2 (masque). C'est le bouton principal de cette fenêtre."),
        ("Précédant / Suivant", "Permettent de naviguer entre les tranches d'une même coupe, sous l'aperçu."),
        (".czi dans le dossier Input / .czi dans un autre dossier", "Deux cases à cocher pour dire où se trouvent vos fichiers. La seconde ouvre un choix de dossier."),
    ],
)

# ----- Fenêtre 2 -----
story += bloc_fenetre(
    2, "Dessin du masque et alignement",
    "elle affiche 4 images en même temps (en carré 2×2) pour vous aider à délimiter "
    "la zone à mesurer et à l'aligner sur l'atlas de référence du rat.",
    [
        par("Les 4 images affichées (une dans chaque coin) :"),
        puce([
            "<b>Haut-gauche (MRI)</b> : l'image de référence de l'atlas. Une barre coulissante "
            "(le « slider ») en dessous permet de choisir la profondeur de la coupe.",
            "<b>Haut-droit (Histology)</b> : votre coupe histologique à mesurer.",
            "<b>Bas-gauche (Atlas)</b> : l'atlas de référence aligné.",
            "<b>Bas-droit (Alignment)</b> : le résultat de l'alignement, mis à jour au fur et à mesure.",
        ]),
        par("<b>Le but ici est de poser des points de repère</b> pour aligner vos images. "
            "Voici la marche à suivre :"),
        puce([
            "Cliquez sur <b>Placer des marqueurs</b> (le bouton vire au vert) : votre curseur "
            "devient une croix.",
            "Cliquez sur au moins <b>2 points</b> dans l'image du haut-gauche (MRI), puis sur "
            "les <b>mêmes points</b> dans l'image du haut-droit (Histologie). Les points sont "
            "numérotés dans l'ordre.",
            "Si vous vous trompez, cliquez sur <b>Annuler le point</b> pour retirer le dernier "
            "point posé.",
            "Quand vos points sont bien placés, cliquez sur <b>Replacer le masque</b> : "
            "l'image du bas-droite (Alignment) se met à jour.",
            "Pour mieux voir une image, utilisez la <b>molette de la souris</b> pour zoomer, ou "
            "cliquez avec le <b>bouton du milieu</b> de la souris pour faire glisser (déplacer) "
            "l'image. <b>Réinitialiser le zoom</b> remet tout à la taille normale.",
        ]),
        par("<b>Pour passer à la coupe suivante :</b> quand le masque vous convient, cliquez sur "
            "<b>Valider la coupe</b>. L'application enregistre votre travail et passe à la coupe "
            "suivante toute seule. S'il n'y a plus de coupe, le message « Coupe validée » "
            "s'affiche.", "Note"),
        par("Si vous avez validé une coupe par erreur, cliquez sur <b>Annuler la validation</b> "
            "pour revenir à la coupe précédente.", "Note"),
    ],
    [
        ("Previous", "Retourne à la fenêtre 1 (aperçu)."),
        ("Placer des marqueurs", "Active le mode « poser des points » (curseur en croix) pour aligner les images."),
        ("Annuler le point", "Retire le dernier point posé."),
        ("Replacer le masque", "Met à jour l'image d'alignement (bas-droite) avec les points posés."),
        ("Réinitialiser le zoom", "Remet toutes les images à leur taille normale."),
        ("Valider la coupe", "Enregistre le masque et passe à la coupe suivante (bouton vert)."),
        ("Annuler la validation", "Revient à la coupe précédente si vous avez validé par erreur (bouton rouge)."),
        ("Next", "Passe à la fenêtre 3 (quantification)."),
    ],
)

# ----- Fenêtre 3 -----
story += bloc_fenetre(
    3, "Quantification (comptage des cellules)",
    "elle lance le comptage automatique des cellules (noyaux) dans vos coupes, à "
    "l'aide du logiciel QuPath. Vous n'avez quasiment rien à faire : c'est "
    "l'ordinateur qui travaille.",
    [
        par("Sur cette fenêtre, vous voyez :"),
        puce([
            "<b>En haut</b> : le nombre d'images détectées et leur emplacement (source JPEG 4x).",
            "<b>Une zone « Progression »</b> avec deux barres (avancement global et avancement de "
            "l'image en cours) et des messages d'état.",
            "<b>Une zone « Journal / triggers »</b> : c'est le compte-rendu automatique du "
            "comptage, pas besoin de le lire en détail.",
            "<b>À droite</b> : la prévisualisation du dernier masque (les cellules détectées) "
            "trouvées.",
        ]),
        par("<b>Comment faire :</b>"),
        puce([
            "Cliquez sur <b>Start quantification</b> (bouton vert) pour lancer le comptage.",
            "Attendez : les barres de progression avancent toutes seules. Le nombre de cellules "
            "comptées s'affiche en bas (« Dernier résultat : X cellule(s) »).",
            "À la fin, le message « Terminé : X cellule(s) » apparaît en haut.",
            "Si aucune image n'est trouvée, un message vous rappelle de d'abord faire les "
            "fenêtres 1 et 2.",
        ]),
        par("Le comptage peut durer plusieurs minutes selon le nombre d'images. Ne fermez pas la "
            "fenêtre ; vous pouvez suivre l'avancement grâce aux barres.", "Note"),
    ],
    [
        ("Previous", "Retourne à la fenêtre 2 (masque)."),
        ("Start quantification", "Lance le comptage automatique des cellules (bouton vert)."),
        ("Next", "Passe à la fenêtre 4 (validation) une fois le comptage terminé."),
    ],
)

# ----- Fenêtre 4 -----
story += bloc_fenetre(
    4, "Validation et sauvegarde",
    "elle permet de vérifier à l'œil le résultat du comptage, de le corriger si "
    "besoin, et d'enregistrer tous les résultats (images + tableaux CSV).",
    [
        par("Sur cette fenêtre, vous voyez :"),
        puce([
            "<b>Au centre</b> : l'aperçu de la coupe, avec les régions colorées et les cellules "
            "repérées (points jaunes).",
            "<b>À droite</b> : une barre verticale « Z » pour faire défiler les tranches de la "
            "coupe, et la colonne de boutons.",
            "<b>En bas de l'aperçu</b> : un texte récapitulatif (coupe en cours, nombre de cellules…).",
        ]),
        par("<b>Comment faire :</b>"),
        puce([
            "Naviguez entre les coupes avec <b>Lame précédente</b> / <b>Lame suivante</b>.",
            "Faites défiler les tranches avec la barre <b>Z</b> à droite.",
            "Cliquez sur <b>Afficher le diagramme</b> pour voir un graphique (barres) du nombre "
            "de cellules par région au lieu de l'image. Recliquez pour revenir à l'image.",
            "Regardez si le comptage vous semble correct. Si une coupe est mauvaise, cliquez sur "
            "<b>Rejeter la lame</b> : elle sera recomptée automatiquement.",
            "Quand tout vous convient, cliquez sur <b>Valider la lame</b> ou sur "
            "<b>Sauvegarder</b> pour enregistrer.",
        ]),
        par("Différence entre les boutons de sauvegarde : <b>Sauvegarder</b> enregistre dans le "
            "dossier « output » du programme (avec la date et l'heure). <b>Sauvegarder vers...</b> "
            "vous laisse choisir vous-même le dossier de destination. <b>Valider la lame</b> "
            "enregistre dans un dossier « Validation ».", "Note"),
        par("Le bouton <b>Next</b> de cette dernière fenêtre ferme simplement l'application.", "Note"),
    ],
    [
        ("Previous", "Retourne à la fenêtre 3 (quantification)."),
        ("Lame précédente / Lame suivante", "Passent d'une coupe à l'autre."),
        ("Afficher le diagramme", "Bascule entre l'image et le graphique (nombre de cellules par région)."),
        ("Valider la lame", "Enregistre la coupe validée dans le dossier « Validation » (bouton vert)."),
        ("Sauvegarder", "Enregistre les résultats dans le dossier « output » du programme."),
        ("Sauvegarder vers...", "Ouvre un choix de dossier pour enregistrer où vous voulez."),
        ("Rejeter la lame", "Recompte automatiquement la coupe (bouton rouge)."),
        ("Next", "Dernière fenêtre : ferme l'application."),
    ],
)

# ----- Aide rapide -----
story.append(Paragraph("Aide rapide et dépannage", S["H2"]))
story.append(par("Quelques soucis fréquents et leurs solutions :"))
aide = [
    [Paragraph("Symptôme", S["CelluleH"]), Paragraph("Que faire", S["CelluleH"])],
    [Paragraph("L'aperçu reste vide / « Conversion en cours... »", S["Cellule"]),
     Paragraph("Attendez quelques secondes : la conversion des .czi est automatique. "
               "Sinon, vérifiez que vos fichiers sont bien dans le dossier choisi (fenêtre 1).", S["Cellule"])],
    [Paragraph("Fenêtre 3 : « Aucune image » au lancement", S["Cellule"]),
     Paragraph("Faites d'abord les fenêtres 1 et 2 : les images doivent avoir été converties "
               "et les coupes validées avant de pouvoir compter les cellules.", S["Cellule"])],
    [Paragraph("Fenêtre 4 : « Aucune lame disponible »", S["Cellule"]),
     Paragraph("Lancez les fenêtres 2 et 3 avant la fenêtre 4. Les résultats doivent exister "
               "pour être validés.", S["Cellule"])],
    [Paragraph("Je me suis trompé de coupe / de point", S["Cellule"]),
     Paragraph("Utilisez <b>Previous</b> / <b>Next</b> pour revenir en arrière, ou "
               "<b>Annuler la validation</b> / <b>Annuler le point</b> selon la fenêtre.", S["Cellule"])],
    [Paragraph("Je veux repartir de zéro sur un comptage", S["Cellule"]),
     Paragraph("Dans la fenêtre 4, utilisez <b>Rejeter la lame</b> pour recomptage automatique "
               "d'une coupe.", S["Cellule"])],
]
t_aide = Table(aide, colWidths=[60 * mm, 102 * mm])
t_aide.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), BLEU),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, FOND_GRIS]),
    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
]))
story.append(t_aide)
story.append(Spacer(1, 8))
story.append(par("Rappel de l'ordre des fenêtres : "
                 "<b>1. Aperçu → 2. Masque → 3. Quantification → 4. Validation</b>. "
                 "Suivez ce cheminement et chaque étape sera naturelle.", "Note"))

doc = SimpleDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=18 * mm, rightMargin=18 * mm,
    topMargin=16 * mm, bottomMargin=16 * mm,
    title="Manuel d'utilisation — Quantification",
    author="Assistant Hermes",
)

doc.build(story)
print(f"PDF genere : {OUT}")
print(f"Taille : {OUT.stat().st_size} octets")
