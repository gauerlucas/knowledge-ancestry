# -*- coding: utf-8 -*-
"""
Étape 2 : dessiner la rosace.

    python etape2_dessiner.py                     # A4 portrait, sans titre (PNG 300 dpi + PDF)
    python etape2_dessiner.py --mode poster       # A1 avec titre, sous-titre et signature
    python etape2_dessiner.py --mode impression   # carré de 19 cm, pour une page de thèse ou de livre
    python etape2_dessiner.py --mode diapo        # 16:9, rosace à droite, seuls les jalons sont nommés
    python etape2_dessiner.py --mode nue          # carré, sans aucun texte
    python etape2_dessiner.py --mode a4 --ici     # avec vos articles et l'annotation « vous êtes ici »
    python etape2_dessiner.py --mode tout         # toutes les versions d'un coup

Le script lit graph.json et config.json (écrits par l'étape 1) et range les figures dans sortie/.
Il n'interroge aucun serveur : on peut le relancer autant de fois qu'on veut en retouchant
config.json entre deux essais.
"""
import argparse
import collections
import json
import os
import pickle

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.collections import PathCollection
from matplotlib.patches import FancyArrowPatch
from matplotlib.transforms import Bbox

from outils import (ANNEAU, ANNEAU_FORT, ENCRE, FOND, GRIS, GRIS_ANNEES, GRIS_MAIN, Etiqueteur, ascii_minuscule,
                    charger_polices, clef_auteur, en_langue, fil, lire_config, longueur_trait, mise_en_page)

# =====================================================================================
# Formats
# =====================================================================================
# W, H : taille en pouces. F : facteur d'échelle des traits et des textes (le poster est
# 2,6 fois plus grand que l'A4, tout y est donc 2,6 fois plus gros). L : marge autour du
# disque. CAP, BIG, ISO : nombre d'étiquettes ordinaires (par thème, plus gros points,
# points isolés du centre). TF : grossissement supplémentaire du texte pour l'impression
# en petit format.
FORMATS = {
    "a4":         dict(W=8.27,   H=11.69, F=1.0, TF=1.0,  CAP=8, BIG=10, ISO=8, L=1.09, etiquettes=True,  descripteurs=True,  annees=True),
    "poster":     dict(W=23.39,  H=33.11, F=2.6, TF=1.0,  CAP=8, BIG=10, ISO=8, L=1.09, etiquettes=True,  descripteurs=True,  annees=True),
    "impression": dict(W=7.48,   H=7.48,  F=1.0, TF=1.15, CAP=5, BIG=6,  ISO=4, L=1.10, etiquettes=True,  descripteurs=True,  annees=True, renforce=True),
    "diapo":      dict(W=13.333, H=7.5,   F=0.9, TF=1.0,  CAP=0, BIG=0,  ISO=0, L=1.07, etiquettes=True,  descripteurs=False, annees=False),
    "nue":        dict(W=10,     H=10,    F=1.25, TF=1.0, CAP=0, BIG=0,  ISO=0, L=1.03, etiquettes=False, descripteurs=False, annees=False),
}
# Avec l'annotation « vous êtes ici », on élargit un peu la marge pour lui faire de la place.
MARGE_ICI = {"a4": 1.12, "poster": 1.12, "impression": 1.16}


# =====================================================================================
# Préparation des données
# =====================================================================================
def preparer(graphe, cfg):
    """Assemble la liste des travaux à dessiner à partir du graphe et de la config.

    C'est ici que les jalons sont marqués, que les jalons absents du graphe sont repêchés dans
    la poussière ou ajoutés à la main (précurseurs), et que les outils sont repérés.
    """
    noeuds = [d for d in graphe["noeuds"] if d["id"] not in set(cfg["mes_articles"])]
    N = {d["id"]: d for d in noeuds}
    liens = {tuple(l) for l in graphe["liens"] if l[0] in N and l[1] in N}
    poussiere = dict(graphe["poussiere"])

    for i, nom in cfg["renommer"].items():
        if i.lower() in N:
            N[i.lower()]["nom"] = nom

    def theme_majoritaire(citants):
        votes = collections.Counter(N[c]["theme"] for c in citants if c in N)
        return votes.most_common(1)[0][0] if votes else 0

    for j in cfg["jalons"]:
        i = j["id"].lower() if j["id"].startswith("10.") else j["id"]
        if i not in N and i in poussiere:
            # Jalon cité une ou deux fois seulement par la bibliothèque : on le sort de la poussière.
            grain = poussiere.pop(i)
            citants = grain["citants"]
            N[i] = dict(id=i, biblio=False, annee=j.get("annee") or grain.get("annee"), nom=j.get("nom"), citations=j.get("citations"),
                        degre=len(citants), theme=j.get("theme", theme_majoritaire(citants)))
            noeuds.append(N[i])
            liens |= {(c, i) for c in citants if c in N}
        elif i not in N and j.get("annee"):
            # Précurseur absent de Crossref (un livre de 1736, une thèse ancienne...) : on l'ajoute à la main,
            # relié aux travaux qui le citent selon vous (« cite_par »).
            citants = [c.lower() if c.startswith("10.") else c for c in j.get("cite_par", [])]
            N[i] = dict(id=i, biblio=False, annee=j["annee"], nom=j.get("nom"), citations=j.get("citations"),
                        degre=len(citants), theme=j.get("theme", theme_majoritaire(citants)), precurseur=True, cite_par=citants)
            noeuds.append(N[i])
            liens |= {(c, i) for c in citants if c in N}
        elif i not in N:
            print(f"  jalon introuvable dans graph.json : {j['id']} (ajoutez \"annee\" et \"cite_par\" pour le créer à la main)")
            continue
        d = N[i]
        d["jalon"] = j
        if j.get("nom"):
            d["nom"] = j["nom"]
        if j.get("annee"):
            d["annee"] = j["annee"]
        if j.get("citations"):
            d["citations"] = j["citations"]

    # Les précurseurs peuvent se citer entre eux (Bloch cité par Lauterbur, lui-même jalon).
    for d in noeuds:
        if d.get("precurseur"):
            liens |= {(c, d["id"]) for c in d["cite_par"] if c in N}

    ids_outils = {x.lower() for x in cfg["outils"]["ids"]}
    noms_outils = {ascii_minuscule(x) for x in cfg["outils"]["noms"]}
    for d in noeuds:
        d["outil"] = d["id"] in ids_outils or clef_auteur(d.get("nom") or "") in noms_outils
    return noeuds, sorted(liens), poussiere


def estimateur_citations(noeuds):
    """Renvoie une fonction qui donne le nombre de citations de chaque travail, mesuré ou estimé.

    Les travaux sans compte (pas de DOI, précurseurs) sont estimés par une régression du
    logarithme des citations sur le logarithme du nombre de citants dans la bibliothèque. Les
    outils (logiciels, tests statistiques, atlas) sont toujours estimés ainsi : leurs dizaines
    de milliers de citations en feraient sinon les plus gros points de la figure.
    """
    x = [np.log(d["degre"]) for d in noeuds if d.get("citations") and d["degre"] > 0 and not d["outil"]]
    y = [np.log(d["citations"]) for d in noeuds if d.get("citations") and d["degre"] > 0 and not d["outil"]]
    pente, origine = np.polyfit(x, y, 1) if len(x) > 5 else (0.9, np.log(50))

    def citations(d):
        if d.get("outil") or not d.get("citations"):
            estime = float(np.exp(origine + pente * np.log(max(d["degre"], 1))))
            return estime * (3 if d.get("precurseur") and not d.get("citations") else 1)
        return d["citations"]
    return citations


def taille_point(citations, F):
    """Aire du point (en points²) : racine des citations, avec un frein au-delà de 20 000."""
    c = max(citations, 0)
    frein = 0.9 * np.log(c / 20000) if c > 20000 else 0
    return (1.6 + 0.33 * np.sqrt(min(c, 20000)) + frein) * F * F


# =====================================================================================
# Texte espacé (titre, sous-titre, signature)
# =====================================================================================
def texte_espace(fig, x, y, texte, taille, espacement, largeur_max=0.88, **kw):
    """Écrit un texte centré en espaçant les lettres (espacement en fraction de la taille du corps).

    matplotlib ne sait pas espacer les lettres : on pose donc chaque caractère l'un après
    l'autre, à partir de leur largeur mesurée. Un texte plus large que `largeur_max` (en
    fraction de la page) est réduit d'autant : un titre long ne sort pas de la feuille.
    """
    rendu = fig.canvas.get_renderer()
    largeur_fig = fig.get_size_inches()[0] * fig.dpi

    def largeur(c):
        if c == " ":
            a = fig.text(0, 0, "n n", fontsize=taille, **kw)
            b = fig.text(0, 0, "nn", fontsize=taille, **kw)
            w = a.get_window_extent(rendu).width - b.get_window_extent(rendu).width
            a.remove()
            b.remove()
            return w
        t = fig.text(0, 0, c, fontsize=taille, **kw)
        w = t.get_window_extent(rendu).width
        t.remove()
        return w

    largeurs = [largeur(c) for c in texte]
    pas = espacement * taille * fig.dpi / 72
    total = sum(largeurs) + pas * (len(texte) - 1)
    if total > largeur_max * largeur_fig:
        reduction = largeur_max * largeur_fig / total
        taille *= reduction
        largeurs = [w * reduction for w in largeurs]
        pas *= reduction
    curseur = x * largeur_fig - (sum(largeurs) + pas * (len(texte) - 1)) / 2
    for c, w in zip(texte, largeurs):
        if c != " ":
            fig.text(curseur / largeur_fig, y, c, fontsize=taille, ha="left", va="center", **kw)
        curseur += w + pas


# =====================================================================================
# Dessin d'une version
# =====================================================================================
def dessiner(mode, ici, avec_titre, D, cfg, args, polices):
    P = dict(FORMATS[mode])
    if args.etiquettes is not None and P["CAP"]:
        P["CAP"] = args.etiquettes
    if ici:
        P["L"] = MARGE_ICI.get(mode, P["L"])
    titre = mode == "poster" or (mode == "a4" and avec_titre)
    F, TF, L, dpi = P["F"], P["TF"], P["L"], args.dpi
    renforce = P.get("renforce", False)
    langue = cfg["langue"]
    noeuds, liens, N, XY, angle = D["noeuds"], D["liens"], D["N"], D["XY"], D["angle"]
    secteur, ordre, interstice, echelle = D["secteur"], D["ordre"], D["interstice"], D["echelle"]
    couleur, citations = D["couleur"], D["citations"]

    def taille(i):
        return taille_point(citations(N[i]), F)

    # --- feuille et repère ---------------------------------------------------------------------
    fig = plt.figure(figsize=(P["W"], P["H"]), dpi=dpi)
    fig.patch.set_facecolor(FOND)
    if mode == "diapo":
        h = 0.96
        w = h * P["H"] / P["W"]
        ax = fig.add_axes([0.66 - w / 2, 0.02, w, h])
    elif mode in ("nue", "impression"):
        ax = fig.add_axes([0, 0, 1, 1])
    else:
        # A4 et A1 ont les mêmes proportions : une même mise en page sert aux deux formats.
        h = 0.98 * P["W"] / P["H"]
        centre_y = 0.497 if titre else 0.535
        ax = fig.add_axes([0.01, centre_y - h / 2, 0.98, h])
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_xlim(-L, L)
    ax.set_ylim(-L, L)

    jalons = [d["id"] for d in noeuds if d.get("jalon")] if P["etiquettes"] else []
    aire_anneau = {g: taille(g) * 2.6 + 26 * F * F for g in jalons}
    etiq = Etiqueteur(fig, ax, dpi, F, polices, [XY[g] for g in jalons], [])
    rayon_anneau = {g: etiq.rayon_marqueur(aire_anneau[g]) for g in jalons}
    etiq.jalons_r = np.array([rayon_anneau[g] for g in jalons]) if jalons else np.zeros(0)

    # --- anneaux des décennies et une seule série d'années ------------------------------------------
    # Les années sont écrites le long d'un seul rayon, dans l'interstice entre deux secteurs où
    # les jalons sont les plus rares (on évite celui de midi, réservé à « vous êtes ici »).
    midi = np.pi / 2 + interstice * 0.5
    interstices = [secteur[t][1] - interstice * 0.5 for t in ordre]
    angles_jalons = [angle[g] for g in jalons if 0.15 < N[g]["r"] < 0.9]

    def encombrement(a):
        return sum(np.exp(-abs((b - a + np.pi) % (2 * np.pi) - np.pi) / np.deg2rad(16)) for b in angles_jalons)
    candidats = [a for a in interstices if abs((a - midi + np.pi) % (2 * np.pi) - np.pi) > 0.05] or interstices
    rayon_annees = min(candidats, key=encombrement)
    textes_annees = []
    pas = echelle.pas_des_annees()
    for y in echelle.decennies():
        r = echelle.rayon(y)
        t = np.linspace(0, 2 * np.pi, 400)
        fort = y % 50 == 0
        ax.plot(r * np.cos(t), r * np.sin(t), color=ANNEAU_FORT if fort else ANNEAU,
                lw=(0.75 if fort else 0.55 if y % 20 == 0 else 0.38) * F, zorder=2.5)
        if P["annees"] and y % pas == 0:
            textes_annees.append(ax.text((r - 0.004) * np.cos(rayon_annees), (r - 0.004) * np.sin(rayon_annees), str(y),
                                         fontfamily=polices["normale"], fontsize=5.6 * F * TF, color=GRIS_ANNEES, ha="right",
                                         va="center", zorder=9, rotation=np.rad2deg(rayon_annees) - 90, rotation_mode="anchor",
                                         path_effects=[pe.withStroke(linewidth=2 * F, foreground=FOND, alpha=0.9)]))

    # --- poussière ----------------------------------------------------------------------------------
    pou = D["poussiere"]
    if len(pou["x"]):
        aire = (0.40 + 2.0 * np.clip(0.5 - np.hypot(pou["x"], pou["y"]), 0, 1)) * F * F
        ax.scatter(pou["x"], pou["y"], s=aire * (1.3 if renforce else 1), c=pou["c"], alpha=0.5 if renforce else 0.42,
                   linewidths=0, zorder=2)

    # --- fils : un seul type, dans la couleur du thème du travail cité ---------------------------------
    chemins = [fil(XY[s], XY[c], angle[s], angle[c]) for s, c in liens]
    teintes = [couleur[N[c]["theme"]] for s, c in liens]
    opacite = 0.10 if mode == "diapo" else 0.18 if renforce else 0.14
    ax.add_collection(PathCollection(chemins, facecolors="none", edgecolors=teintes, linewidths=0.22 * F, alpha=opacite, zorder=3))

    # --- points : un seul type, taille selon les citations -------------------------------------------
    ids = [d["id"] for d in noeuds]
    pts = np.array([XY[i] for i in ids])
    ax.scatter(pts[:, 0], pts[:, 1], s=[taille(i) for i in ids], c=[couleur[N[i]["theme"]] for i in ids],
               alpha=0.95, linewidths=0, zorder=5)
    if jalons:
        # Anneau des jalons : un liseré blanc, puis l'encre, pour qu'il se détache même dans la masse des fils.
        pj = np.array([XY[g] for g in jalons])
        aj = [aire_anneau[g] for g in jalons]
        ax.scatter(pj[:, 0], pj[:, 1], s=aj, facecolors="none", edgecolors=FOND, linewidths=2.4 * F, zorder=6.85)
        ax.scatter(pj[:, 0], pj[:, 1], s=aj, facecolors="none", edgecolors=ENCRE, linewidths=0.8 * F, alpha=0.95, zorder=6.9)

    # --- noms des secteurs --------------------------------------------------------------------------
    if mode != "nue":
        for t in ordre:
            a0, a1 = secteur[t]
            am = (a0 + a1) / 2
            deg = np.rad2deg(am) % 360
            rotation = deg - 90 if np.sin(am) >= 0 else deg + 90          # toujours lisible sans tourner la tête
            ax.text(1.045 * np.cos(am), 1.045 * np.sin(am), " ".join(D["nom_theme"][t].upper()), rotation=rotation,
                    rotation_mode="anchor", ha="center", va="center", fontfamily=polices["grasse"], fontsize=6.6 * F * TF,
                    color=couleur[t], alpha=0.7 if mode == "diapo" else 0.95, zorder=9)
            arc = np.linspace(a1, a0, 60)
            ax.plot(1.02 * np.cos(arc), 1.02 * np.sin(arc), color=couleur[t], lw=0.8 * F, alpha=0.7, zorder=9)

    # --- « vous êtes ici » ------------------------------------------------------------------------------
    # Dessiné avant les étiquettes, pour qu'elles lui laissent la place. Vos articles sont posés
    # dans l'interstice de midi, reliés en gris aux travaux qu'ils citent, entourés d'un cercle
    # tracé à main levée... ou presque.
    if ici:
        moi = [(doi, info) for doi, info in D["moi"].items()]
        moi.sort(key=lambda x: x[1].get("annee") or echelle.y1)
        points_moi = []
        for k, (doi, info) in enumerate(moi):
            r = echelle.rayon(info.get("annee") or echelle.y1) - 0.034 - 0.022 * k
            a = midi + (0.009 if k % 2 == 0 else -0.009)
            p = np.array([r * np.cos(a), r * np.sin(a)])
            points_moi.append(p)
            cites = [c for c in info["cites"] if c in XY]
            ax.add_collection(PathCollection([fil(p, XY[c], a, angle[c]) for c in cites], facecolors="none",
                                             edgecolors="#9CA3AF", linewidths=0.22 * F, alpha=0.5, zorder=4.5))
        points_moi = np.array(points_moi)
        ax.scatter(points_moi[:, 0], points_moi[:, 1], s=taille_point(12, F), c=ENCRE, alpha=0.9, linewidths=0, zorder=10)
        centre = points_moi.mean(0)
        rc = max(0.024, max(np.hypot(*(p - centre)) for p in points_moi) + 0.012)
        th = np.linspace(0, 2 * np.pi, 200)
        ax.plot(centre[0] + rc * np.cos(th), centre[1] + rc * np.sin(th), color=GRIS_MAIN, lw=0.7 * F, alpha=0.9, zorder=11)
        u = np.array([np.cos(midi), np.sin(midi)])
        v = np.array([-u[1], u[0]])
        note = centre + u * 0.09 + v * 0.085
        pointe = centre + (u * 0.55 + v * 0.8) / np.hypot(0.55, 0.8) * (rc + 0.005)
        ax.add_patch(FancyArrowPatch(note + np.array([0.004, -0.004]), pointe, arrowstyle="-|>,head_length=3,head_width=1.4",
                                     connectionstyle="arc3,rad=-0.25", color=GRIS_MAIN, lw=0.7 * F, zorder=11, mutation_scale=F))
        t_note = ax.text(note[0], note[1], "vous êtes ici !" if langue == "fr" else "you are here!", fontfamily=polices["main"],
                         fontsize=11 * F * TF, color=GRIS_MAIN, ha="left", va="bottom", zorder=11, rotation=-6)
        (bx0, by0), (bx1, by1) = ax.transData.transform([centre - rc - 0.012, centre + rc + 0.012])
        etiq.reserver(Bbox([[bx0, by0], [bx1, by1]]))
        etiq.reserver(etiq.boite(t_note))

    # --- étiquettes ------------------------------------------------------------------------------
    if P["etiquettes"]:
        poser_etiquettes(mode, P, D, cfg, etiq, jalons, aire_anneau, taille, polices)
        # Une année recouverte par une étiquette est simplement effacée : elle se lit sur l'anneau voisin.
        for t in textes_annees:
            if any(etiq.chevauche(etiq.boite(t), b, marge=2) for b in etiq.boites):
                t.remove()

    # --- titre, sous-titre, signature -------------------------------------------------------------
    if titre:
        texte_espace(fig, 0.5, 0.9325, cfg["titre"], 42 * F, 0.025, fontfamily=polices["fine"], color=ENCRE)
        fig.add_artist(plt.Line2D([0.472, 0.528], [0.9055, 0.9055], transform=fig.transFigure, color=ANNEAU_FORT,
                                  lw=0.55 * F, solid_capstyle="butt"))
        for k, ligne in enumerate(cfg["sous_titre"][:2]):
            texte_espace(fig, 0.5, 0.8865 - 0.0145 * k, ligne.upper(), 8.2 * F, 0.22, fontfamily=polices["normale"], color=GRIS)
    if cfg["signature"] and mode in ("a4", "poster"):
        if titre:
            texte_espace(fig, 0.5, 0.072, cfg["signature"].upper(), 6.6 * F, 0.22, fontfamily=polices["normale"], color=GRIS)
        else:
            fig.text(0.5, 0.095, cfg["signature"], fontfamily=polices["fine"], fontsize=8 * F, color=GRIS, ha="center", va="center")

    nom = f"rosace_{mode}" + ("_titre" if mode == "a4" and avec_titre else "") + ("_ici" if ici else "")
    base = os.path.join(args.sortie, nom)
    fig.savefig(base + ".png", dpi=dpi, facecolor=FOND)
    if dpi >= 300:
        fig.savefig(base + ".pdf", dpi=dpi, facecolor=FOND)
    plt.close(fig)
    print(f"  écrit : {base}.png" + (" et .pdf" if dpi >= 300 else ""))


def poser_etiquettes(mode, P, D, cfg, etiq, jalons, aire_anneau, taille, polices):
    """Pose, dans l'ordre : les jalons, les travaux à toujours nommer, un auteur par travail
    influent (budget par thème), les plus gros points, puis les points isolés du centre."""
    noeuds, N, XY, ordre, citations = D["noeuds"], D["N"], D["XY"], D["ordre"], D["citations"]
    F, TF = P["F"], P["TF"]
    langue = cfg["langue"]
    jamais = {i.lower() if i.startswith("10.") else i for i in cfg["jamais_nommer"]}
    nommes = set()
    auteurs_nommes = set()

    # --- 1. Jalons : nom et année en Medium, descripteur en Light, trait de rappel si besoin ------------
    # Les jalons les plus entourés d'autres jalons choisissent leur place en premier : sinon
    # leurs voisins, plus libres, leur prendraient les seules positions possibles.
    def voisins(g, rayon=0.3):
        return [h for h in jalons if h != g and np.hypot(*(XY[h] - XY[g])) < rayon]
    ordre_jalons = sorted(jalons, key=lambda g: (-len(voisins(g)), N[g]["annee"] or 0))
    traces = {}

    def poser_jalon(g, niveau=0):
        d = N[g]
        r = np.hypot(*XY[g])
        nom = en_langue(d.get("nom") or "?", langue)
        quoi = (d["jalon"].get("quoi") or None) if P["descripteurs"] else None
        # Cinq passes de plus en plus permissives : on s'éloigne davantage du point et l'on
        # tolère un écart plus grand avec l'anneau des années.
        passes = [dict(tol=0.055 if r > 0.8 else 0.045, tol_out=0.06, distance_max=0.085, vers_centre=r > 0.93, rmax=1.01),
                  dict(tol=0.06, tol_out=0.09, distance_max=0.12, vers_centre=r > 0.86, rmax=1.01),
                  dict(tol=0.06, tol_out=0.09, distance_max=0.12, rmax=1.01, marge=0.001),
                  dict(tol=9, tol_out=None, distance_max=0.16),
                  dict(tol=9, tol_out=None, distance_max=0.24)]
        for reglage in passes[niveau:]:
            if etiq.poser(XY[g], f"{nom}  {d['annee']}", aire_anneau[g], 6.6 * F * TF, polices["grasse"], sous_texte=quoi,
                          trait=True, mon_jalon=jalons.index(g), **reglage):
                traces[g] = etiq.derniere
                nommes.add(g)
                auteurs_nommes.add(clef_auteur(nom))
                return True
        return False

    echecs = [g for g in ordre_jalons if not poser_jalon(g)]
    # Réparation : un jalon resté sans place fait retirer ses voisins un à un, se pose, puis les voisins se reposent.
    for g in echecs:
        proches = sorted((h for h in traces if np.hypot(*(XY[h] - XY[g])) < 0.3), key=lambda h: np.hypot(*(XY[h] - XY[g])))
        for h in proches:
            etiq.retirer(traces.pop(h))
            nommes.discard(h)
            if poser_jalon(g):
                break
        else:
            print(f"    jalon sans place : {N[g].get('nom')} {N[g].get('annee')}")
        for h in proches:
            if h not in traces and not poser_jalon(h):
                print(f"    jalon sans place après réparation : {N[h].get('nom')} {N[h].get('annee')}")
    # Amélioration : un jalon au trait long tente de passer avant ses voisins ; on ne garde le
    # nouvel arrangement que si la longueur totale des traits du groupe diminue.
    for g in sorted(traces, key=lambda g: -longueur_trait(traces.get(g))):
        if g not in traces or longueur_trait(traces[g]) < 0.045:
            continue
        groupe = [g] + [h for h in voisins(g) if h in traces]
        avant = sum(longueur_trait(traces[h]) for h in groupe)
        for h in groupe:
            etiq.retirer(traces.pop(h))
        reussi = all(poser_jalon(h) for h in groupe)
        apres = sum(longueur_trait(traces.get(h)) for h in groupe)
        if not reussi or apres >= avant - 0.01:
            for h in groupe:
                if h in traces:
                    etiq.retirer(traces.pop(h))
            for h in sorted(groupe, key=ordre_jalons.index):
                poser_jalon(h)
    print(f"    jalons étiquetés : {len(traces)}/{len(jalons)}")

    # --- étiquette ordinaire : nom et année, sans descripteur, collée à son point ---------------------------
    def ordinaire(i):
        d = N[i]
        r = np.hypot(*XY[i])
        return etiq.poser(XY[i], f"{en_langue(d['nom'], langue)}  {d['annee']}", taille(i), 5.4 * F * TF, polices["grasse"],
                          tol=0.02, tol_out=0.05, distance_max=0.04, halo=0.8 if P.get("renforce") else 0.55, vers_centre=r > 0.9)

    def nommable(d):
        return (d["id"] not in nommes and d["id"] not in jamais and not d.get("outil") and not d.get("revue")
                and d.get("nom") and d.get("annee"))

    # --- 2. Travaux à toujours nommer -----------------------------------------------------------------------
    if mode != "diapo":
        for i in cfg["toujours_nommer"]:
            i = i.lower() if i.startswith("10.") else i
            if i in N and nommable(N[i]) and ordinaire(i):
                nommes.add(i)
                auteurs_nommes.add(clef_auteur(N[i]["nom"]))

    # --- 3. Un auteur = une étiquette, posée sur son premier travail influent ------------------------------
    # Pour chaque premier auteur, on retient son travail le plus ancien parmi ceux qui sont cités
    # au moins trois fois ; les auteurs sont ensuite classés par l'importance de leur meilleur travail.
    # Chaque thème a un budget proportionnel à la racine de sa taille, diminué de ses jalons.
    if P["CAP"]:
        candidats = collections.defaultdict(list)
        for d in noeuds:
            if not nommable(d) or not (d["degre"] >= 3 or d.get("ancetre") or d.get("precurseur")):
                continue
            a = clef_auteur(d["nom"])
            if not a or a in auteurs_nommes:
                continue
            score = np.sqrt(citations(d)) * np.log(1 + d["degre"])
            candidats[a].append((d["annee"], -score, d["id"], score))
        par_theme = collections.defaultdict(list)
        for a, liste in candidats.items():
            liste.sort()
            premier = liste[0][2]
            par_theme[N[premier]["theme"]].append((max(x[3] for x in liste), premier, a))
        effectif = D["effectif"]
        moyenne = np.mean([effectif[t] for t in ordre])
        for t in ordre:
            n_jalons = sum(1 for g in jalons if N[g]["theme"] == t)
            budget = int(np.clip(round(P["CAP"] * (effectif[t] / moyenne) ** 0.5) - max(0, n_jalons - 2), 2, P["CAP"] + 3))
            poses = 0
            for score, i, a in sorted(par_theme[t], reverse=True):
                if poses >= budget:
                    break
                if a not in auteurs_nommes and ordinaire(i):
                    nommes.add(i)
                    auteurs_nommes.add(a)
                    poses += 1

    # --- 4. Les plus gros points sont toujours nommés ---------------------------------------------------------
    gros = sorted((d for d in noeuds if nommable(d) and not d.get("precurseur")), key=lambda d: -citations(d))[:P["BIG"]]
    for d in gros:
        if ordinaire(d["id"]):
            nommes.add(d["id"])

    # --- 5. Les points isolés près du centre, qui seraient sinon des énigmes -------------------------------
    if P["ISO"]:
        ids = [d["id"] for d in noeuds]
        pts = np.array([XY[i] for i in ids])
        distances = np.sqrt(((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1))
        np.fill_diagonal(distances, np.inf)
        plus_proche = distances.min(1)
        isoles = [(citations(N[i]), i) for k, i in enumerate(ids)
                  if nommable(N[i]) and np.hypot(*pts[k]) < 0.6 and plus_proche[k] >= 0.05 and citations(N[i]) >= 400]
        for _, i in sorted(isoles, reverse=True)[:P["ISO"]]:
            if ordinaire(i):
                nommes.add(i)
    print(f"    étiquettes au total : {len(nommes)}")


# =====================================================================================
# Programme principal
# =====================================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="a4", choices=list(FORMATS) + ["tout"])
    ap.add_argument("--ici", action="store_true", help="ajouter vos articles (\"mes_articles\" dans config.json) et « vous êtes ici »")
    ap.add_argument("--titre", action="store_true", help="en A4, ajouter le titre et le sous-titre (le poster les a toujours)")
    ap.add_argument("--dpi", type=int, default=300, help="résolution (300 par défaut ; 600 pour une impression très fine ; 100 pour un aperçu rapide)")
    ap.add_argument("--etiquettes", type=int, default=None, help="nombre d'étiquettes ordinaires par thème (8 par défaut en A4 et en poster)")
    ap.add_argument("--graine", type=int, default=3, help="changer ce nombre donne une autre disposition à l'intérieur des secteurs")
    ap.add_argument("--graphe", default="graph.json")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--sortie", default="sortie")
    args = ap.parse_args()

    cfg = lire_config(args.config)
    graphe = json.load(open(args.graphe, encoding="utf-8"))
    polices = charger_polices()
    os.makedirs(args.sortie, exist_ok=True)
    graphe["moi"] = {k: v for k, v in graphe.get("moi", {}).items() if k in cfg["mes_articles"]}
    if args.ici and not graphe["moi"]:
        print("Pas d'article dans \"mes_articles\" (ou étape 1 non relancée depuis leur ajout) : la version « ici » est ignorée.")
        args.ici = False

    noeuds, liens, poussiere = preparer(graphe, cfg)
    print(f"{len(noeuds)} travaux, {len(liens)} liens. Calcul de la disposition...")
    M = mise_en_page(noeuds, liens, poussiere, cfg, graine=args.graine)
    D = dict(M, noeuds=noeuds, liens=liens, N={d["id"]: d for d in noeuds}, citations=estimateur_citations(noeuds), moi=graphe["moi"])

    if args.mode == "tout":
        versions = [("a4", False, False), ("a4", False, True), ("poster", False, False), ("impression", False, False),
                    ("diapo", False, False), ("nue", False, False)]
        if D["moi"]:
            versions += [("a4", True, False), ("impression", True, False)]
    else:
        versions = [(args.mode, args.ici and args.mode in MARGE_ICI, args.titre)]
    for mode, ici, titre in versions:
        print(f"Version {mode}{' + titre' if titre else ''}{' + vous êtes ici' if ici else ''} :")
        dessiner(mode, ici, titre, D, cfg, args, polices)

    # Données de mise en page pour l'animation (étape 3).
    ids = [d["id"] for d in noeuds]
    rang = {i: k for k, i in enumerate(ids)}
    ech = M["echelle"]
    pickle.dump(dict(ids=ids, annees=[d.get("annee") or ech.y0 for d in noeuds], couleurs=[M["couleur"][d["theme"]] for d in noeuds],
                     citations=[D["citations"](d) for d in noeuds], noms={d["id"]: d.get("nom") for d in noeuds},
                     jalons={d["id"]: d["jalon"].get("quoi", "") for d in noeuds if d.get("jalon")},
                     XY=np.array([M["XY"][i] for i in ids]), liens=[(rang[s], rang[c]) for s, c in liens],
                     poussiere=M["poussiere"], secteur=M["secteur"], ordre=M["ordre"], interstice=M["interstice"],
                     nom_theme=M["nom_theme"], couleur=M["couleur"], y0=ech.y0, y1=ech.y1, r0=ech.r0,
                     annees_triees=ech.annees_triees, langue=cfg["langue"], titre=cfg["titre"]),
                open(os.path.join(args.sortie, "mise_en_page.pkl"), "wb"))


if __name__ == "__main__":
    main()
