# Evaluare ASR (1B): date, glosar, gold, WER

Toate comenzile se rulează din `AI/`. Tot ce e aici rulează **fără GPU** (doar `pyyaml`, `numpy`, `soundfile`,
`rapidfuzz`, `pyarrow`, `huggingface_hub`). ASR-ul (`pipeline.asr`) rulează pe mașina cu GPU; rezultatele
(`jobs/<id>/transcript.json`) se evaluează oriunde.

`tests/evaluate.py` (WER/CER pe fragmentele din `tests/reference/*.txt`) rămâne comparația rapidă între joburi.
`evaluation/evaluate.py` e varianta detaliată: suprapuneri, termeni medicali, limbă, decizii/ETA și tabelul de rezultate.
Referința existentă se importă direct: `python -m evaluation.gold from-reference tests/reference/medpark.txt Medpark`.

## Fișiere

| Ce | Unde | În git? |
|---|---|---|
| Audio-ul ședinței | `inventory/Medpark_audio.m4a` → `jobs/Medpark/audio_16k.wav` | nu |
| Glosar medical RO/RU/EN + abrevieri | `glossary/terms.tsv` | da |
| Hotwords (generate din glosar) | `glossary/hotwords.<lang>.txt` | da |
| Gold (etichete Audacity + `gold.json`) | `evaluation/gold/<NAME>/` | **nu** (conținut medical) |
| Eșantion română moldovenească | `data/external/moldovan_dialect/sample/` | nu (drepturi rezervate) |
| Rezultate | `evaluation/results.csv`, `evaluation/RESULTS.md` | da |
| Raport detaliat per rulare | `jobs/<id>/eval_<gold>.json` | nu |

## Fluxul

```powershell
# 0. o singură dată: audio normalizat (ffmpeg -> 16 kHz mono)
python -m pipeline.ingest inventory\Medpark_audio.m4a --job-id Medpark
python -m pipeline.normalize Medpark

# 1. baseline pe GPU (config.yaml implicit: fără prompt, fără hotwords, fără corecție)
python run_pipeline.py inventory\Medpark_audio.m4a --job-id Medpark_base

# 2a. gold din referința existentă (fragmentele din tests/reference/medpark.txt)...
python -m evaluation.gold from-reference tests/reference/medpark.txt Medpark
# 2b. ...și, ca să ajungem la ~10 min, schiță pentru restul înregistrării din baseline + corectare în Audacity
#     (vezi mai jos). Replicile din referință se pot importa în Audacity ca pistă separată, ca să nu fie refăcute.
python -m evaluation.gold draft Medpark_base Medpark_full
python -m evaluation.gold build Medpark_full     # validează + statistici (suprapuneri, decizii/ETA)

# 3. evaluare: după FIECARE schimbare, un job nou + un rând în RESULTS.md
python -m evaluation.evaluate Medpark_base --gold Medpark --note "baseline large-v3"
```

Variante tipice (fiecare cu propriul `--job-id`, ca asr.jsonl să nu amestece setări):

| Schimbare | Cum | Ce se reface |
|---|---|---|
| hotwords | `python -m pipeline.glossary hotwords`, apoi `whisper.use_hotwords: true` | ASR (job nou) |
| prompt de domeniu | `glossary/prompt.<lang>.txt` (implicit fără: pe Medpark a crescut WER-ul, vezi `glossary/README.md`) | ASR (job nou) |
| bonusul pentru română | `--language-bias ro=0.3` | ASR (job nou) |
| post-corecție după glosar | `postprocess.glossary_correction.enabled: true` | doar postprocess: `python -m evaluation.clone_job Medpark_base Medpark_base_corr` |
| alt model | `--model large-v3-turbo` | ASR (job nou) |
| fără diarizare | `--no-diarization` | segment + ASR (job nou) |

Segmentarea diferă între joburi (VAD/diarizare), dar evaluarea nu depinde de ea: compară textul pe axa timpului.

## Eșantionul de română moldovenească

Corpus: [FraPiz/moldovan-dialectal-romanian-speech-corpus](https://huggingface.co/datasets/FraPiz/moldovan-dialectal-romanian-speech-corpus)
(70 h, lecții școlare, 56 de profesori). **Licență „rights reserved”**: doar evaluare locală, nu antrenare, nu redistribuire.
Nu are termeni medicali, rusă sau suprapuneri; e util pentru **alegerea limbii** (cât de des româna moldovenească
e luată drept rusă → calibrarea `whisper.language_bias`) și pentru compararea modelelor pe dialect.

```powershell
# o singură dată (rețea): metadate + ~600 de clipuri de la 30 de profesori, ~2 GB descărcați
hf download FraPiz/moldovan-dialectal-romanian-speech-corpus --repo-type dataset --include metadata.jsonl README.md --local-dir data\external\moldovan_dialect\raw
python -m evaluation.sample_moldovan

python -m evaluation.moldovan make-job --job-id md_large-v3
python -m pipeline.asr md_large-v3 ; python -m pipeline.postprocess md_large-v3      # GPU
python -m evaluation.moldovan score md_large-v3 --note "large-v3, cmp ro+ru"
```

## Adnotarea gold în Audacity

1. Deschideți `jobs/Medpark/audio_16k.wav`, apoi **File → Import → Labels…** cu `evaluation/gold/Medpark/utterances.txt`
   (schița din ASR). Importați în același fel `overlap.txt` și `events.txt` (goale la început), ca piste separate.
2. Ascultați **tot**, nu doar citiți schița: corectura trebuie să fie ce s-a spus, nu ce a auzit ASR-ul.
3. La final: selectați fiecare pistă → **File → Export → Export Labels…**, peste fișierul ei.
4. `python -m evaluation.gold build Medpark` verifică formatul și afișează statisticile.

### `utterances.txt`: o etichetă = o replică

Textul etichetei: `VORBITOR|limbă|text`, de exemplu `S2|ro|Tensiunea 80 pe 40, pe noradrenalină.`

- **Vorbitor**: `S1`, `S2`, … consecvent în toată ședința (nu nume). Schița are `UNK`/`SPEAKER_NN`: înlocuiți-le.
- **Limba**: `ro`, `ru`, `en` sau `mix` (replică cu schimbare de limbă la mijloc; nu intră în „limbă ok”).
  Termenii medicali rusești într-o frază românească nu fac replica `mix`.
- **Limite**: începutul/sfârșitul vorbirii, ±0.2 s. O replică lungă se poate împărți în mai multe etichete
  (ajută evaluarea, fiindcă timpul cuvintelor e interpolat în interiorul etichetei).
- **Suprapuneri**: când vorbesc doi deodată, fiecare are propria etichetă, iar etichetele se suprapun în timp.
  Suprapunerile se calculează automat din etichetele unor vorbitori diferiți.
- **Textul, exact cum s-a spus**:
  - cu diacritice (ș, ț cu virgulă), în alfabetul limbii vorbite: româna în latină, rusa în chirilică;
  - **numerele cu cifre**, ca Whisper: `patul 8`, `80 pe 40`, `38%`, `ora 12`;
  - abrevierile cum s-au pronunțat și cu majuscule: `TA`, `CT`, `ЭКГ` (dacă s-a spus „tensiunea arterială”, scrieți așa);
  - fără corectarea gramaticii sau a regionalismelor (`amu`, `îi` pentru „este”), fără cuvinte adăugate;
  - ezitările (`ăă`, `mmm`) nu se scriu; cuvintele repetate real („nu, nu”) se scriu;
  - neinteligibil: `[neclar]`; zgomote/râs: `[zgomot]`. Tot ce e între `[...]` e ignorat la WER.

### `overlap.txt`: regiuni de vorbire simultană (opțional)

Regiunile calculate din `utterances.txt` sunt de obicei suficiente. Marcați aici în plus suprapunerile scurte
(interjecții, „da”, „aha” peste altcineva) pe care nu le-ați transcris ca replică. Textul etichetei nu contează.

### `events.txt`: decizii și termene

- `DECISION: <rezumat scurt>` pe intervalul în care se formulează decizia (ex. „facem CT mâine”, „se transferă în cardiologie”);
- `ETA: <rezumat>` pe intervalul unde se spune un termen/moment (ex. „până la ora 12”, „mâine dimineață”, „peste 2 zile”);
- `REGION` (opțional): intervalul evaluat, dacă gold-ul nu acoperă toată înregistrarea.

Evaluarea raportează câte decizii și ETA cad în suprapuneri și WER-ul doar pe aceste intervale:
acolo o eroare ASR afectează direct procesul-verbal.

## Metrici (evaluation/evaluate.py)

- **WER/CER** pe text normalizat (litere mici, fără punctuație, `ş/ţ`→`ș/ț`, `ё`→`е`, cratima = spațiu).
  Referința și ipoteza se aliniază global, deci totalul erorilor nu depinde de timpi. Timpii decid doar dacă o eroare
  e în **clean** sau **overlap** și în ce limbă; pentru cuvintele gold, timpul e interpolat în replică,
  deci la granițele suprapunerilor atribuirea e aproximativă (împărțiți replicile lungi ca să o reduceți).
- **Termeni**: aparițiile din `glossary/terms.tsv` (cu flexiune: rădăcină + terminație) în gold vs. ASR, același concept
  și alfabet, la ±2 s. Recall total/clean/overlap și precizie (termeni în ASR fără corespondent în gold).
- **Limbă ok**: limba aleasă de ASR per segment vs. limba replicii gold cu care se suprapune cel mai mult.

## Glosarul

`glossary/terms.tsv`: un rând = un concept (id, categorie, forme RO/RU/EN, abrevieri RO/RU/EN, hotword).
Variantele se separă cu `|`. Verificare rapidă: `python -m pipeline.glossary find "text"`.
După ce se ascultă ședința, adăugați termenii care apar în ea și lipsesc (e cea mai bună sursă).

Hotwords: `hotword=1` pentru ~25 de termeni per limbă, cei rari/greu de recunoscut (Whisper știe deja „operație”).
Hotwords și `initial_prompt` împart ~224 de tokeni; când nu încap, faster-whisper taie din prompt. Fișierele
`glossary/hotwords.<lang>.txt` se folosesc doar cu `whisper.use_hotwords: true`, deci nu schimbă baseline-ul.

Post-corecția (`pipeline/correct.py`) înlocuiește doar cuvinte foarte apropiate de rădăcina unui termen, cu terminație
validă, în același alfabet; nu atinge participii („intubat”) sau cuvinte comune. Fiecare înlocuire e păstrată în
`transcript.json` (`corrections`, `text_raw`). Se pornește doar dacă tabelul arată că ajută.
