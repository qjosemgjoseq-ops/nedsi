# DOT-NL Datakwaliteit — Checklijst & Methode

**Doel:** een levende, herbruikbare lijst van datakwaliteitscontroles ("checks")
op de laadpaal-*locatiedata* die CPO's via DOT-NL aanleveren. Dit is de
ontbrekende laag: LINDA toetst de *sessiedata* (10 `t_*`-checks), maar de
*locatie/beschikbaarheidsdata* in DOT-NL heeft nog geen gepubliceerd
kwaliteitskader. Deze lijst vult dat gat en vormt de basis voor de per-CPO
datakwaliteit-scorecard.

Peildatum profiling: 2026-07-13 · Snapshot: 66.370 laadstations in
`raw_ndw_dotnl` (NEDSI-database).

---

## 1. Hoe stel je een goede checklijst samen? (methode)

Niet checks uit het hoofd verzinnen — dan vind je alleen de problemen die je al
kent. Een goede catalogus ontstaat uit **twee methodes tegelijk**:

### A. Deductief (top-down) — systematisch afleiden
Neem *elk veld* dat DOT-NL levert en loop het langs de zes standaard
datakwaliteitsdimensies. Elke cel is een kandidaat-check:

| Dimensie | Kernvraag | Voorbeeld in DOT-NL |
|---|---|---|
| **Volledigheid** | Is het veld aanwezig/gevuld? | `owner_name` ontbreekt bij 45% |
| **Validiteit** | Voldoet de waarde aan type/enum/bereik/standaard? | `power_max > 400 kW` bij 9 stations |
| **Nauwkeurigheid** | Klopt de waarde met de werkelijkheid (externe bron)? | coördinaat vs. PDOK/OSM-kruiscontrole |
| **Consistentie** | Spreken samenhangende velden elkaar tegen? | `available > total` (onmogelijk) |
| **Uniciteit** | Duplicaten die er niet zouden mogen zijn? | 667 coördinaten gedeeld door >1 station |
| **Tijdigheid** | Is de data vers? | 4,1% niet bijgewerkt in >7 dagen |

Leg daar drie **externe waarheidsbronnen** overheen — dáár komen de
niet-voor-de-hand-liggende checks vandaan:
- **OCPI 2.2.1-schema** → validatieregels (enums, verplichte velden, formaten).
- **AFIR-verordening** → welke velden *wettelijk verplicht* zijn (locatie,
  tarief, beschikbaarheid). Een ontbrekend AFIR-veld is een *compliance*-issue,
  geen schoonheidsfoutje.
- **LINDA's bestaande `t_*`-checks** → zelfde naamgeving en patronen hergebruiken,
  zodat de lijst leest als een zusje van wat NDW al draait.

### B. Inductief (bottom-up) — de data laten praten
Profileer de échte dataset (verdelingen, null-percentages, cardinaliteit,
tegenstrijdigheden tussen velden). Alles wat verrast, wordt een nieuwe check.
Dít is het antwoord op "problemen die ik nog niet heb gevonden". Script:
[`scripts/profile_dotnl_quality.py`](scripts/profile_dotnl_quality.py).

### C. Levend houden — drie feedbackloops
1. **Profiler opnieuw draaien** op elke nieuwe snapshot → nieuwe anomalie = nieuwe check.
2. **Kruisbron-verschillen**: elke keer dat NEDSI's fusie-engine NDW ziet
   afwijken van OCM/OSM, is dat afwijkingspatroon een kandidaat-nauwkeurigheidscheck.
3. **Snapshot-over-tijd**: "verdwijnende stations" en "flapperende status" zie je
   alleen door snapshots te vergelijken — die tijdreekschecks komen erbij naarmate
   de historie groeit.

---

## 2. Formaat van een check

Elke check krijgt dezelfde vorm — dát maakt van "een lijst" een gereedschap.
Naamgeving in LINDA-stijl (`t_*`):

```
t_missing_tariff   | dimensie: volledigheid | ernst: hoog (AFIR)
  definitie:  station heeft op geen enkele connector tariff_ids
  detectie:   SQL over availabilities[].tariff_ids
  omvang:     28.162 stations (42,4%)
  per-CPO:    oprollen per operator → wie is verantwoordelijk
```

Velden: `id` · `dimensie` · `definitie (mensentaal)` · `ernst` (met AFIR/wettelijk-vlag) ·
`detectielogica` · `huidige omvang` · `per-CPO-oprol`. De **per-CPO-oprol** maakt
er een scorecard van in plaats van een rapport: elke check wordt "welke operators
falen hierop, en hoe erg".

**Ernst-schaal (voorstel, te bevestigen met DOT-team):**
- **Kritiek** — maakt het station onbruikbaar of onvindbaar (fout coördinaat,
  station verdwenen).
- **Hoog** — schendt een AFIR-verplichting (geen tarief, geen beschikbaarheid).
- **Middel** — bruikbaar maar onbetrouwbaar/onvolledig (verouderd, geen owner).
- **Laag** — cosmetisch / ter verificatie (gedeelde coördinaat, semantiek onduidelijk).

---

## 3. Startcatalogus (geseed met echte profiling-cijfers)

Status: ✅ bevestigd door profiling · 🔎 vereist snapshot-historie of externe bron ·
❓ semantiek uitzoeken met NDW.

### Volledigheid
| ID | Definitie | Ernst | Omvang (2026-07-13) | Status |
|---|---|---|---|---|
| `t_missing_tariff` | Geen `tariff_ids` op enige connector (AFIR: prijstransparantie verplicht) | Hoog | 28.162 (42,4%) | ✅ |
| `t_missing_owner` | `owner_name` ontbreekt | Middel | 30.032 (45,2%) | ✅ |
| `t_missing_operator` | `operator_name` ontbreekt | Middel | 1.096 (1,7%) | ✅ |
| `t_missing_address` | `address` leeg | Middel | 0 (0,0%) | ✅ |
| `t_missing_availability` | Geen `availabilities`-array | Hoog | 0 (0,0%) | ✅ |

### Validiteit
| ID | Definitie | Ernst | Omvang | Status |
|---|---|---|---|---|
| `t_coord_zero` | Coördinaat is (0,0) | Kritiek | 0 | ✅ |
| `t_coord_outside_nl` | Coördinaat buiten NL-bounding-box | Kritiek | 0 | ✅ |
| `t_power_zero` | `power_max` = 0/null op een connector | Middel | 8 | ✅ |
| `t_power_implausible` | `power_max` > 400 kW (onrealistisch) | Middel | 9 | ✅ |
| `t_country_not_nld` | `country` ≠ NLD terwijl aangeleverd onder NL-prefix | Hoog | 0* | ✅ |

\* De eerdere 22 buiten-NL-adressen (zie `NDW_DATA_QUALITY_REPORT.md`) hadden
`country: NLD` maar een buitenlands adres → wél te vangen met de
nauwkeurigheidscheck `t_coord_far_from_road`, niet met `t_country_not_nld`.

### Consistentie
| ID | Definitie | Ernst | Omvang | Status |
|---|---|---|---|---|
| `t_available_gt_total` | `available` > `total` (onmogelijk) | Kritiek | 0 | ✅ |
| `t_total_zero` | `total` connectoren = 0 | Middel | 0 | ✅ |
| `t_open_flag_semantics` | `open=false` terwijl er beschikbare connectoren zijn | ❓ uitzoeken | 57.007 (85,9%) | ❓ |
| `t_cpo_id_mismatch` | id-prefix-CPO ≠ `cpo_id`-veld | Laag | 0 | ✅ |
| `t_brand_vs_party_mismatch` | Merk levert onder platform-party-id ≠ eigen register-id | Middel | zie DQ-rapport | ✅ |

> `t_open_flag_semantics`: 86% heeft `open=false` mét beschikbare connectoren.
> Óf een massale tegenstrijdigheid, óf het `open`-veld betekent iets anders dan
> "operationeel" (bijv. "24/7 open" of default-false bij ontbrekende
> openingstijden). **Eerst betekenis navragen bij NDW** voordat dit als fout telt.

### Uniciteit
| ID | Definitie | Ernst | Omvang | Status |
|---|---|---|---|---|
| `t_duplicate_id` | Zelfde station-id > 1× | Kritiek | 0 | ✅ |
| `t_shared_coordinate` | Coördinaat gedeeld door > 1 station (co-locatie of luie geocodering?) | Laag | 667 clusters | ✅ |

### Tijdigheid
| ID | Definitie | Ernst | Omvang | Status |
|---|---|---|---|---|
| `t_stale_7d` | `last_updated` > 7 dagen oud | Middel | 2.713 (4,1%) | ✅ |
| `t_stale_30d` | `last_updated` > 30 dagen oud | Hoog | 1.080 (1,6%) | ✅ |
| `t_stale_90d` | `last_updated` > 90 dagen oud (waarschijnlijk dood) | Hoog | 448 (0,7%) | ✅ |

### Nauwkeurigheid (externe bron / historie vereist)
| ID | Definitie | Ernst | Status |
|---|---|---|---|
| `t_coord_far_from_road` | Coördinaat ver van dichtstbijzijnde NL-weg (NWB) | Kritiek | 🔎 (22 gevonden, zie DQ-rapport) |
| `t_coord_disagrees_source` | Coördinaat wijkt >X m af van OCM/OSM voor zelfde station | Hoog | 🔎 (via fusie-engine) |
| `t_disappeared` | Station in vorige snapshot, weg in huidige | Kritiek | 🔎 (snapshot-historie nodig) |
| `t_status_flapping` | Beschikbaarheid wisselt onrealistisch vaak | Middel | 🔎 (tijdreeks nodig) |
| `t_never_available` | Station rapporteert nooit een vrije connector | Middel | 🔎 (tijdreeks nodig) |

---

## 4. Belangrijkste bevindingen uit de eerste profiling

1. **42,4% zonder tarief** — grootste bevinding; AFIR verplicht prijstransparantie.
2. **`open`-vlag onduidelijk** (86% false met vrije connectoren) — semantiek
   uitzoeken; klassiek "unknown-unknown" dat de inductieve methode opleverde.
3. **45% zonder owner_name** — attributie naar de echte eigenaar ontbreekt vaak.
4. **Structureel schoon** waar het telt: 0 foute/(0,0)-coördinaten, 0 buiten NL,
   0 `available>total`, 0 dubbele id's, schone connector/vermogen-enums. Melden
   wat góed is, is net zo geloofwaardig als melden wat fout is.
5. **Party-id vs merk-mismatch** — merken leveren onder hun platform-party-id
   (TotalEnergies→GreenFlux, EQUANS→LastMileSolutions); zie
   [`NDW_DATA_QUALITY_REPORT.md`](NDW_DATA_QUALITY_REPORT.md).

---

## 5. Volgende stappen
- [ ] Ernst/drempels bevestigen met DOT-team (met name `t_open_flag_semantics`,
      staleness-grenzen, "verdwenen"-definitie).
- [ ] Elke ✅-check als herbruikbare SQL/functie vastleggen (single source of truth).
- [ ] Per-CPO-scorecard bovenop deze checks bouwen (oprol per operator).
- [ ] Snapshot-historie opbouwen voor de 🔎-tijdreekschecks (verdwenen/flapperen).
- [ ] Self-service validator: CPO plakt zijn OCPI-payload, krijgt deze checks terug.
