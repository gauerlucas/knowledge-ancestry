# -*- coding: utf-8 -*-
"""
Étape 1 : de l'export Zotero au graphe de citations.

    python etape1_recuperer.py ma_bibliotheque.bib --email prenom.nom@exemple.org

Pour chaque article de l'export qui possède un DOI, on demande à Crossref sa liste de
références, puis à OpenAlex le nombre total de citations de chaque travail retenu. Le
script écrit trois fichiers :

    graph.json      le graphe : travaux, liens de citation, « poussière » des travaux cités une ou deux fois
    config.json     les réglages de la figure (thèmes, jalons, titre...) : à ouvrir et à modifier
    candidats.txt   par thème, les travaux les plus influents, pour choisir vos jalons

Toutes les réponses des serveurs sont gardées dans le dossier cache/ : relancer le script
après une coupure reprend là où il s'était arrêté, et le relancer une fois tout récupéré
est instantané.

Confidentialité : l'adresse e-mail est transmise à Crossref dans l'en-tête des requêtes,
comme Crossref le demande, et n'est écrite dans aucun fichier. Il en va de même pour la
clef OpenAlex, qui est facultative.
"""
import argparse
import collections
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from outils import (CONFIG_DEFAUT, PALETTE, ascii_minuscule, clef_auteur, etiquette_auteurs, lire_config,
                    nettoyer_doi, nom_de_famille)

RX_DOI = re.compile(r"^10\.\d{4,9}/\S+$")


# =====================================================================================
# 1. Lecture de l'export Zotero
# =====================================================================================
def lire_export(chemin):
    """Lit un export Zotero et renvoie une liste de {doi, titre, annee, auteurs}.

    Formats acceptés : CSV (le plus simple), BibTeX ou BibLaTeX, RIS, CSL JSON. Zotero range
    parfois le DOI dans le champ « Extra » (chapitres d'ouvrage, prépublications) : on va
    l'y chercher aussi.
    """
    ext = os.path.splitext(chemin)[1].lower()
    if ext == ".csv":
        entrees = lire_csv(chemin)
    elif ext in (".bib", ".bibtex"):
        entrees = lire_bibtex(chemin)
    elif ext == ".ris":
        entrees = lire_ris(chemin)
    elif ext == ".json":
        entrees = lire_csl_json(chemin)
    else:
        sys.exit(f"Format {ext} non reconnu. Exportez depuis Zotero en CSV, BibTeX, RIS ou CSL JSON.")
    propres = []
    for e in entrees:
        doi = nettoyer_doi(e.get("doi"))
        if not RX_DOI.match(doi):
            trouve = re.search(r"\b(10\.\d{4,9}/[^\s\"<>]+)", e.get("extra") or "")
            doi = nettoyer_doi(trouve.group(1)) if trouve else ""
        annee = re.search(r"\d{4}", str(e.get("annee") or ""))
        propres.append(dict(doi=doi.rstrip(".,;"), titre=(e.get("titre") or "").strip(),
                            annee=int(annee.group(0)) if annee else None, auteurs=e.get("auteurs") or []))
    return propres


def lire_csv(chemin):
    entrees = []
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        for ligne in csv.DictReader(f):
            auteurs = [a.split(",")[0].strip() for a in (ligne.get("Author") or "").split(";") if a.strip()]
            entrees.append(dict(doi=ligne.get("DOI"), titre=ligne.get("Title"), annee=ligne.get("Publication Year"),
                                auteurs=auteurs, extra=ligne.get("Extra")))
    return entrees


def lire_bibtex(chemin):
    """Petit lecteur BibTeX qui respecte les accolades imbriquées ({\\'e}, {DNA}...)."""
    texte = open(chemin, encoding="utf-8", errors="replace").read()
    entrees = []
    for debut in re.finditer(r"@(\w+)\s*\{", texte):
        if debut.group(1).lower() in ("comment", "string", "preamble"):
            continue
        # On avance jusqu'à l'accolade fermante qui correspond à l'ouvrante de l'entrée.
        i, profondeur = debut.end(), 1
        while i < len(texte) and profondeur:
            profondeur += {"{": 1, "}": -1}.get(texte[i], 0)
            i += 1
        corps = texte[debut.end():i - 1]
        champs = {}
        for m in re.finditer(r"(\w+)\s*=\s*", corps):
            j = m.end()
            if j >= len(corps):
                break
            if corps[j] == "{":
                k, p = j + 1, 1
                while k < len(corps) and p:
                    p += {"{": 1, "}": -1}.get(corps[k], 0)
                    k += 1
                valeur = corps[j + 1:k - 1]
            elif corps[j] == '"':
                k = corps.find('"', j + 1)
                valeur = corps[j + 1:k]
            else:
                valeur = re.match(r"[^,\n]*", corps[j:]).group(0)
            champs.setdefault(m.group(1).lower(), re.sub(r"[{}]", "", valeur).strip())
        auteurs = []
        for a in re.split(r"\s+and\s+", champs.get("author", "")):
            a = a.strip()
            if a:
                auteurs.append(a.split(",")[0].strip() if "," in a else a.split()[-1])
        entrees.append(dict(doi=champs.get("doi"), titre=champs.get("title"),
                            annee=champs.get("year") or champs.get("date"), auteurs=auteurs,
                            extra=champs.get("note", "") + " " + champs.get("extra", "")))
    return entrees


def lire_ris(chemin):
    entrees, courante = [], None
    for ligne in open(chemin, encoding="utf-8-sig", errors="replace"):
        m = re.match(r"^([A-Z][A-Z0-9])  - ?(.*)$", ligne.rstrip("\n"))
        if not m:
            continue
        balise, valeur = m.group(1), m.group(2).strip()
        if balise == "TY":
            courante = dict(doi="", titre="", annee="", auteurs=[], extra="")
            entrees.append(courante)
        elif courante is None:
            continue
        elif balise == "DO":
            courante["doi"] = valeur
        elif balise in ("TI", "T1") and not courante["titre"]:
            courante["titre"] = valeur
        elif balise in ("PY", "Y1", "DA") and not courante["annee"]:
            courante["annee"] = valeur
        elif balise in ("AU", "A1"):
            courante["auteurs"].append(valeur.split(",")[0].strip())
        elif balise in ("N1", "UR"):
            courante["extra"] += " " + valeur
    return entrees


def lire_csl_json(chemin):
    donnees = json.load(open(chemin, encoding="utf-8"))
    if isinstance(donnees, dict):
        donnees = donnees.get("items", [])
    entrees = []
    for it in donnees:
        parties = (it.get("issued") or {}).get("date-parts") or [[None]]
        auteurs = [(a.get("family") or a.get("literal") or "").strip() for a in it.get("author", [])]
        entrees.append(dict(doi=it.get("DOI"), titre=it.get("title"), annee=parties[0][0] if parties and parties[0] else None,
                            auteurs=auteurs, extra=it.get("note")))
    return entrees


# =====================================================================================
# 2. Interrogation de Crossref et d'OpenAlex, avec cache
# =====================================================================================
class Serveurs:
    """Toutes les requêtes passent par ici, et toutes les réponses sont mises en cache.

    Crossref limite le débit (un peu moins de dix requêtes par seconde avec une adresse
    e-mail, moins sans) ; OpenAlex accepte dix requêtes par seconde sans clef, et la
    recherche d'un travail par son DOI ne compte pas dans son quota journalier. En cas de
    refus temporaire (code 429 ou 5xx), on attend le temps demandé et on recommence.
    """

    def __init__(self, email="", cle_openalex="", hors_ligne=False, dossier_cache="cache"):
        self.email, self.cle, self.hors_ligne, self.cache = email, cle_openalex, hors_ligne, dossier_cache
        self.agent = "rosace-citations/2.0" + (f" (mailto:{email})" if email else "")

    def _fichier(self, nature, doi):
        os.makedirs(os.path.join(self.cache, nature), exist_ok=True)
        return os.path.join(self.cache, nature, hashlib.md5(doi.encode()).hexdigest() + ".json")

    def _demander(self, url, pause):
        for essai in range(6):
            requete = urllib.request.Request(url, headers={"User-Agent": self.agent})
            try:
                with urllib.request.urlopen(requete, timeout=40) as r:
                    donnees = json.loads(r.read().decode("utf-8"))
                time.sleep(pause)
                return donnees
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return {"_absent": True}
                if e.code in (401, 403):
                    print(f"   accès refusé ({e.code}) : {url.split('?')[0]}")
                    return None
                attente = float(e.headers.get("Retry-After") or 3 + 5 * essai)
                print(f"   le serveur demande une pause ({e.code}) : {attente:.0f} s", flush=True)
                time.sleep(attente)
            except Exception as e:                  # coupure réseau, délai dépassé...
                print(f"   réseau : {e.__class__.__name__}, nouvel essai", flush=True)
                time.sleep(2 + 3 * essai)
        return None                                  # rien en cache : on réessaiera au prochain lancement

    def _avec_cache(self, nature, doi, url, pause):
        chemin = self._fichier(nature, doi)
        if os.path.exists(chemin):
            return json.load(open(chemin, encoding="utf-8"))
        if self.hors_ligne:
            return {}
        reponse = self._demander(url, pause)
        if reponse is None:
            return {}
        json.dump(reponse, open(chemin, "w", encoding="utf-8"))
        return reponse

    def crossref(self, doi):
        """Notice Crossref d'un DOI (titre, auteurs, année, liste de références)."""
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/()._-;:")
        if self.email:
            url += "?mailto=" + urllib.parse.quote(self.email)
        reponse = self._avec_cache("crossref", doi, url, pause=0.12 if self.email else 0.3)
        return reponse.get("message", reponse) if isinstance(reponse, dict) else {}

    def openalex(self, doi):
        """Nombre de citations, année, auteurs, titre et type d'un DOI selon OpenAlex.

        Le « doi: » de l'adresse ne doit pas être encodé : c'est ce qui fait reconnaître la
        requête comme la recherche d'un seul travail, gratuite, plutôt que comme une recherche filtrée.
        """
        url = ("https://api.openalex.org/works/doi:" + urllib.parse.quote(doi, safe="/()._-;:")
               + "?select=cited_by_count,publication_year,authorships,title,type")
        if self.cle:
            url += "&api_key=" + urllib.parse.quote(self.cle)
        reponse = self._avec_cache("openalex", doi, url, pause=0.11)
        return reponse if isinstance(reponse, dict) else {}


# =====================================================================================
# 3. Lecture d'une liste de références Crossref
# =====================================================================================
ANNEE_MAX = time.localtime().tm_year + 1


def annee_plausible(y):
    """Écarte les années impossibles (coquilles des listes de références : « 2104 », « 0 »...)."""
    try:
        y = int(y)
    except (TypeError, ValueError):
        return None
    return y if 1600 <= y <= ANNEE_MAX else None


def annee_reference(ref):
    """Année d'une référence : champ year, sinon une année lue dans le texte brut."""
    y = str(ref.get("year") or "").strip()[:4]
    if y.isdigit():
        return annee_plausible(y)
    m = re.search(r"\b(1[6-9]\d\d|20\d\d)\b", ref.get("unstructured") or "")
    return annee_plausible(m.group(1)) if m else None


def auteur_reference(ref):
    """Premier auteur d'une référence, débarrassé des initiales ; '' si illisible."""
    a = ref.get("author") or ""
    if not a and ref.get("unstructured"):
        a = re.split(r"[,;]| and | et ", ref["unstructured"])[0]
    a = nom_de_famille(a)
    if re.search(r"(?i)unspecified|anonym|^et al|^\d|^\W", a) or len(a) > 40:
        return ""
    return a


def lire_reference(ref):
    """Transforme une référence Crossref en {clef, doi, annee, auteur, titre, signature}.

    La clef d'un travail est son DOI s'il en a un, sinon « u:nomdefamille:année ». La
    signature (auteur, année, volume, page) sert ensuite à reconnaître qu'une référence sans
    DOI désigne le même travail qu'une autre référence, qui elle en a un.
    """
    doi = nettoyer_doi(ref.get("DOI"))
    annee, auteur = annee_reference(ref), auteur_reference(ref)
    volume = re.sub(r"\D", "", str(ref.get("volume") or ""))
    page = re.sub(r"\W", "", str(ref.get("first-page") or "").lower())
    signature = (ascii_minuscule(auteur), annee, volume, page) if auteur and annee and (volume or page) else None
    if RX_DOI.match(doi):
        clef = doi
    elif auteur:
        doi = None
        clef = f"u:{ascii_minuscule(auteur)}:{annee or ''}"
        if volume or page:
            clef += f":{volume}-{page}"
    else:
        # Ni DOI ni auteur lisible : on ne saura pas la rapprocher d'une autre, elle reste unique.
        doi = None
        brut = ref.get("unstructured") or ref.get("key") or json.dumps(ref, sort_keys=True)
        clef = "x:" + hashlib.md5(brut.encode()).hexdigest()[:12]
    titre = (ref.get("article-title") or ref.get("volume-title") or ref.get("unstructured") or "")[:200]
    return dict(clef=clef, doi=doi, annee=annee, auteur=auteur, titre=titre, signature=signature)


def references_de(notice):
    return [lire_reference(r) for r in (notice.get("reference") or [])]


# =====================================================================================
# 4. Noms automatiques des thèmes
# =====================================================================================
# Mots trop fréquents pour caractériser un thème, en anglais et en français. Si vos thèmes
# s'appellent « study · effect · model », c'est qu'il en manque : ajoutez-les.
MOTS_VIDES = set("""
the of and in a an for to with on by from at as is are be or that this these its into using use used based new
via its their than between during after before under over within without toward towards through about against
study studies analysis approach method methods data review case cases effect effects role model models
evidence results result findings report clinical patients patient human humans first two three novel
de la le les des du et en un une pour sur par au aux dans avec chez entre selon sans sous vers leur leurs
étude etude analyse cas effet effets rôle role modèle modele nouveau nouvelle résultats resultats
""".split())


def nom_automatique(titres):
    mots = collections.Counter()
    for t in titres:
        for m in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\-]{3,}", t or ""):
            if m.lower() not in MOTS_VIDES:
                mots[m.lower()] += 1
    return " · ".join(m for m, _ in mots.most_common(3)) or "thème"


# =====================================================================================
# 5. Programme principal
# =====================================================================================
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="export Zotero (.csv, .bib, .ris ou .json CSL)")
    ap.add_argument("--email", default=os.environ.get("ROSACE_EMAIL", ""),
                    help="votre adresse, transmise à Crossref pour un accès plus rapide (jamais écrite dans un fichier)")
    ap.add_argument("--cle-openalex", default=os.environ.get("OPENALEX_API_KEY", ""), help="clef OpenAlex gratuite, facultative")
    ap.add_argument("--min-citations", type=int, default=3,
                    help="un travail cité au moins N fois par votre bibliothèque devient un point de la figure (3 par défaut)")
    ap.add_argument("--themes-max", type=int, default=7, help="nombre maximal de thèmes (7 par défaut)")
    ap.add_argument("--resolution", type=float, default=1.0, help="finesse de la détection des thèmes : plus grand = plus de thèmes")
    ap.add_argument("--hubs", type=int, default=5, help="par thème, nombre de travaux influents dont on lit aussi les références")
    ap.add_argument("--hors-ligne", action="store_true", help="n'utiliser que le cache, sans aucune requête")
    ap.add_argument("--langue", choices=["fr", "en"], default="fr",
                    help="langue de la figure inscrite dans le config.json de départ (fr par défaut, en pour l'anglais)")
    args = ap.parse_args()

    serveurs = Serveurs(args.email, args.cle_openalex, args.hors_ligne)
    if not args.email and not args.hors_ligne:
        print("Conseil : avec --email votre.adresse@exemple.org, Crossref répond plus vite et coupe moins souvent.\n")
    cfg_existante = lire_config("config.json") if os.path.exists("config.json") else None
    mes_articles = set(cfg_existante["mes_articles"]) if cfg_existante else set()

    # --- 1. bibliothèque ----------------------------------------------------------------------
    entrees = lire_export(args.export)
    biblio = {}
    for e in entrees:
        if e["doi"] and e["doi"] not in mes_articles:
            biblio.setdefault(e["doi"], e)
    print(f"{len(entrees)} entrées lues, dont {len(biblio)} avec un DOI. "
          f"Les {len(entrees) - len(biblio)} autres (livres, sites, entrées sans DOI) ne peuvent pas être reliées.")

    # --- 2. listes de références ---------------------------------------------------------------
    notices = {}
    for k, doi in enumerate(list(biblio) + sorted(mes_articles)):
        notices[doi] = serveurs.crossref(doi)
        if k % 25 == 0:
            print(f"  Crossref : {k}/{len(biblio) + len(mes_articles)}", flush=True)
    refs = {doi: references_de(notices[doi]) for doi in notices}
    sans_refs = sum(1 for doi in biblio if not refs[doi])
    if sans_refs:
        print(f"  {sans_refs} articles sans liste de références chez Crossref (certains éditeurs ne la déposent pas).")

    # Une même référence est parfois citée avec son DOI par un article et sans DOI par un autre :
    # on rattache la seconde à la première quand auteur, année, volume et page concordent.
    signature_vers_doi = {}
    for liste in refs.values():
        for r in liste:
            if r["doi"] and r["signature"]:
                signature_vers_doi.setdefault(r["signature"], r["doi"])
    for liste in refs.values():
        for r in liste:
            if not r["doi"] and r["signature"] in signature_vers_doi:
                r["clef"] = signature_vers_doi[r["signature"]]

    # --- 3. qui cite qui ------------------------------------------------------------------------
    citants = collections.defaultdict(set)          # travail cité -> articles de la bibliothèque qui le citent
    annees = collections.defaultdict(list)
    auteurs = collections.defaultdict(list)
    titres = {}
    for doi in biblio:
        for r in refs[doi]:
            if r["clef"] == doi:
                continue
            citants[r["clef"]].add(doi)
            if r["annee"]:
                annees[r["clef"]].append(r["annee"])
            if r["auteur"]:
                auteurs[r["clef"]].append(r["auteur"])
            if len(r["titre"]) > len(titres.get(r["clef"], "")):
                titres[r["clef"]] = r["titre"]

    def annee_majoritaire(clef):
        return collections.Counter(annees[clef]).most_common(1)[0][0] if annees[clef] else None

    def auteur_majoritaire(clef):
        return collections.Counter(auteurs[clef]).most_common(1)[0][0] if auteurs[clef] else None

    coeur = {c for c, s in citants.items() if len(s) >= args.min_citations and c not in biblio and c not in mes_articles}
    poussiere = {c: dict(citants=sorted(s), annee=annee_majoritaire(c))
                 for c, s in citants.items() if len(s) < args.min_citations and c not in biblio and c not in mes_articles}
    print(f"{len(coeur)} travaux cités au moins {args.min_citations} fois, {len(poussiere)} cités une ou deux fois (poussière).")

    # --- 4. les points de la figure --------------------------------------------------------------
    noeuds = {}
    for doi, e in biblio.items():
        n = notices[doi]
        parties = (n.get("issued") or {}).get("date-parts") or [[None]]
        familles = [a.get("family") or a.get("name") or "" for a in n.get("author", [])] or e["auteurs"]
        noeuds[doi] = dict(id=doi, biblio=True, annee=annee_plausible(e["annee"] or (parties[0][0] if parties[0] else None)),
                           titre=(n.get("title") or [e["titre"]])[0] if n.get("title") else e["titre"],
                           nom=etiquette_auteurs(familles), citations=n.get("is-referenced-by-count"),
                           source_citations="crossref", degre=len(citants.get(doi, ())))
    for c in coeur:
        noeuds[c] = dict(id=c, biblio=False, annee=annee_majoritaire(c), titre=titres.get(c, ""), nom=auteur_majoritaire(c),
                         citations=None, source_citations=None, degre=len(citants[c]))
    liens = {(s, c) for c, ss in citants.items() if c in noeuds for s in ss if s in noeuds}

    # --- 5. thèmes : communautés du graphe (méthode de Louvain) ----------------------------------
    import networkx as nx
    g = nx.Graph()
    g.add_nodes_from(noeuds)
    g.add_edges_from(liens)
    communautes = sorted(nx.algorithms.community.louvain_communities(g, resolution=args.resolution, seed=3), key=len, reverse=True)
    grandes = communautes[:args.themes_max]
    # Si l'étape 1 a déjà tourné et que config.json a été retouché, les thèmes doivent garder
    # leur numéro : sinon le nom « Théorie des graphes » se retrouverait sur un autre secteur.
    # On rattache donc chaque nouvelle communauté à l'ancien thème avec lequel elle a le plus
    # de travaux en commun.
    numero = list(range(len(grandes)))
    if cfg_existante is not None and os.path.exists("graph.json"):
        ancien = {d["id"]: d.get("theme") for d in json.load(open("graph.json", encoding="utf-8"))["noeuds"]}
        recouvrement = collections.Counter((k, ancien[i]) for k, com in enumerate(grandes) for i in com if ancien.get(i) is not None)
        numero, pris = [None] * len(grandes), set()
        for (k, t), _ in recouvrement.most_common():
            if numero[k] is None and t not in pris:
                numero[k] = t
                pris.add(t)
        suivant = max(pris, default=-1) + 1
        for k in range(len(grandes)):
            if numero[k] is None:
                numero[k] = suivant
                suivant += 1
    grandes = [(numero[k], com) for k, com in enumerate(grandes)]
    theme = {i: k for k, com in grandes for i in com}
    for com in communautes[args.themes_max:]:       # les petites communautés rejoignent leur voisin le plus lié
        for i in com:
            votes = collections.Counter(theme.get(v) for v in g.neighbors(i) if v in theme)
            theme[i] = votes.most_common(1)[0][0] if votes else grandes[0][0]
    for i, d in noeuds.items():
        d["theme"] = theme.get(i, 0)

    # --- 6. ancêtres communs -----------------------------------------------------------------------
    # Les travaux fondateurs sont souvent cités par les grands articles du domaine plutôt que
    # par les vôtres. On lit donc aussi les références des travaux les plus cités de chaque
    # thème, et l'on garde celles qu'au moins deux thèmes ont en commun, ou qui sont anciennes.
    pivots = {}
    for k, _ in grandes:
        candidats = sorted((d for d in noeuds.values() if d["theme"] == k and d["id"].startswith("10.")), key=lambda d: -d["degre"])
        pivots[k] = [d["id"] for d in candidats[:args.hubs]]
    themes_anc = collections.defaultdict(set)
    pivots_anc = collections.defaultdict(set)
    infos_anc = {}
    for k, ids in pivots.items():
        for p in ids:
            notice = notices.get(p) or serveurs.crossref(p)
            for r in references_de(notice):
                if not r["doi"] and r["signature"] in signature_vers_doi:
                    r["clef"] = signature_vers_doi[r["signature"]]
                if r["clef"] == p:
                    continue
                themes_anc[r["clef"]].add(k)
                pivots_anc[r["clef"]].add(p)
                infos_anc.setdefault(r["clef"], r)
    toutes = [d["annee"] for d in noeuds.values() if d["annee"]]
    seuil_ancien = int(min(toutes) + 0.35 * (max(toutes) - min(toutes))) if toutes else 1980
    ajoutes = 0
    for c, ths in themes_anc.items():
        r = infos_anc[c]
        if c in noeuds or c in mes_articles or not r["annee"]:
            for p in pivots_anc[c]:
                if c in noeuds and p in noeuds and p != c:
                    liens.add((p, c))
            continue
        if len(ths) >= 2 or r["annee"] <= seuil_ancien:
            noeuds[c] = dict(id=c, biblio=False, annee=r["annee"], titre=r["titre"], nom=r["auteur"] or None, citations=None,
                             source_citations=None, degre=len(pivots_anc[c]), theme=collections.Counter(ths).most_common(1)[0][0],
                             ancetre=True)
            poussiere.pop(c, None)
            for p in pivots_anc[c]:
                liens.add((p, c))
            ajoutes += 1
    print(f"{ajoutes} ancêtres communs ajoutés à partir des références de {sum(len(v) for v in pivots.values())} travaux pivots.")

    # --- 7. nombre total de citations, noms et années (OpenAlex) -----------------------------------
    a_demander = [d for d in noeuds.values() if d["id"].startswith("10.")]
    print(f"OpenAlex : {len(a_demander)} travaux à compléter.")
    for k, d in enumerate(a_demander):
        oa = serveurs.openalex(d["id"])
        if oa.get("cited_by_count") is not None:
            d["citations"], d["source_citations"] = oa["cited_by_count"], "openalex"
        if not d["biblio"]:
            if annee_plausible(oa.get("publication_year")):
                d["annee"] = oa["publication_year"]
            familles = [nom_de_famille(a.get("raw_author_name") or (a.get("author") or {}).get("display_name") or "")
                        for a in oa.get("authorships") or []]
            if familles and familles[0]:
                d["nom"] = etiquette_auteurs(familles)
            if oa.get("title"):
                d["titre"] = oa["title"]
        if oa.get("type") == "review":
            d["revue"] = True                         # revue de synthèse : jamais étiquetée
        if k % 100 == 0:
            print(f"  OpenAlex : {k}/{len(a_demander)}", flush=True)
    for d in noeuds.values():
        if d["source_citations"] == "crossref" and d["citations"] is not None:
            d["citations"] = int(d["citations"] * 1.25)   # Crossref compte environ 20 % de citations de moins qu'OpenAlex

    # --- 8. vos propres articles (version « vous êtes ici ») ---------------------------------------
    moi = {}
    for doi in mes_articles:
        n = notices.get(doi) or {}
        parties = (n.get("issued") or {}).get("date-parts") or [[None]]
        cles = set()
        for r in references_de(n):
            if not r["doi"] and r["signature"] in signature_vers_doi:
                r["clef"] = signature_vers_doi[r["signature"]]
            cles.add(r["clef"])
        moi[doi] = dict(annee=annee_plausible(parties[0][0] if parties[0] else None), cites=sorted(cles))
        if not cles:
            print(f"  attention : aucune référence trouvée chez Crossref pour {doi}")

    # --- 9. écriture -----------------------------------------------------------------------------
    annees_ok = [d["annee"] for d in noeuds.values() if d["annee"]]
    graphe = dict(noeuds=list(noeuds.values()), liens=sorted(liens), poussiere=poussiere, moi=moi,
                  meta=dict(source=os.path.basename(args.export), n_biblio=len(biblio), n_coeur=len(coeur),
                            n_poussiere=len(poussiere), annee_min=min(annees_ok), annee_max=max(annees_ok)))
    json.dump(graphe, open("graph.json", "w", encoding="utf-8"), ensure_ascii=False)

    themes = []
    for k, com in sorted(grandes):
        themes.append(dict(id=k, nom=nom_automatique(noeuds[i]["titre"] for i in com if i in noeuds),
                           couleur=PALETTE[k % len(PALETTE)], taille=len(com)))
    print("\nThèmes détectés (à renommer dans config.json) :")
    for t in themes:
        print(f"   {t['id']} : {t['nom']}  ({t['taille']} travaux)")

    def importance(d):
        return (d["citations"] or 10 * d["degre"]) ** 0.5 * (1 + d["degre"]) ** 0.5

    # Trois jalons provisoires par thème, pour que la première figure ne soit pas muette.
    # On écarte d'office les revues de synthèse et les grands classiques de la statistique.
    outils = {ascii_minuscule(n) for n in CONFIG_DEFAUT["outils"]["noms"]}

    def proposable(d):
        return not d.get("revue") and d.get("nom") and d.get("annee") and clef_auteur(d["nom"]) not in outils

    jalons = []
    for t in themes:
        meilleurs = sorted((d for d in noeuds.values() if d["theme"] == t["id"] and proposable(d)),
                           key=lambda d: -importance(d))[:3]
        jalons += [dict(id=d["id"], nom=d["nom"], quoi="") for d in meilleurs]

    if cfg_existante is None:
        if args.langue == "en":
            titre, sous_titre = "Genealogy of a library", ["The works it rests upon", "and those they descend from"]
        else:
            titre, sous_titre = "Généalogie d’une bibliothèque", ["Les travaux sur lesquels elle repose", "et ceux dont ils procèdent"]
        cfg = {"langue": args.langue, "titre": titre, "sous_titre": sous_titre,
               "signature": "", "annee_centre": None,
               "themes": [dict(id=t["id"], nom=t["nom"], couleur=t["couleur"]) for t in themes],
               "fusionner": {}, "jalons": jalons, "toujours_nommer": [], "jamais_nommer": [],
               "outils": {"ids": [], "noms": ["Benjamini", "Hochberg", "Bonferroni", "Holm"]},
               "mes_articles": [], "renommer": {}}
        json.dump(cfg, open("config.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\nconfig.json créé : renommez les thèmes, complétez les jalons, puis lancez etape2_dessiner.py.")
    else:
        # On ne touche jamais à une config déjà retouchée à la main : la nouvelle détection va à côté.
        json.dump(dict(themes=[dict(id=t["id"], nom=t["nom"], couleur=t["couleur"]) for t in themes], jalons=jalons),
                  open("config_proposee.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\nconfig.json existe déjà et n'a pas été modifié. La nouvelle détection des thèmes est dans config_proposee.json.")

    with open("candidats.txt", "w", encoding="utf-8") as f:
        f.write("Les travaux les plus influents de chaque thème, pour choisir vos jalons.\n"
                "Copiez l'identifiant (DOI ou clef u:...) dans la liste \"jalons\" de config.json.\n"
                "[revue] = revue de synthèse selon OpenAlex : mieux vaut lui préférer l'article original.\n")
        for t in themes:
            f.write(f"\n=== Thème {t['id']} : {t['nom']} ===\n")
            for d in sorted((d for d in noeuds.values() if d["theme"] == t["id"]), key=lambda d: -importance(d))[:20]:
                cit = d["citations"] if d["citations"] is not None else "?"
                f.write(f"{d['id']:<45} {(d.get('nom') or '?')[:28]:<28} {d.get('annee') or '?'}  {cit:>7} cit.  "
                        f"{'[revue] ' if d.get('revue') else ''}{(d.get('titre') or '')[:90]}\n")
    print(f"graph.json : {len(noeuds)} travaux, {len(liens)} liens, {len(poussiere)} grains de poussière. candidats.txt écrit.")


if __name__ == "__main__":
    main()
