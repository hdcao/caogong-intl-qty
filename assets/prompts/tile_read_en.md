# Tile Reading Instruction (English)

> How to use: paste this whole file as the prompt, together with **one** tile image
> referenced in `manifest.json`. One tile at a time — reading several at once causes
> row/label cross-contamination and makes errors impossible to locate.
> Output must be JSON (§4). **No prose narration.**

---

## 1. What you are looking at

- This is **one tile cut out of a larger engineering drawing**, not the full sheet.
- Structures may be partially visible. **Do not extrapolate sheet totals from this tile.**
- Adjacent tiles overlap by 15%. The same element may appear in two tiles — **that is
  expected, not a duplicate element.**

## 2. Count only these (positive list)

1. Physical elements: beams / columns / slabs / walls / piles / footings / pier caps /
   pipes / equipment / luminaires …
2. **Explicit numbers printed on the sheet**: dimension text, levels, spacing (number
   after `@`), marks, type designations, quantities
3. Every line of a multi-line stacked annotation — read line by line, skip none
4. Material and section codes (`HA` / `FE` / `Ø` / `C25` / `IPE` / `HEB` …)
5. Axis circle labels (for location only, never counted)

## 3. Never count these (negative list)

- ❌ Symbols on a **legend / symbol-key sheet** — they are schematic, not installed items
- ❌ **Tag blocks and callout boxes themselves** (e.g. `DOOR TAG`) — they label elements,
  they are not elements
- ❌ Multiple views of the same element (plan + section) — within one tile count **one view only**
- ❌ Title block, revision block, general notes text
- ❌ Elements truncated by the tile boundary whose ownership is unclear — put them in
  `truncated`, never guess

## 4. Output format (strict JSON, no comments)

```json
{
  "tile": "<tile path from manifest>",
  "page": 1,
  "drawing_type": "reinforcement | setting-out | combined | section | elevation | schedule | legend | undetermined",
  "axes_visible": ["A", "B", "C"],
  "objects": [
    {
      "type": "beam | column | slab | wall | pile | footing | pier_cap | pipe | equipment | luminaire | other",
      "label": "<mark or name exactly as printed, do not translate>",
      "spec": "<section / diameter / type, exactly as printed>",
      "quantity": 10,
      "quantity_kind": "count | length_mm | area_mm2 | volume_mm3 | spacing_mm",
      "nearest_axes": "B-C / 3-4",
      "center_px": [1240, 860],
      "confidence": "clear | fuzzy | unreadable",
      "evidence_text": "<verbatim characters from the sheet, including primes and subscripts>"
    }
  ],
  "dimensions": [
    {"text": "3600", "value": 3600, "unit": "mm", "what": "storey height", "center_px": [800, 420], "confidence": "clear"}
  ],
  "truncated": ["description of elements cut by the tile boundary"],
  "notes": "anything specific to this tile (title block, detail view, repeated element)"
}
```

**Field rules**

- `confidence`: `clear` legible / `fuzzy` slightly blurred or at tile edge / `unreadable`
- `center_px` is the pixel coordinate **inside this tile image** (origin top-left). Required.
  "see drawing" is not an acceptable value.
- `evidence_text` must be verbatim, **including primes and subscripts** (`A'` is not `A`,
  `A₁` is not `A1`)
- If you cannot read it, return `unreadable` with `center_px` intact. **Do not invent a
  number to complete the JSON.**

## 5. Three non-negotiable statements

1. **No estimation.** If a number is not printed, do not produce it. Return an empty
   array rather than "approximately".
2. **Do not miss any.** If 30 identical piles appear in the tile, report 30 — no sampling,
   no "and so on".
3. **If this is a detail view (DETAIL / DÉTAIL), count only within the detail** and flag
   "needs repetition factor". **Never** add it to the plan-view quantity — the repetition
   factor must be counted separately on the plan.

## 6. When nothing is readable

Return `objects: []` and state the reason in `notes` (text too small / converted to curves /
cut by boundary / legend sheet). **An empty array is a valid answer; a guessed number is not.**
