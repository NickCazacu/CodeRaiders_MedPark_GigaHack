# Intrarea pentru LLM: `llm_input.json`

Pipeline-ul audio scrie la final, pentru fiecare ședință, `jobs/<job_id>/llm_input.json`. Fișierul
conține transcrierea ședinței împărțită în bucăți care încap în contextul modelului.

Exemplu complet, generat cu același cod ca pipeline-ul, din date sintetice:
[`docs/llm_input.example.json`](llm_input.example.json). Se regenerează cu `python -m tests.make_llm_example`.

## Pe scurt: ce trimiteți la LLM

Pentru fiecare element din `windows`, trimiteți `windows[i].text` în prompt. Textul are câte o replică pe linie:

```
[00:21] SPEAKER_01: Diureza cum este?
[00:26] SPEAKER_00: Diureza e scăzută, 400 ml pe noapte, creatinina 240.
[00:32] SPEAKER_02: Давайте повторим креатинин вечером и решим по гемодиализу.
[00:41] SPEAKER_01: De acord. Și ecografia? [?]
```

- `[mm:ss]`: momentul din înregistrare în care începe replica. Minutele pot depăși 59, de exemplu `[125:03]` la 2 h.
- `SPEAKER_00`, `SPEAKER_01`, …: vorbitori anonimi dați de diarizare. **Nu sunt nume.** Aceeași etichetă înseamnă
  același vorbitor în toată ședința. Fără diarizare, toate replicile au `UNK` (vezi „Stadiul actual”).
- ` [?]` la final: transcrierea replicii e nesigură, fie pentru că modelul ASR a avut încredere mică, fie pentru că limba e incertă.
- Textul e în **română, rusă sau engleză**, amestecate chiar în aceeași ședință. Nu e tradus.

O ședință scurtă are o singură fereastră. O ședință de 2 h are mai multe, de ~3500 de tokeni fiecare.

## Structura

```jsonc
{
  "job_id": "exemplu_sedinta",
  "audio_duration_s": 61.0,               // durata înregistrării, în secunde
  "speakers": ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
  "languages": {"ro": 6, "ru": 2},        // câte segmente ASR are fiecare limbă
  "format": "[mm:ss] SPEAKER: text  (\" [?]\" = încredere scăzută)",
  "token_estimate": "utf8_bytes/4",       // cum e estimat tokens_est (vezi mai jos)
  "n_turns": 7,
  "n_windows": 2,
  "turns":   [ /* replicile, în ordine */ ],
  "windows": [ /* bucățile de trimis la LLM */ ]
}
```

### `turns[]`: o replică = segmente consecutive ale aceluiași vorbitor

| Câmp | Tip | Sens |
|---|---|---|
| `id` | int | indexul replicii (0, 1, 2…), folosit în `windows[].turn_ids` |
| `speaker` | str | `SPEAKER_NN` sau `UNK` |
| `start`, `end` | float | secunde de la începutul înregistrării |
| `line` | str | linia gata formatată, exact cum apare în `windows[].text` |
| `low_confidence` | bool | `true` dacă cel puțin jumătate din durata replicii e nesigură; atunci `line` se termină cu ` [?]` |
| `low_confidence_ratio` | float | ce parte din durata replicii e nesigură (0–1) |
| `langs` | list[str] | limbile segmentelor din replică, în ordinea apariției, de exemplu `["ro", "ru"]` |
| `segment_ids` | list[int] | segmentele ASR din care e formată replica (pentru trasabilitate, vezi `transcript.json`) |
| `tokens_est` | int | estimarea de tokeni pentru `line` + newline |

O replică foarte lungă (monolog) se împarte în mai multe replici ale aceluiași vorbitor, de câte ~800 de tokeni.

### `windows[]`: ce se trimite la LLM

| Câmp | Tip | Sens |
|---|---|---|
| `index` | int | 0, 1, 2… |
| `turn_ids` | [int, int] | prima și ultima replică din fereastră (inclusiv) |
| `overlap_turns` | int | **câte replici de la începutul ferestrei sunt repetate din fereastra anterioară** (0 la prima) |
| `start`, `end` | float | intervalul acoperit, în secunde |
| `tokens_est` | int | estimarea de tokeni a lui `text` (≤ ~3500) |
| `text` | str | liniile replicilor `turn_ids[0]..turn_ids[1]`, separate prin `\n` |

## Reguli de folosire

1. **Suprapunerea.** Primele `overlap_turns` linii dintr-o fereastră (de obicei 3) repetă finalul ferestrei anterioare,
   ca LLM-ul să aibă context. Tratați-le doar ca context: nu extrageți de două ori deciziile sau sarcinile din ele.
   Cel mai simplu e să îi spuneți modelului în prompt: „primele N replici sunt doar context”.
2. **Citări.** Cereți modelului să citeze timpul `[mm:ss]` pentru fiecare decizie sau afirmație, ca să poată fi verificată în audio.
3. **Replicile `[?]`.** Pot conține cuvinte greșite, mai ales termeni medicali, cifre și nume. LLM-ul nu trebuie să
   „repare” inventând, iar informațiile care vin doar din replici `[?]` trebuie marcate ca nesigure în rezumat.
4. **Erori ASR previzibile** chiar și fără `[?]` (exemple reale din ședința de test):
   - abrevieri colocviale nerecunoscute sau deformate: „Nor” / „nori” = noradrenalină, „Dobu” = dobutamină, „EKS/ECS”;
   - date și cifre: „pe data de 11” → „spidat de uzgrăci”, „fracție de 38-40” → „3.6-3.4.10”;
   - terminații și forme dialectale: „dozile” în loc de „dozele”, „tromboaspirație” în loc de „tromboaspirația”;
   - intervenții scurte ale altui vorbitor („Da.”, „Cardiac și trivascular, nu?”) lipite de replica vorbitorului principal
     sau lipsă.
   LLM-ul poate interpreta abrevierile din context, dar **cifrele, dozele și datele din replici `[?]` nu trebuie prezentate
   ca sigure** în procesul-verbal.
5. **Datele sunt medicale.** Pipeline-ul rulează local. Dacă LLM-ul rulează în alt loc, trebuie discutat înainte de a trimite ceva.
6. **Estimarea de tokeni** e aproximativă (octeți UTF-8 / 4), fără tokenizer-ul modelului vostru. Pentru engleză e aproape exactă,
   pentru română și rusă supraestimează ușor, deci ferestrele încap. Dacă modelul are alt buget, schimbați
   `pack.window_tokens` în `config.yaml` și rulați din nou doar etapa `pack` (`python -m pipeline.pack_for_llm <job_id>`,
   după ce ștergeți `llm_input.json`).

## Stadiul actual (26.09.2026)

**Fișierul real de lucru:** `AI/jobs/Medpark_final/llm_input.json` (ședința de test de 11:42, 3 vorbitori,
68 de replici, o fereastră de ~2.5k tokeni). Nu e în git (date medicale): cereți-l local sau rulați pipeline-ul.
Pentru cod și teste automate folosiți `docs/llm_input.example.json` (sintetic).

- **Diarizarea e activă** (pyannote 3.1, local): vorbitorii sunt `SPEAKER_00..02`, fără nume. Același om poate avea altă
  etichetă în altă ședință.
- **Calitatea măsurată** pe 2 fragmente transcrise manual (346 de cuvinte): WER 59.5%, adică **~45% din cuvinte exact
  corecte**. Încă ~38% sunt apropiate (formă, terminație) sau greșite, iar ~17% lipsesc, mai ales intervențiile scurte suprapuse.
  Textul e suficient pentru **subiect, pacient, diagnostic, tratament și decizii**, dar **nu e sigur la cifre, doze și date**.
- În ședința de test, 32 din 68 de replici sunt `[?]`. E normal pentru acest audio.
- **Limbi:** româna e aproape exclusivă în test. Rusa și engleza sunt suportate, dar recunoașterea rusei nu e încă validată
  pe o înregistrare reală. Formatul nu se schimbă.
- Structura JSON-ului e **stabilă**. Îmbunătățirile viitoare ale ASR-ului schimbă doar textul, nu câmpurile.

## Dacă aveți nevoie de mai mult detaliu

`jobs/<job_id>/transcript.json` conține, per segment ASR: limba, scorurile, probabilitatea fiecărui cuvânt cu timpul lui și
motivul pentru care un segment a fost eliminat (de exemplu halucinații). Legătura se face prin `turns[].segment_ids`.
