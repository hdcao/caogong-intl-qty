# Instruction de lecture de tuile (Français)

> Mode d'emploi : fournir ce fichier entier comme invite, avec **une seule** tuile
> référencée dans `manifest.json`. **Une tuile à la fois** — lire plusieurs tuiles
> simultanément provoque des mélanges de repères et rend toute erreur intraçable.
> La sortie doit être du JSON (§4). **Aucune narration en prose.**

---

## 1. Ce que vous avez sous les yeux

- C'est **une tuile découpée dans un plan plus grand**, pas la feuille complète.
- Les ouvrages peuvent être partiellement visibles. **N'extrapolez jamais un total de feuille
  à partir de cette tuile.**
- Les tuiles voisines se recouvrent de 15 %. Un même élément peut apparaître sur deux
  tuiles — **c'est normal, ce n'est pas un doublon.**

## 2. Ne compter que ceci (liste positive)

1. Ouvrages physiques : poutres / poteaux / dalles / voiles / pieux / semelles / radiers /
   chevêtres / conduites / équipements / luminaires …
2. **Chiffres réellement portés sur le plan** : cotes, niveaux, espacements (nombre après
   `@`), repères, désignations, quantités
3. **Chaque ligne** d'une annotation groupée multilingue verticale — ligne par ligne,
   aucune omission
4. Codes matériaux et sections (`HA` / `FE` / `TS` / `Ø` / `C25` / `IPE` / `HEB` …)
5. Repères dans les cercles d'axe (pour localiser uniquement, **jamais** comptés)

## 3. Ne jamais compter ceci (liste négative)

- ❌ Symboles d'une **planche de légende / de symboles** — schématiques, non installés
- ❌ **Les blocs d'étiquette et cadres d'appel eux-mêmes** (`ÉTIQUETTE`, `DÉNOMINATION`) —
  ils désignent un élément, ils ne sont pas l'élément
- ❌ Les vues multiples d'un même élément (plan + coupe) — dans une tuile, **compter une
  seule vue**
- ❌ Cartouche, bloc de révision, notes générales
- ❌ Éléments coupés par le bord de la tuile et d'appartenance incertaine — les mettre dans
  `truncated`, **ne jamais deviner**

## 4. Format de sortie (JSON strict, sans commentaire)

```json
{
  "tile": "<chemin de la tuile dans le manifest>",
  "page": 1,
  "drawing_type": "ferraillage | coffrage | combiné | coupe | élévation | tableau | légende | indéterminé",
  "axes_visible": ["A", "B", "C"],
  "objects": [
    {
      "type": "poutre | poteau | dalle | voile | pieu | semelle | radier | chevêtre | conduite | équipement | luminaire | autre",
      "label": "<repère ou nom exactement comme imprimé, ne pas traduire>",
      "spec": "<section / diamètre / type, exactement comme imprimé>",
      "quantity": 10,
      "quantity_kind": "count | length_mm | area_mm2 | volume_mm3 | spacing_mm",
      "nearest_axes": "B-C / 3-4",
      "center_px": [1240, 860],
      "confidence": "clear | fuzzy | unreadable",
      "evidence_text": "<caractères verbatim du plan, apostrophes et indices compris>"
    }
  ],
  "dimensions": [
    {"text": "3600", "value": 3600, "unit": "mm", "what": "hauteur d'étage", "center_px": [800, 420], "confidence": "clear"}
  ],
  "truncated": ["description des éléments coupés par le bord"],
  "notes": "particularités de la tuile (cartouche, détail, élément répété)"
}
```

**Règles de champ**

- `confidence` : `clear` lisible / `fuzzy` légèrement flou ou en bord de tuile / `unreadable`
- `center_px` = coordonnée pixel **dans l'image de cette tuile** (origine en haut à gauche).
  Obligatoire. « voir plan » n'est pas une valeur acceptable.
- `evidence_text` doit être **verbatim**, apostrophes et indices compris :
  `A'` ≠ `A`, `A₁` ≠ `A1`, `E-a` ≠ `Ea`. **Un repère mal recopié fusionne deux axes et
  divise la quantité par deux.**
- Si illisible : renvoyer `unreadable` en conservant `center_px`. **Ne jamais inventer un
  nombre pour compléter le JSON.**

## 5. Trois consignes non négociables

1. **Aucune estimation.** Si un nombre n'est pas porté, ne le produisez pas. Mieux vaut un
   tableau vide qu'un « environ ».
2. **N'omettez aucun élément.** 30 pieux identiques ⇒ 30, sans échantillonnage ni « etc. ».
3. **Si c'est un détail (`DÉTAIL`, `DÉTAIL TYPE`), ne compter que dans le détail** et
   signaler « facteur de répétition à appliquer ». **Ne jamais** l'additionner à la quantité
   du plan — le nombre de répétitions se compte séparément sur le plan.

## 6. Vocabulaire : garder les mots du dossier

**Ne traduisez pas les termes d'ouvrage.** Utilisez les mots du plan, tels quels :

| Terme du plan | Ne pas écrire |
|---|---|
| `CULÉE` | abutment / 桥台 (garder CULÉE, préciser en note si besoin) |
| `PILE` | pier（墩） — **et non** pieu |
| `PIEU` | pile（英文的 pile = 法语 pieu，此处极易混淆） |
| `CHEVÊTRE` | pier cap |
| `SEMELLE` / `RADIER` | footing（两者不是同一个东西：SEMELLE 独立基础，RADIER 筏板） |
| `FERRAILLAGE` | reinforcement（配筋） |
| `COFFRAGE` | formwork（模板/布置） |
| `PREDALLE` | precast slab |
| `ENTREVOUS` | span（净跨） |

⚠️ `PILE`（墩）与 `PIEU`（桩）**只差一个字母，是完全不同的构件**。写错会让整个基础
工程量错位——这是法语图册最常见的一类致命误读。

## 7. Quand rien n'est lisible

Renvoyer `objects: []` et indiquer la raison dans `notes` (texte trop petit / vectorisé en
courbes / coupé par le bord / planche de légende).
**Un tableau vide est une réponse valide ; un nombre deviné ne l'est pas.**
