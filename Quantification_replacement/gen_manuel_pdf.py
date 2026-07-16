"""Generate a user manual PDF (English, simple, non-technical)
explaining how to use the 4 windows of the Quantification application.

Usage:
    .venv\\Scripts\\python gen_manuel_pdf.py
Output: C:/Users/Lioba/Documents/Quantification/Manuel_Quantification.pdf
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

# Soft colors (app "accent" palette)
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

S = styles  # short alias


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
    """Build a complete block for one window."""
    flow = []
    flow.append(Paragraph(f"Window {num} — {nom}", S["H2"]))
    flow.append(par(f"<b>What it is for:</b> {role}"))
    for c in contenu:
        flow.append(c)
    # Button table
    if boutons:
        data = [[Paragraph("Button", S["CelluleH"]), Paragraph("What it does", S["CelluleH"])]]
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

# ----- Title page -----
story.append(par("User Manual", "Titre"))
story.append(par("Application <b>Quantification</b> — counting cells on rat histological sections", "SousTitre"))
story.append(HRFlowable(width="100%", thickness=1.2, color=BLEU, spaceAfter=8))
story.append(par(
    "This guide explains, without technical terms, how to use the 4 windows "
    "of the application. Use the app in order, from window 1 to window 4, by "
    "clicking the <b>Next</b> button (bottom right) of each window. Take your "
    "time: at each step, the screen waits for you to finish before moving on."))
story.append(par(
    "<b>In short, the workflow is:</b>"))
story.append(puce([
    "<b>Window 1</b> — choose the images to process (section preview).",
    "<b>Window 2</b> — draw the region to measure and align it on the reference atlas.",
    "<b>Window 3</b> — launch the automatic cell counting.",
    "<b>Window 4</b> — check the result by eye and save it.",
]))
story.append(par(
    "General tip: at the bottom of each window are the navigation buttons "
    "<b>Previous</b> (go back) and <b>Next</b> (next window). If you make a "
    "mistake, you can always go back.", "Note"))

# ----- Window 1 -----
story += bloc_fenetre(
    1, "Section preview",
    "it loads your images (.czi files from the Zeiss microscope) and lets you "
    "look at them before you start. This is the starting point.",
    [
        par("On this window you see:"),
        puce([
            "<b>On the left</b>: the list of your .czi files and two small checkboxes to "
            "say where they are (in the program's « Input » folder, or in another "
            "folder of your choice).",
            "<b>Top-left of the list</b>: a « Slice depth (µm) » field where you can "
            "enter the section thickness in micrometers (default is 40).",
            "<b>On the right</b>: the large preview image of the selected section.",
        ]),
        par("<b>How to do it:</b>"),
        puce([
            "Click a file name in the left list: its preview appears on the right.",
            "If a section is made of several « slices », use the <b>Previous</b> / "
            "<b>Next</b> buttons (under the preview) to move between them.",
            "The image may take a few seconds to appear: a conversion runs "
            "automatically in the background. The « Converting... » message disappears "
            "when the image is ready.",
        ]),
        par("When you have chosen the section to process, click <b>Next</b> (bottom "
            "right) to go to the next window.", "Note"),
    ],
    [
        ("Next", "Goes to window 2 (mask). This is the main button of this window."),
        ("Previous / Next", "Navigate between the slices of the same section, under the preview."),
        (".czi in the Input folder / .czi in another folder", "Two checkboxes to say where your files are. The second opens a folder chooser."),
    ],
)

# ----- Window 2 -----
story += bloc_fenetre(
    2, "Mask drawing and alignment",
    "it shows 4 images at once (in a 2×2 square) to help you outline the "
    "region to measure and align it on the rat reference atlas.",
    [
        par("The 4 images shown (one in each corner):"),
        puce([
            "<b>Top-left (MRI)</b>: the atlas reference image. A slider below lets you "
            "choose the section depth.",
            "<b>Top-right (Histology)</b>: your histological section to measure.",
            "<b>Bottom-left (Atlas)</b>: the aligned reference atlas.",
            "<b>Bottom-right (Alignment)</b>: the alignment result, updated as you go.",
        ]),
        par("<b>The goal here is to place landmark points</b> to align your images. "
            "Here is the procedure:"),
        puce([
            "Click <b>Place markers</b> (the button turns green): your cursor becomes a cross.",
            "Click at least <b>2 points</b> in the top-left image (MRI), then on the "
            "<b>same points</b> in the top-right image (Histology). Points are numbered in order.",
            "If you make a mistake, click <b>Cancel point</b> to remove the last point placed.",
            "When your points are well placed, click <b>Replace mask</b>: the bottom-right "
            "image (Alignment) updates.",
            "To see an image better, use the <b>mouse wheel</b> to zoom, or click with the "
            "<b>middle mouse button</b> and drag to move the image. <b>Reset zoom</b> "
            "returns everything to normal size.",
        ]),
        par("<b>To go to the next section:</b> when the mask suits you, click "
            "<b>Validate slice</b>. The app saves your work and moves to the next section "
            "on its own. If there is no more section, the « Slice validated » message appears.", "Note"),
        par("If you validated a section by mistake, click <b>Cancel validation</b> "
            "to go back to the previous section.", "Note"),
    ],
    [
        ("Previous", "Returns to window 1 (preview)."),
        ("Place markers", "Activates « place points » mode (cross cursor) to align the images."),
        ("Cancel point", "Removes the last point placed."),
        ("Replace mask", "Updates the alignment image (bottom-right) with the placed points."),
        ("Reset zoom", "Returns all images to their normal size."),
        ("Validate slice", "Saves the mask and moves to the next section (green button)."),
        ("Cancel validation", "Goes back to the previous section if you validated by mistake (red button)."),
        ("Next", "Goes to window 3 (quantification)."),
    ],
)

# ----- Window 3 -----
story += bloc_fenetre(
    3, "Quantification (cell counting)",
    "it launches the automatic counting of cells (nuclei) in your sections, "
    "using the QuPath software. You have almost nothing to do: the computer works.",
    [
        par("On this window you see:"),
        puce([
            "<b>At the top</b>: the number of images detected and their location (4x JPEG source).",
            "<b>A « Progress » area</b> with two bars (overall progress and current-image "
            "progress) and status messages.",
            "<b>A « Log / triggers » area</b>: the automatic report of the counting; no need "
            "to read it in detail.",
            "<b>On the right</b>: the preview of the last detected mask (the cells found).",
        ]),
        par("<b>How to do it:</b>"),
        puce([
            "Click <b>Start quantification</b> (green button) to launch the counting.",
            "Wait: the progress bars advance on their own. The number of cells counted "
            "shows at the bottom (« Last result: X cell(s) »).",
            "At the end, the « Done: X cell(s) » message appears at the top.",
            "If no image is found, a message reminds you to first do windows 1 and 2.",
        ]),
        par("Counting can take several minutes depending on the number of images. Do not "
            "close the window; you can follow progress with the bars.", "Note"),
    ],
    [
        ("Previous", "Returns to window 2 (mask)."),
        ("Start quantification", "Launches the automatic cell counting (green button)."),
        ("Next", "Goes to window 4 (validation) once counting is done."),
    ],
)

# ----- Window 4 -----
story += bloc_fenetre(
    4, "Validation and saving",
    "it lets you check the counting result by eye, correct it if needed, "
    "and save all results (images + CSV tables).",
    [
        par("On this window you see:"),
        puce([
            "<b>In the center</b>: the section preview, with the colored regions and the "
            "detected cells (yellow dots).",
            "<b>On the right</b>: a vertical « Z » bar to scroll through the section slices, "
            "and the column of buttons.",
            "<b>At the bottom of the preview</b>: a summary text (current section, cell count…).",
        ]),
        par("<b>How to do it:</b>"),
        puce([
            "Navigate between sections with <b>Previous slide</b> / <b>Next slide</b>.",
            "Scroll through slices with the <b>Z</b> bar on the right.",
            "Click <b>Show diagram</b> to see a bar chart of cells per region instead of the "
            "image. Click again to return to the image.",
            "Check if the count looks correct. If a section is bad, click <b>Reject slide</b>: "
            "it will be re-counted automatically.",
            "When everything suits you, click <b>Validate slide</b> or <b>Save</b> to record.",
        ]),
        par("Difference between the save buttons: <b>Save</b> records into the program's "
            "« output » folder (with date and time). <b>Save to...</b> lets you choose the "
            "destination folder yourself. <b>Validate slide</b> records into a « Validation » "
            "folder.", "Note"),
        par("The <b>Next</b> button of this last window simply closes the application.", "Note"),
    ],
    [
        ("Previous", "Returns to window 3 (quantification)."),
        ("Previous slide / Next slide", "Move from one section to another."),
        ("Show diagram", "Toggles between the image and the chart (cells per region)."),
        ("Validate slide", "Saves the validated section into the « Validation » folder (green button)."),
        ("Save", "Saves the results into the program's « output » folder."),
        ("Save to...", "Opens a folder chooser to save wherever you want."),
        ("Reject slide", "Re-counts the section automatically (red button)."),
        ("Next", "Last window: closes the application."),
    ],
)

# ----- Quick help -----
story.append(Paragraph("Quick help and troubleshooting", S["H2"]))
story.append(par("A few common issues and their solutions:"))
aide = [
    [Paragraph("Symptom", S["CelluleH"]), Paragraph("What to do", S["CelluleH"])],
    [Paragraph("The preview stays empty / « Converting... »", S["Cellule"]),
     Paragraph("Wait a few seconds: the .czi conversion is automatic. "
               "Otherwise, check that your files are in the chosen folder (window 1).", S["Cellule"])],
    [Paragraph("Window 3: « No image » at launch", S["Cellule"]),
     Paragraph("Do windows 1 and 2 first: the images must have been converted "
               "and the sections validated before cells can be counted.", S["Cellule"])],
    [Paragraph("Window 4: « No slide available »", S["Cellule"]),
     Paragraph("Run windows 2 and 3 before window 4. Results must exist to be validated.", S["Cellule"])],
    [Paragraph("I picked the wrong section / point", S["Cellule"]),
     Paragraph("Use <b>Previous</b> / <b>Next</b> to go back, or "
               "<b>Cancel validation</b> / <b>Cancel point</b> depending on the window.", S["Cellule"])],
    [Paragraph("I want to restart a count from zero", S["Cellule"]),
     Paragraph("In window 4, use <b>Reject slide</b> for automatic re-counting of a section.", S["Cellule"])],
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
story.append(par("Reminder of the window order: "
                 "<b>1. Preview → 2. Mask → 3. Quantification → 4. Validation</b>. "
                 "Follow this path and each step will feel natural.", "Note"))

doc = SimpleDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=18 * mm, rightMargin=18 * mm,
    topMargin=16 * mm, bottomMargin=16 * mm,
    title="User Manual — Quantification",
    author="Assistant Hermes",
)

doc.build(story)
print(f"PDF generated: {OUT}")
print(f"Size: {OUT.stat().st_size} bytes")
