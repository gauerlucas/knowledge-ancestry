# -*- coding: utf-8 -*-
"""
Boîte à outils commune aux trois étapes.

On y trouve, dans l'ordre :
    1. les couleurs et les polices ;
    2. la lecture de config.json ;
    3. la manipulation des noms d'auteurs ;
    4. l'échelle du temps (année -> rayon) ;
    5. la mise en page de la rosace (angle de chaque travail) ;
    6. le dessin des fils ;
    7. le placement des étiquettes, qui est la partie la plus délicate.

Rien ici ne touche au réseau : seule l'étape 1 interroge Crossref et OpenAlex.
"""
import collections
import copy
import glob
import json
import os
import re
import time
import unicodedata

import numpy as np

ICI = os.path.dirname(os.path.abspath(__file__))


# =====================================================================================
# 1. Couleurs et polices
# =====================================================================================
# Fond blanc, encre presque noire, gris pour tout ce qui est secondaire. Les anneaux des
# décennies sont volontairement pâles : ils servent de repère sans se disputer l'attention
# avec les fils.
FOND = "#FFFFFF"
ENCRE = "#1F2328"
GRIS = "#6B7280"
GRIS_DESCRIPTEUR = "#4B5563"
ANNEAU = "#CFD3DA"
ANNEAU_FORT = "#B9BEC7"
GRIS_ANNEES = "#6F7580"
GRIS_MAIN = "#374151"      # couleur de l'annotation « vous êtes ici »

# Douze couleurs assez sourdes pour cohabiter sur fond blanc. Les sept premières sont
# celles de ma figure ; au-delà, mieux vaut fusionner des thèmes que d'en ajouter.
PALETTE = ["#4A6FB5", "#2F94A3", "#C65A3B", "#B8457E", "#9A6A3E", "#D1A02A", "#7B5CA8",
           "#4E8F5A", "#8A6D9F", "#C27C3A", "#5C7F99", "#A14F5C"]


def charger_polices():
    """Déclare à matplotlib les polices du dossier polices/ et renvoie leurs noms.

    Roboto Light sert aux descripteurs et au titre, Roboto Regular au texte courant,
    Roboto Medium aux noms, Caveat à l'annotation manuscrite. Si le dossier a disparu,
    on se rabat sur DejaVu Sans, moins élégante mais toujours présente.
    """
    import logging
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    # matplotlib s'inquiète à chaque texte de ne pas trouver de « Roboto Light » de graisse normale :
    # c'est voulu (chaque graisse est déclarée comme une famille à part), on le fait taire.
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

    familles = {}
    for f in glob.glob(os.path.join(ICI, "polices", "*.ttf")):
        fm.fontManager.addfont(f)
        familles[os.path.basename(f)] = fm.FontProperties(fname=f).get_name()   # nom interne de la police
    if not any(n.startswith("Roboto") for n in familles.values()):
        print("Polices Roboto introuvables : la figure utilisera DejaVu Sans.")
        return dict(fine="DejaVu Sans", normale="DejaVu Sans", grasse="DejaVu Sans", main="DejaVu Sans")

    def nom(fichier, defaut):
        return familles.get(fichier, defaut)
    polices = dict(fine=nom("Roboto-Light.ttf", "Roboto"), normale=nom("Roboto-Regular.ttf", "Roboto"),
                   grasse=nom("Roboto-Medium.ttf", "Roboto"), main=nom("Caveat-Medium.ttf", nom("Roboto-Light.ttf", "Roboto")))
    # DejaVu en second choix : matplotlib y va chercher les caractères absents de Roboto
    # (un nom vietnamien, un symbole exotique dans un titre) au lieu d'afficher des carrés.
    plt.rcParams["font.family"] = [polices["normale"], "DejaVu Sans"]
    return polices


# =====================================================================================
# 2. config.json
# =====================================================================================
# Toutes les clefs reconnues, avec leur valeur par défaut. Le README les décrit une par une.
CONFIG_DEFAUT = {
    "langue": "fr",                    # "fr" ou "en" : « et » / « and », « vous êtes ici » / « you are here »
    "titre": "Généalogie d’une bibliothèque",
    "sous_titre": ["Les travaux sur lesquels elle repose", "et ceux dont ils procèdent"],
    "signature": "",                   # pied de page du poster et de l'A4, par exemple "Prénom Nom — 2026"
    "annee_centre": None,              # année placée au centre ; None = calculée sur le corpus
    "themes": [],                      # [{"id": 0, "nom": "...", "couleur": "#4A6FB5"}, ...] dans le sens horaire depuis midi
    "fusionner": {},                   # {"5": 2} : le thème 5 est versé dans le thème 2
    "jalons": [],                      # [{"id": "10.1038/30918", "nom": "Watts et Strogatz", "quoi": "Modèle small-world"}, ...]
    "toujours_nommer": [],             # identifiants à étiqueter quoi qu'il arrive (sans descripteur)
    "jamais_nommer": [],               # identifiants à ne jamais étiqueter (revues de synthèse, éditoriaux...)
    "outils": {"ids": [], "noms": ["Benjamini", "Hochberg", "Bonferroni", "Holm"]},
    "mes_articles": [],                # DOI de vos propres articles, pour la version « vous êtes ici »
    "renommer": {},                    # {"10.xxxx/yyyy": "Nom correct"} pour corriger une étiquette
}


def lire_config(chemin="config.json"):
    """Lit config.json en complétant les clefs absentes par leur valeur par défaut."""
    cfg = copy.deepcopy(CONFIG_DEFAUT)
    if os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            try:
                lu = json.load(f)
            except json.JSONDecodeError as e:
                # L'erreur la plus fréquente : une virgule oubliée, ou une virgule de trop après le dernier élément.
                raise SystemExit(f"{chemin} n'est pas un JSON valide, ligne {e.lineno}, colonne {e.colno} : {e.msg}.\n"
                                 "Vérifiez les virgules et les guillemets autour de cet endroit.")
        cfg.update({k: v for k, v in lu.items() if not k.startswith("_")})
    if isinstance(cfg["sous_titre"], str):
        cfg["sous_titre"] = [cfg["sous_titre"]]
    cfg["mes_articles"] = [nettoyer_doi(d) for d in cfg["mes_articles"]]
    return cfg


def nettoyer_doi(doi):
    """'https://doi.org/10.1038/30918' -> '10.1038/30918' (toujours en minuscules)."""
    doi = (doi or "").strip()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.lower()


# =====================================================================================
# 3. Noms d'auteurs
# =====================================================================================
def ascii_minuscule(s):
    """'Erdős' -> 'erdos' : sert de clef de comparaison, jamais d'affichage."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


# Particules qui font partie du nom de famille quand elles précèdent le dernier mot.
PARTICULES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "dos", "das", "le", "la", "ten", "ter", "bin", "al", "el"}


def nom_de_famille(nom_complet):
    """Extrait un nom de famille lisible d'une chaîne d'auteur, quelle qu'en soit la forme.

    'Le Bihan, D.' -> 'Le Bihan' ; 'D. J. Watts' -> 'Watts' ; 'Martijn P. van den Heuvel'
    -> 'van den Heuvel'. Les références sont saisies de façon très hétérogène : on vise
    juste, pas parfait.
    """
    a = (nom_complet or "").strip()
    # Les bases bibliographiques remplissent parfois le champ auteur par défaut : on l'ignore.
    if not a or re.match(r"(?i)^(unspecified|unknown|anonymous|anonyme|none|null|n/?a|et al\.?)$", a):
        return ""
    if "," in a:                                   # forme « Nom, Prénom »
        return a.split(",")[0].strip()
    a = re.sub(r"^(?:[A-Z]{1,3}\.?\s+)+", "", a)   # initiales en tête : « D. J. Watts »
    a = re.sub(r"(?:\s+[A-Z]{1,3}\.?)+$", "", a)   # initiales en queue : « Watts DJ »
    mots = a.split()
    if len(mots) <= 1:
        return a
    # Forme « Prénom(s) [particules] Nom » : on remonte depuis la fin tant qu'on croise des particules.
    k = len(mots) - 1
    while k > 0 and mots[k - 1].lower() in PARTICULES:
        k -= 1
    return " ".join(mots[k:])


def clef_auteur(etiquette):
    """Clef du premier auteur d'une étiquette : 'Watts et Strogatz' -> 'watts'."""
    premier = re.split(r"\s+(?:et|and)\s+|,", etiquette or "")[0]
    return ascii_minuscule(premier.replace(" al.", ""))


def etiquette_auteurs(noms):
    """['Watts', 'Strogatz'] -> 'Watts et Strogatz' ; trois auteurs ou plus -> 'Fox et al.'

    On garde « et al. » en français comme en anglais ; la traduction de « et » en « and »
    se fait au moment du dessin (voir en_langue).
    """
    noms = [n for n in noms if n]
    if not noms:
        return None
    if len(noms) == 1:
        return noms[0]
    if len(noms) == 2:
        return f"{noms[0]} et {noms[1]}"
    return f"{noms[0]} et al."


def en_langue(etiquette, langue):
    """En anglais, 'Watts et Strogatz' devient 'Watts and Strogatz' ; 'et al.' ne bouge pas."""
    if langue == "fr" or not etiquette:
        return etiquette
    return re.sub(r"\bet\b(?! al\.)", "and", etiquette)


# =====================================================================================
# 4. Échelle du temps
# =====================================================================================
class EchelleTemps:
    """Convertit une année en rayon : le centre est l'année la plus ancienne, le bord l'année la plus récente.

    Une échelle purement linéaire écrase les dernières décennies, où se trouve presque tout
    le corpus. On mélange donc 55 % de linéaire et 45 % de rang (la proportion du corpus
    publiée avant cette année) : les périodes denses sont dilatées, les périodes creuses
    resserrées, sans que l'ordre des années soit jamais trahi. Les travaux antérieurs à
    l'année du centre sont tassés dans un petit disque central.
    """

    def __init__(self, annees, annee_centre=None, rayon_centre=0.06):
        a = np.array([y for y in annees if y], dtype=float)
        if annee_centre:
            self.y0 = int(annee_centre)
        else:
            # Le premier millième du corpus, arrondi à la décennie : quelques précurseurs
            # isolés ne doivent pas imposer l'année du centre.
            self.y0 = max(1700, int(np.percentile(a, 0.7) // 10 * 10))
        self.y1 = int(min(np.ceil(a.max()), time.localtime().tm_year + 1))
        self.r0 = rayon_centre
        self.annees_triees = np.sort(a[a >= self.y0])

    @classmethod
    def depuis(cls, y0, y1, r0, annees_triees):
        """Reconstruit une échelle déjà calculée (l'animation la relit dans mise_en_page.pkl)."""
        e = cls.__new__(cls)
        e.y0, e.y1, e.r0, e.annees_triees = y0, y1, r0, np.asarray(annees_triees)
        return e

    def rayon(self, y):
        if y is None:
            return 0.5
        if y < self.y0:
            # Avant l'année du centre : de 0,015 (an 1700) jusqu'au petit disque central.
            return 0.015 + (self.r0 - 0.02) * max(0.0, y - 1700) / max(1.0, self.y0 - 1700)
        y = min(self.y1, y)
        lineaire = (y - self.y0) / max(1, self.y1 - self.y0)
        rang = np.searchsorted(self.annees_triees, y, side="right") / max(len(self.annees_triees), 1)
        return self.r0 + (1 - self.r0) * (0.55 * lineaire + 0.45 * rang)

    def decennies(self):
        debut = int(np.ceil(self.y0 / 10.0)) * 10
        return list(range(debut, self.y1 + 1, 10))

    def pas_des_annees(self):
        """Une année écrite tous les 20 ans si le siècle est couvert, tous les 10 ans sinon."""
        return 20 if self.y1 - self.y0 > 70 else 10


# =====================================================================================
# 5. Mise en page de la rosace
# =====================================================================================
def moyenne_angulaire(angles):
    """Moyenne d'angles (en radians), qui ne se trompe pas de côté autour de ±π."""
    return float(np.arctan2(np.mean(np.sin(angles)), np.mean(np.cos(angles))))


def mise_en_page(noeuds, liens, poussiere, cfg, graine=3, iterations_fa2=600):
    """Calcule la position de chaque travail.

    Le rayon est donné par l'année ; l'angle par le thème (un secteur par thème) puis, dans
    le secteur, par la proximité dans le graphe. Pour cette dernière, on laisse ForceAtlas2
    — ou à défaut l'algorithme de ressorts de networkx — rapprocher les travaux qui se citent,
    et on lit l'ordre angulaire de chaque thème autour de son centre de gravité.

    Renvoie un dictionnaire : positions XY, angles, secteurs, échelle du temps, couleurs,
    positions de la poussière.
    """
    import networkx as nx

    rng = np.random.default_rng(graine)
    N = {d["id"]: d for d in noeuds}

    # --- thèmes : ordre, couleurs, fusions ----------------------------------------------
    fusion = {int(k): int(v) for k, v in cfg["fusionner"].items()}
    for d in noeuds:
        t = d.get("theme", 0)
        while t in fusion:                     # une fusion peut en chaîner une autre
            t = fusion[t]
        d["theme"] = t
    ordre_cfg = [t["id"] for t in cfg["themes"] if t["id"] not in fusion]
    effectif = collections.Counter(d["theme"] for d in noeuds)
    ordre = [t for t in ordre_cfg if effectif.get(t)] + sorted(t for t in effectif if t not in ordre_cfg)
    couleur = {}
    for k, t in enumerate(ordre):
        donnee = next((x.get("couleur") for x in cfg["themes"] if x["id"] == t), None)
        couleur[t] = donnee or PALETTE[k % len(PALETTE)]
    nom_theme = {t: next((x.get("nom") for x in cfg["themes"] if x["id"] == t), f"thème {t}") for t in ordre}

    # --- rayons -------------------------------------------------------------------------
    echelle = EchelleTemps([d.get("annee") for d in noeuds] + [p.get("annee") for p in poussiere.values()],
                           annee_centre=cfg.get("annee_centre"))
    for d in noeuds:
        d["r"] = echelle.rayon(d.get("annee"))

    # --- secteurs : largeur proportionnelle à l'effectif puissance 0,35 -----------------
    # Avec une largeur strictement proportionnelle, le plus gros thème mangerait la moitié
    # du cercle ; la puissance 0,35 laisse de la place aux petits thèmes.
    interstice = np.deg2rad(2.2)
    poids = {t: effectif[t] ** 0.35 for t in ordre}
    total = sum(poids.values())
    disponible = 2 * np.pi - interstice * len(ordre)
    a = np.pi / 2                              # on part de midi et on tourne dans le sens horaire
    secteur = {}
    for t in ordre:
        largeur = disponible * poids[t] / total
        secteur[t] = (a, a - largeur)
        a -= largeur + interstice

    # --- ordre angulaire dans chaque secteur ----------------------------------------------
    graphe = nx.Graph()
    graphe.add_nodes_from(N)
    graphe.add_edges_from(liens)
    try:
        from fa2_modified import ForceAtlas2
        fa2 = ForceAtlas2(outboundAttractionDistribution=True, edgeWeightInfluence=1.0, jitterTolerance=1.0,
                          barnesHutOptimize=True, barnesHutTheta=1.2, scalingRatio=2.0, gravity=1.0, verbose=False)
        pos = fa2.forceatlas2_networkx_layout(graphe, pos=None, iterations=iterations_fa2)
    except Exception:
        # Sans ForceAtlas2, les ressorts de networkx donnent un ordre un peu moins net mais tout à fait correct.
        pos = nx.spring_layout(graphe, seed=graine, iterations=100)
    voisins = collections.defaultdict(list)
    for s, c in liens:
        voisins[s].append(c)
        voisins[c].append(s)

    angle = {}
    for t in ordre:
        ids = [d["id"] for d in noeuds if d["theme"] == t]
        P = np.array([pos[i] for i in ids])
        centre = P.mean(0)
        theta = np.arctan2(P[:, 1] - centre[1], P[:, 0] - centre[0])
        a0, a1 = secteur[t]
        for rang, k in enumerate(np.argsort(theta)):
            angle[ids[k]] = a0 + (a1 - a0) * (rang + 0.5) / len(ids)
        # Trois passes de lissage : chaque travail se rapproche de l'angle moyen de ses voisins
        # du même thème, puis on répartit de nouveau régulièrement dans le secteur.
        for _ in range(3):
            vise = {}
            for i in ids:
                proches = [angle[j] for j in voisins[i] if j in angle and N[j]["theme"] == t]
                vise[i] = 0.5 * angle[i] + 0.5 * np.mean(proches) if proches else angle[i]
            for rang, i in enumerate(sorted(ids, key=lambda i: -vise[i])):
                angle[i] = a0 + (a1 - a0) * (rang + 0.5) / len(ids)

    # --- les précurseurs ajoutés à la main suivent les travaux qui les citent ---------------
    for d in noeuds:
        if d.get("precurseur"):
            citants = [s for s in d.get("cite_par", []) if s in angle and not N[s].get("precurseur")]
            if citants:
                angle[d["id"]] = moyenne_angulaire([angle[s] for s in citants])

    # --- écarter les jalons qui se partagent une même bande d'années -----------------------
    # Deux jalons du même thème publiés à quelques années d'écart se retrouveraient l'un sur
    # l'autre, étiquettes comprises. On impose un écart angulaire minimal entre eux.
    for t in ordre:
        a0, a1 = secteur[t]
        bas, haut = min(a0, a1), max(a0, a1)
        ids = [d["id"] for d in noeuds if d["theme"] == t and (d.get("jalon") or d.get("precurseur"))]
        for _ in range(12):
            bandes = collections.defaultdict(list)
            for i in ids:
                bandes[int(N[i]["r"] / 0.05)].append(i)
            for b, membres in bandes.items():
                if len(membres) < 2:
                    continue
                r = (b + 0.5) * 0.05
                ecart = min(0.10 / max(r, 0.08), (haut - bas) / len(membres) * 0.9)
                membres.sort(key=lambda i: angle[i])
                for k in range(1, len(membres)):
                    angle[membres[k]] = max(angle[membres[k]], angle[membres[k - 1]] + ecart)
                for k in range(len(membres) - 2, -1, -1):
                    angle[membres[k]] = min(angle[membres[k]], angle[membres[k + 1]] - ecart)
                for i in membres:
                    angle[i] = min(haut - 0.004, max(bas + 0.004, angle[i]))

    XY = {i: np.array([N[i]["r"] * np.cos(angle[i]), N[i]["r"] * np.sin(angle[i])]) for i in N}

    # --- poussière : les travaux cités une ou deux fois ------------------------------------
    # Chaque grain se pose à l'angle moyen des travaux qui le citent, sur l'anneau de son année
    # (ou un peu avant son premier citant quand l'année est inconnue).
    px, py, pc, pa = [], [], [], []
    for info in poussiere.values():
        citants = [c for c in info["citants"] if c in N and N[c].get("annee")]
        if not citants:
            continue
        premier = min(N[c]["annee"] for c in citants)
        y = info.get("annee") or premier - rng.uniform(1, 14)
        y = min(y, premier - 0.3)
        th = moyenne_angulaire([angle[c] for c in citants]) + rng.normal(0, np.deg2rad(2.2))
        r = echelle.rayon(y)
        px.append(r * np.cos(th))
        py.append(r * np.sin(th))
        pc.append(couleur[N[citants[0]]["theme"]])
        pa.append(y)

    return dict(XY=XY, angle=angle, secteur=secteur, ordre=ordre, interstice=interstice, echelle=echelle,
                couleur=couleur, nom_theme=nom_theme, effectif=effectif,
                poussiere=dict(x=np.array(px), y=np.array(py), c=pc, annee=np.array(pa)))


# =====================================================================================
# 6. Les fils
# =====================================================================================
def point_de_controle(p0, p1, a0, a1):
    """Point de contrôle d'une courbe de Bézier quadratique tirée vers le centre.

    Plus les deux travaux sont éloignés en angle, plus le fil plonge vers le centre : les
    liens entre thèmes voisins restent en périphérie, ceux qui traversent la rosace passent
    près du cœur. C'est ce qui donne à la figure son aspect de rosace.
    """
    r0, r1 = np.hypot(*p0), np.hypot(*p1)
    da = (a1 - a0 + np.pi) % (2 * np.pi) - np.pi
    milieu = a0 + da / 2
    rc = min(r0, r1) * max(0.04, 0.78 - 0.75 * abs(da) / np.pi)
    return np.array([rc * np.cos(milieu), rc * np.sin(milieu)])


def fil(p0, p1, a0, a1):
    """Le fil sous forme de chemin matplotlib (pour les figures fixes)."""
    from matplotlib.path import Path
    return Path([p0, point_de_controle(p0, p1, a0, a1), p1], [Path.MOVETO, Path.CURVE3, Path.CURVE3])


def fil_points(p0, p1, a0, a1, k=9):
    """Le même fil échantillonné en k points (pour l'animation)."""
    c = point_de_controle(p0, p1, a0, a1)
    t = np.linspace(0, 1, k)[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * c + t ** 2 * p1


# =====================================================================================
# 7. Placement des étiquettes
# =====================================================================================
class Etiqueteur:
    """Pose des étiquettes horizontales près de leur point, en respectant quelques règles.

    - Une étiquette ne chevauche jamais une autre étiquette ni l'anneau d'un jalon ; elle peut
      en revanche passer sur des points ordinaires, faute de quoi le centre resterait muet.
    - Elle ne « remonte pas le temps » : elle ne s'écarte pas de plus de `tol` vers le centre,
      sans quoi Fox 1986 aurait l'air d'avoir été publié en 1960. Pour les jalons, on borne
      aussi l'écart vers le bord (`tol_out`).
    - Une étiquette de jalon qui ne touche pas son anneau est reliée à lui par un trait de
      rappel ; ce trait ne coupe ni une étiquette, ni un autre trait, ni un autre anneau.
    - Parmi les positions valides, on garde la plus proche, puis la moins tournée vers le centre.

    Toutes les distances sont en unités du disque (le bord est à 1).
    """

    def __init__(self, fig, ax, dpi, F, polices, jalons_xy, jalons_r):
        self.fig, self.ax, self.dpi, self.F, self.polices = fig, ax, dpi, F, polices
        self.rendu = fig.canvas.get_renderer()
        ax.apply_aspect()
        self.inverse = ax.transData.inverted()
        x0, x1 = ax.get_xlim()
        self.px_par_unite = ax.get_window_extent(renderer=self.rendu).width / (x1 - x0)
        self.boites = []            # boîtes (en pixels) déjà occupées
        self.traits = []            # traits de rappel déjà tracés
        self.marge = 0.007
        self.jalons_xy = np.array(jalons_xy) if len(jalons_xy) else np.zeros((0, 2))
        self.jalons_r = np.array(jalons_r) if len(jalons_r) else np.zeros(0)
        self.derniere = None

    # --- conversions ---------------------------------------------------------------------
    def rayon_marqueur(self, aire_pts2):
        """Aire d'un marqueur scatter (en points²) -> rayon en unités du disque."""
        return (np.sqrt(aire_pts2) / 2) * self.dpi / 72 / self.px_par_unite

    def boite(self, texte):
        return texte.get_window_extent(renderer=self.rendu)

    def en_donnees(self, b):
        (x0, y0), (x1, y1) = self.inverse.transform([(b.x0, b.y0), (b.x1, b.y1)])
        return x0, y0, x1, y1

    def reserver(self, boite_px):
        """Interdit une zone aux étiquettes (titre, annotation « vous êtes ici »...)."""
        self.boites.append(boite_px)

    # --- géométrie -----------------------------------------------------------------------
    def chevauche(self, b1, b2, marge=None):
        m = self.px_par_unite * self.marge if marge is None else marge
        return (min(b1.x1, b2.x1) - max(b1.x0, b2.x0) + m > 0) and (min(b1.y1, b2.y1) - max(b1.y0, b2.y0) + m > 0)

    @staticmethod
    def segments_croises(a, b, c, d):
        def sens(p, q, r):
            return (r[1] - p[1]) * (q[0] - p[0]) > (q[1] - p[1]) * (r[0] - p[0])
        return sens(a, c, d) != sens(b, c, d) and sens(a, b, c) != sens(a, b, d)

    def segment_touche_boite(self, a, b, boite_px):
        coins = [self.inverse.transform(p) for p in
                 ((boite_px.x0, boite_px.y0), (boite_px.x1, boite_px.y0), (boite_px.x1, boite_px.y1), (boite_px.x0, boite_px.y1))]
        dedans = any(coins[0][0] <= q[0] <= coins[2][0] and coins[0][1] <= q[1] <= coins[2][1] for q in (a, b))
        return dedans or any(self.segments_croises(a, b, coins[k], coins[(k + 1) % 4]) for k in range(4))

    def trait_possible(self, p, q, mon_jalon):
        """Le trait de rappel p -> q est-il libre (ni boîte, ni trait, ni anneau étranger) ?"""
        if any(self.segment_touche_boite(p, q, b) for b in self.boites):
            return False
        if any(self.segments_croises(p, q, a, b) for a, b in self.traits):
            return False
        r_moi = self.jalons_r[mon_jalon] if mon_jalon is not None else 0
        for k, (m, rr) in enumerate(zip(self.jalons_xy, self.jalons_r)):
            if k == mon_jalon or np.hypot(*(m - p)) < r_moi + rr + 0.006:
                continue                      # deux anneaux qui se touchent : rien à faire
            v, w = q - p, m - p
            t = np.clip(np.dot(w, v) / max(np.dot(v, v), 1e-12), 0, 1)
            if np.hypot(*(p + t * v - m)) < rr + 0.012:
                return False
        return True

    def positions_candidates(self, rayon_point, distance_max, vers_centre, p):
        """Positions d'ancrage à essayer, des plus proches aux plus lointaines.

        D'abord les huit positions collées au point (droite, gauche, dessus, dessous, diagonales),
        puis des couronnes de seize positions de plus en plus éloignées.
        """
        g = 0.004
        rp = rayon_point
        cand = [("left", "center", rp + g, 0), ("right", "center", -rp - g, 0),
                ("center", "bottom", 0, rp + g), ("center", "top", 0, -rp - g),
                ("left", "bottom", rp * 0.7 + g, rp * 0.7), ("right", "bottom", -rp * 0.7 - g, rp * 0.7),
                ("left", "top", rp * 0.7 + g, -rp * 0.7), ("right", "top", -rp * 0.7 - g, -rp * 0.7)]
        for dist in np.arange(0.025, distance_max + 1e-9, 0.015):
            for k in range(16):
                a = k * np.pi / 8
                ox, oy = dist * np.cos(a), dist * np.sin(a)
                ha = "left" if ox > 0.006 else "right" if ox < -0.006 else "center"
                va = "bottom" if oy > 0.006 else "top" if oy < -0.006 else "center"
                cand.append((ha, va, ox, oy))
        rn = np.hypot(*p)
        u = p / rn if rn > 1e-6 else np.array([1.0, 0.0])

        def priorite(c):
            v = np.array([c[2], c[3]])
            d = np.hypot(*v)
            radial = float(np.dot(v, u)) / max(d, 1e-9)
            return (d, radial if vers_centre else -radial)   # à distance égale, vers l'extérieur
        return sorted(cand, key=priorite)

    # --- pose d'une étiquette --------------------------------------------------------------
    def poser(self, p, texte, aire_pts2, taille, police, couleur=ENCRE, sous_texte=None, rmax=1.0,
              tol=0.03, tol_out=None, distance_max=0.08, halo=1.0, trait=False, mon_jalon=None,
              vers_centre=False, marge=0.007):
        """Essaie de poser `texte` (et éventuellement une seconde ligne `sous_texte`) près du point p.

        Renvoie l'objet texte de matplotlib, ou None si aucune position ne respecte les règles.
        """
        ax, F = self.ax, self.F
        self.marge = marge
        rp = self.rayon_marqueur(aire_pts2) + 0.003
        rn = np.hypot(*p)
        meilleur = None
        for ha, va, dx, dy in self.positions_candidates(rp, distance_max, vers_centre, p):
            for essai in (0, 1):
                t = ax.text(p[0] + dx, p[1] + dy, texte, fontfamily=police, fontsize=taille, color=couleur, ha=ha, va=va, zorder=9)
                b = self.boite(t)
                x0, y0, x1, y1 = self.en_donnees(b)
                # Le rayon « ressenti » de l'étiquette : celui du point de la ligne de nom le plus
                # proche du nœud, légèrement tiré vers le milieu de la ligne. C'est ce que l'œil
                # compare à l'anneau des années.
                proche = np.array([min(max(p[0], x0), x1), min(max(p[1], y0), y1)])
                milieu = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
                r_lu = np.hypot(*(proche + 0.2 * (milieu - proche)))
                ts = None
                if sous_texte:
                    xa = {"left": x0, "right": x1, "center": (x0 + x1) / 2}[ha]
                    ts = ax.text(xa, y0 - 0.003, sous_texte, fontfamily=self.polices["fine"], fontsize=taille - 0.8 * F,
                                 color=GRIS_DESCRIPTEUR, ha=ha, va="top", zorder=9)
                    from matplotlib.transforms import Bbox
                    b = Bbox.union([b, self.boite(ts)])
                    x0, y0, x1, y1 = self.en_donnees(b)
                coins = [(x0, y0), (x1, y0), (x0, y1), (x1, y1), ((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1)]
                r_coin_max = max(np.hypot(*c) for c in coins)

                def retirer():
                    t.remove()
                    if ts is not None:
                        ts.remove()

                if essai == 0 and rmax < r_coin_max < rmax + 0.18:
                    # L'étiquette déborde du disque : on la fait glisser vers le centre d'autant, une fois.
                    retirer()
                    u = p / max(rn, 1e-9)
                    dx -= u[0] * (r_coin_max - rmax + 0.004)
                    dy -= u[1] * (r_coin_max - rmax + 0.004)
                    continue
                valide = r_coin_max <= rmax
                if valide and rn > 0.12 and r_lu < rn - tol:
                    valide = False                    # elle paraîtrait plus ancienne que son travail
                if valide and tol_out is not None and rn > 0.12 and r_lu > rn + tol_out:
                    valide = False                    # elle paraîtrait nettement plus récente
                if valide and essai == 1 and x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
                    valide = False                    # le glissement l'a posée sur son propre point
                if valide and any(self.chevauche(b, autre) for autre in self.boites):
                    valide = False
                if valide:
                    for k, (m, rr) in enumerate(zip(self.jalons_xy, self.jalons_r)):
                        if k != mon_jalon and x0 - rr - 0.005 <= m[0] <= x1 + rr + 0.005 and y0 - rr - 0.005 <= m[1] <= y1 + rr + 0.005:
                            valide = False            # elle couvrirait l'anneau d'un autre jalon
                            break
                q = None
                if valide and trait:
                    q = np.array([min(max(p[0], x0), x1), min(max(p[1], y0), y1)])
                    longueur = np.hypot(*(q - p))
                    if longueur > rp + 0.004:
                        depart = p + (q - p) / longueur * (rp - 0.001)
                        valide = self.trait_possible(depart, q, mon_jalon)
                    else:
                        q = None                      # l'étiquette touche l'anneau : pas besoin de trait
                if not valide:
                    retirer()
                    break
                score = np.hypot(dx, dy) + 0.5 * max(0.0, rn - r_lu)
                if meilleur is None or score < meilleur[0]:
                    if meilleur:
                        meilleur[1].remove()
                        if meilleur[2] is not None:
                            meilleur[2].remove()
                    meilleur = (score, t, ts, b, q, rp)
                else:
                    retirer()
                break
            if meilleur and meilleur[0] <= np.hypot(dx, dy) - 0.02:
                break                                 # les positions suivantes sont toutes plus loin
        if meilleur is None:
            return None

        _, t, ts, b, q, rp = meilleur
        import matplotlib.patheffects as pe
        # Un liseré blanc autour des lettres les détache des fils qu'elles recouvrent.
        t.set_path_effects([pe.withStroke(linewidth=2.2 * F * halo, foreground=FOND, alpha=0.95 if halo >= 1 else 0.75)])
        if ts is not None:
            ts.set_path_effects([pe.withStroke(linewidth=2 * F * halo, foreground=FOND, alpha=0.95 if halo >= 1 else 0.75)])
        ligne, segment = None, None
        if q is not None:
            depart = p + (q - p) / np.hypot(*(q - p)) * (rp - 0.001)
            ligne, = ax.plot([depart[0], q[0]], [depart[1], q[1]], color=ENCRE, lw=0.6 * F, alpha=0.85, zorder=8.5, solid_capstyle="round")
            segment = (depart, q)
            self.traits.append(segment)
        self.boites.append(b)
        self.derniere = dict(t=t, ts=ts, ligne=ligne, boite=b, segment=segment)
        return t

    def retirer(self, trace):
        """Efface une étiquette déjà posée (utile pour tenter un autre ordre de placement)."""
        for objet in (trace["t"], trace["ts"], trace["ligne"]):
            if objet is not None:
                objet.remove()
        self.boites = [b for b in self.boites if b is not trace["boite"]]
        if trace["segment"] is not None:
            self.traits = [s for s in self.traits if s is not trace["segment"]]


def longueur_trait(trace):
    if trace is None or trace["segment"] is None:
        return 0.0
    a, b = trace["segment"]
    return float(np.hypot(*(b - a)))
