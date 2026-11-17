# -*- coding: utf-8 -*-
"""
Étape 3 (facultative) : la rosace qui pousse, en vidéo.

    python etape3_animer.py                          # sortie/animation.mp4, 1920 x 1080, environ 34 s
    python etape3_animer.py --secondes 20 --largeur 1280 --hauteur 720
    python etape3_animer.py --gif                    # sans ffmpeg : un GIF, plus lourd et moins fluide

À lancer après l'étape 2, qui a écrit sortie/mise_en_page.pkl. L'année courante est toujours
au bord du cercle : les travaux apparaissent à leur date de publication puis glissent vers le
centre à mesure que le temps passe, comme les cernes d'un arbre vus depuis l'écorce. Quelques
étiquettes s'allument au passage des jalons et des travaux très cités.

Il faut ffmpeg pour écrire un MP4 (voir le README) ; compter 5 à 10 minutes de calcul pour
30 secondes en 1080p.
"""
import argparse
import os
import pickle
import subprocess

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.collections import LineCollection
from matplotlib.colors import to_rgb, to_rgba

from outils import ENCRE, FOND, GRIS_DESCRIPTEUR, EchelleTemps, charger_polices, en_langue, fil_points


class Ecriture:
    """Envoie les images une à une à ffmpeg (MP4), ou les garde pour un GIF."""

    def __init__(self, chemin, ips, largeur, hauteur, gif):
        self.chemin, self.ips, self.gif, self.images = chemin, ips, gif, []
        if not gif:
            commande = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-vcodec", "rawvideo",
                        "-s", f"{largeur}x{hauteur}", "-pix_fmt", "rgba", "-r", str(ips), "-i", "-",
                        "-an", "-vcodec", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium", chemin]
            try:
                self.ffmpeg = subprocess.Popen(commande, stdin=subprocess.PIPE)
            except FileNotFoundError:
                raise SystemExit("ffmpeg est introuvable : installez-le (voir le README) ou ajoutez --gif.")

    def ajouter(self, fig):
        fig.canvas.draw()
        image = np.asarray(fig.canvas.buffer_rgba())
        if self.gif:
            from PIL import Image
            self.images.append(Image.fromarray(image[..., :3].copy()))
        else:
            self.ffmpeg.stdin.write(image.tobytes())

    def fermer(self):
        if self.gif:
            # Une image sur deux suffit pour un GIF : il resterait sinon énorme.
            self.images[0].save(self.chemin, save_all=True, append_images=self.images[2::2], duration=int(2000 / self.ips), loop=0)
        else:
            self.ffmpeg.stdin.close()
            self.ffmpeg.wait()


def adoucir(x):
    """Rampe douce de 0 à 1 (pour les fondus)."""
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mise-en-page", default="sortie/mise_en_page.pkl")
    ap.add_argument("--secondes", type=float, default=30.0, help="durée de la croissance (4 s de pause s'y ajoutent à la fin)")
    ap.add_argument("--ips", type=int, default=30, help="images par seconde")
    ap.add_argument("--largeur", type=int, default=1920)
    ap.add_argument("--hauteur", type=int, default=1080)
    ap.add_argument("--gif", action="store_true", help="écrire un GIF (sans ffmpeg)")
    ap.add_argument("--sortie", default=None)
    args = ap.parse_args()
    if not os.path.exists(args.mise_en_page):
        raise SystemExit("Lancez d'abord etape2_dessiner.py, qui écrit sortie/mise_en_page.pkl.")
    chemin = args.sortie or os.path.join(os.path.dirname(args.mise_en_page), "animation." + ("gif" if args.gif else "mp4"))

    D = pickle.load(open(args.mise_en_page, "rb"))
    polices = charger_polices()
    langue = D["langue"]
    echelle = EchelleTemps.depuis(D["y0"], D["y1"], D["r0"], D["annees_triees"])
    XY = D["XY"]
    angle = np.arctan2(XY[:, 1], XY[:, 0])
    annees = np.array(D["annees"], dtype=float)
    couleurs = [to_rgb(c) for c in D["couleurs"]]
    n = len(D["ids"])
    ech = args.hauteur / 1080                                # tailles pensées pour du 1080p, ajustées à la hauteur choisie
    F = 2.0 * ech                                            # à 1080p, tout est deux fois plus gros qu'en A4
    aire = np.array([(1.6 + 0.33 * np.sqrt(min(max(c, 0), 20000))) * F * F for c in D["citations"]])
    liens = np.array(D["liens"]) if D["liens"] else np.zeros((0, 2), int)
    fils = np.array([fil_points(XY[s], XY[c], angle[s], angle[c]) for s, c in liens]) if len(liens) else np.zeros((0, 9, 2))
    teinte_fils = np.array([couleurs[c] for s, c in liens]) if len(liens) else np.zeros((0, 3))
    annee_fils = np.array([max(annees[s], annees[c]) for s, c in liens]) if len(liens) else np.zeros(0)
    pou = D["poussiere"]
    aire_pou = (0.40 + 2.0 * np.clip(0.5 - np.hypot(pou["x"], pou["y"]), 0, 1)) * F * F if len(pou["x"]) else np.zeros(0)
    teinte_pou = [to_rgb(c) for c in pou["c"]]

    # Le temps n'avance pas à vitesse constante : on fait progresser régulièrement le rayon du
    # bord, si bien que les décennies chargées défilent lentement et les décennies creuses vite.
    grille = np.linspace(echelle.y0, echelle.y1, 3000)
    rayons = np.array([echelle.rayon(y) for y in grille])
    debut = max(1600.0, float(annees.min()))
    part_ancienne = 0.05 if debut < echelle.y0 - 20 else 0.0     # un court prologue pour les précurseurs

    def annee_a(p):
        if p < part_ancienne:
            return debut + (echelle.y0 - debut) * p / part_ancienne
        q = (p - part_ancienne) / (1 - part_ancienne)
        return float(np.interp(echelle.r0 + (1 - echelle.r0) * q, rayons, grille))

    # --- la scène -------------------------------------------------------------------------------
    dpi = 100
    fig = plt.figure(figsize=(args.largeur / dpi, args.hauteur / dpi), dpi=dpi)
    fig.patch.set_facecolor(FOND)
    h = 0.94
    w = h * args.hauteur / args.largeur
    ax = fig.add_axes([0.5 - w / 2, 0.03, w, h])
    ax.set_aspect("equal")
    ax.axis("off")
    L = 1.08
    ax.set_xlim(-L, L)
    ax.set_ylim(-L, L)
    px_par_unite = h * args.hauteur / (2 * L)
    texte_annee = fig.text(0.06, 0.88, "", fontfamily=polices["fine"], fontsize=54 * ech, color=GRIS_DESCRIPTEUR, ha="left", va="center")
    texte_compte = fig.text(0.06, 0.83, "", fontfamily=polices["fine"], fontsize=15 * ech, color="#9AA0A8", ha="left", va="center")
    texte_titre = fig.text(0.06, 0.88, D["titre"], fontfamily=polices["fine"], fontsize=30 * ech, color=ENCRE, ha="left", va="center", alpha=0)
    noms_secteurs, arcs = [], []
    for t in D["ordre"]:
        a0, a1 = D["secteur"][t]
        am = (a0 + a1) / 2
        deg = np.rad2deg(am) % 360
        noms_secteurs.append(ax.text(1.045 * np.cos(am), 1.045 * np.sin(am), " ".join(D["nom_theme"][t].upper()),
                                     rotation=deg - 90 if np.sin(am) >= 0 else deg + 90, rotation_mode="anchor", ha="center",
                                     va="center", fontfamily=polices["grasse"], fontsize=11 * ech, color=D["couleur"][t], alpha=0, zorder=9))
        arc = np.linspace(a1, a0, 60)
        arcs.append(ax.plot(1.02 * np.cos(arc), 1.02 * np.sin(arc), color=D["couleur"][t], lw=1.4 * ech, alpha=0, zorder=9)[0])
    nuage_pou = ax.scatter([0], [0], s=[1], facecolors=[(0, 0, 0, 0)], linewidths=0, zorder=2)
    nuage_fils = LineCollection([], linewidths=0.7, zorder=3)
    ax.add_collection(nuage_fils)
    nuage_points = ax.scatter([0], [0], s=[1], facecolors=[(0, 0, 0, 0)], linewidths=0, zorder=5)
    rang_jalons = [D["ids"].index(g) for g in D["jalons"] if g in D["ids"]]
    nuage_anneaux = ax.scatter([0], [0], s=[1], facecolors="none", edgecolors=ENCRE, linewidths=1.2 * ech, zorder=6.9, alpha=0)
    cercle = np.linspace(0, 2 * np.pi, 400)
    anneaux, annees_anneaux = {}, {}
    for y in echelle.decennies():
        fort = y % 50 == 0
        anneaux[y] = ax.plot([], [], color="#C3C8D0" if fort else "#D7DBE1", lw=(1.3 if fort else 0.9 if y % 20 == 0 else 0.6) * ech, zorder=1)[0]
        annees_anneaux[y] = ax.text(0, 0, str(y), fontfamily=polices["normale"], fontsize=11 * ech, color="#8A8F98", ha="right", va="center",
                                    alpha=0, zorder=9, path_effects=[pe.withStroke(linewidth=3 * ech, foreground=FOND)])

    # Les étiquettes qui s'allumeront au passage : jalons d'abord, puis travaux de plus de 600 citations.
    a_allumer = []
    for k, i in enumerate(D["ids"]):
        nom = en_langue(D["noms"].get(i), langue)
        if not nom:
            continue
        if i in D["jalons"]:
            a_allumer.append((annees[k], k, nom, D["jalons"][i] or None, True))
        elif D["citations"][k] >= 600:
            a_allumer.append((annees[k], k, nom, None, False))
    a_allumer.sort()

    # --- les images ----------------------------------------------------------------------------
    n_croissance = int(args.secondes * args.ips)
    n_pause = int(4 * args.ips)
    ecriture = Ecriture(chemin, args.ips, args.largeur, args.hauteur, args.gif)
    ne_point = np.full(n, 10 ** 9)                 # image à laquelle chaque point apparaît
    ne_fil = np.full(len(liens), 10 ** 9)
    ne_pou = np.full(len(pou["x"]), 10 ** 9)
    allumees, curseur = [], 0
    duree_etiquette, fondu, max_etiquettes = int(2.6 * args.ips), int(0.4 * args.ips), 3

    for f in range(n_croissance + n_pause):
        croissance = f < n_croissance
        y = annee_a(min(1.0, f / max(n_croissance - 1, 1))) if croissance else echelle.y1
        # Le zoom ramène toujours l'année courante au bord ; les points grossissent un peu quand on zoome fort.
        zoom = min(0.97 / max(echelle.rayon(y), 1e-3), 60.0) if croissance else 1.0
        grossi = float(np.clip(zoom ** 0.42, 1.0, 3.2))
        ne_point[(annees <= y) & (ne_point > f)] = f
        ne_fil[(annee_fils <= y) & (ne_fil > f)] = f
        if len(pou["x"]):
            ne_pou[(pou["annee"] <= y) & (ne_pou > f)] = f
        P = XY * zoom

        visibles = np.where(ne_point <= f)[0]
        age = f - ne_point[visibles]
        eclat = 1 + 0.9 * np.clip(1 - age / 12, 0, 1)          # un travail qui naît est d'abord plus gros
        opacite = np.clip((age + 1) / 10, 0, 1) * 0.95
        nuage_points.set_offsets(P[visibles] if len(visibles) else np.zeros((0, 2)))
        nuage_points.set_sizes(aire[visibles] * grossi ** 2 * eclat)
        nuage_points.set_facecolors([to_rgba(couleurs[k], a) for k, a in zip(visibles, opacite)])

        fils_vis = np.where(ne_fil <= f)[0]
        opacite_fils = np.clip((f - ne_fil[fils_vis] + 1) / 15, 0, 1) * (0.14 + 0.30 * (grossi - 1) / 2.2)
        nuage_fils.set_segments(list(fils[fils_vis] * zoom))
        nuage_fils.set_colors([(*teinte_fils[k], a) for k, a in zip(fils_vis, opacite_fils)])
        nuage_fils.set_linewidth(0.7 * ech * grossi ** 0.6)

        if len(pou["x"]):
            pv = np.where(ne_pou <= f)[0]
            nuage_pou.set_offsets(np.c_[pou["x"][pv], pou["y"][pv]] * zoom if len(pv) else np.zeros((0, 2)))
            nuage_pou.set_sizes(aire_pou[pv] * grossi ** 2)
            nuage_pou.set_facecolors([to_rgba(teinte_pou[k], 0.42) for k in pv])

        jalons_vis = [g for g in rang_jalons if ne_point[g] <= f - 10]
        nuage_anneaux.set_offsets(P[jalons_vis] if jalons_vis else np.zeros((0, 2)))
        nuage_anneaux.set_sizes(aire[jalons_vis] * grossi ** 2 * 2.4 + 30 * F * F * grossi if jalons_vis else np.zeros(0))
        nuage_anneaux.set_alpha(0.9 if jalons_vis else 0)

        for an, ligne in anneaux.items():
            r = echelle.rayon(an) * zoom
            if an <= y + 0.5 and r < 1.06:
                ligne.set_data(r * np.cos(cercle), r * np.sin(cercle))
                ligne.set_alpha(1)
                annees_anneaux[an].set_position(((r - 0.006) * np.cos(np.pi / 2 + 0.02), (r - 0.006) * np.sin(np.pi / 2 + 0.02)))
                annees_anneaux[an].set_alpha(1 if an % 20 == 0 and r > 0.12 else 0)
            else:
                ligne.set_alpha(0)
                annees_anneaux[an].set_alpha(0)

        # Étiquettes : on suit le point (qui glisse vers le centre) et on gère le fondu d'entrée et de sortie.
        def ancrage(k):
            x, yy = P[k]
            vers_centre = -np.array([x, yy]) / max(np.hypot(x, yy), 1e-6)
            ecart = 0.02 + 0.5 * np.sqrt(aire[k] * grossi ** 2) / 72 * dpi / px_par_unite
            return np.array([x, yy]) + vers_centre * ecart * 0.6 + np.array([-ecart if x > 0 else ecart, 0]), ("right" if x > 0 else "left")
        for e in allumees[:]:
            textes, debut_e, fin_e, _, k = e
            a = min(adoucir((f - debut_e) / fondu), adoucir((fin_e - f) / fondu))
            q, _ = ancrage(k)
            textes[0].set_position(q)
            if len(textes) > 1:
                textes[1].set_position((q[0], q[1] - 0.004))
            for t in textes:
                t.set_alpha(a)
            if f >= fin_e:
                for t in textes:
                    t.remove()
                allumees.remove(e)
        while curseur < len(a_allumer) and a_allumer[curseur][0] <= y:
            an, k, nom, quoi, est_jalon = a_allumer[curseur]
            if f - ne_point[k] > int(1.2 * args.ips):
                curseur += 1                               # trop tard, le point est déjà loin du bord
                continue
            trop_pres = any(abs((angle[k] - e[3] + np.pi) % (2 * np.pi) - np.pi) < np.deg2rad(22) for e in allumees)
            if len(allumees) >= max_etiquettes or trop_pres:
                break                                      # on attend qu'une place se libère
            curseur += 1
            q, ha = ancrage(k)
            halo = [pe.withStroke(linewidth=4 * ech, foreground=FOND, alpha=0.95)]
            textes = [ax.text(q[0], q[1], f"{nom}  {int(an)}", fontfamily=polices["grasse"], fontsize=(15 if est_jalon else 12.5) * ech,
                              color=ENCRE, ha=ha, va="bottom" if quoi else "center", zorder=10, alpha=0, path_effects=halo)]
            if quoi:
                textes.append(ax.text(q[0], q[1] - 0.004, quoi, fontfamily=polices["fine"], fontsize=11.5 * ech, color=GRIS_DESCRIPTEUR,
                                      ha=ha, va="top", zorder=10, alpha=0, path_effects=halo))
            allumees.append([textes, f, f + duree_etiquette + (int(0.8 * args.ips) if est_jalon else 0), angle[k], k])

        fin = adoucir((f - n_croissance) / (0.5 * n_pause)) if f >= n_croissance else 0
        for t in noms_secteurs:
            t.set_alpha(fin)
        for a in arcs:
            a.set_alpha(0.7 * fin)
        texte_titre.set_alpha(fin)
        texte_annee.set_text(f"{int(y)}" if croissance else "")
        if croissance:
            texte_compte.set_text(f"{len(visibles)} travaux · {len(fils_vis)} citations" if langue == "fr"
                                  else f"{len(visibles)} works · {len(fils_vis)} citations")
        else:
            texte_compte.set_text("")
        ecriture.ajouter(fig)
        if f % 60 == 0:
            print(f"  image {f}/{n_croissance + n_pause} ({int(y)})", flush=True)
    ecriture.fermer()
    print("écrit :", chemin)


if __name__ == "__main__":
    main()
