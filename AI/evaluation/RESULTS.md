# Rezultatele experimentelor ASR

Fiecare rând: ce s-a schimbat, pe ce date s-a măsurat, rezultatul și decizia. Setările adoptate sunt în `config.yaml`.

**Limita importantă:** există o singură înregistrare medicală reală (Medpark, 12 min), cu 2 fragmente transcrise manual
(`tests/reference/medpark.txt`, 349 de cuvinte). Toate reglajele de mai jos sunt făcute pe ea, deci nu mai e un test
independent. Diferențe de ~1–3 puncte WER (~5–10 cuvinte) sunt în zona de zgomot.

## Medpark, WER/CER pe referință (`python -m tests.evaluate tests/reference/medpark.txt JOB`)

Referința a fost corectată pe 26.09: se spune „patul 8”, nu „pacientul 48”. Rândurile marcate „vechi” sunt pe
referința inițială (346 de cuvinte).

| Configurație | WER | CER | Decizie |
|---|---|---|---|
| medium, detecție automată de limbă, fără diarizare (vechi) | 81.2% | 67.4% | — |
| large-v3, detecție automată (vechi) | 89.6% | 79.3% | detectorul confundă româna moldovenească cu rusa |
| large-v3, `--language ro` (vechi) | 62.7% | 43.4% | bun pe română, dar pierde rusa |
| large-v3 + diarizare, comparare ro/ru fără bonus (vechi) | 65.9% | 48.6% | — |
| … + bonus ro 0.3 (vechi) | 60.1% | 41.2% | — |
| **… + bonus ro 0.5, fără prompt** (vechi) | **59.5%** | **40.1%** | **adoptat**: egal cu româna forțată, rusa rămâne posibilă |
| … + prompt de domeniu (vechi) | 63.0% | 41.8% | respins: inventa „patul 8” din exemplele promptului |
| … + `--denoise` (noisereduce) (vechi) | 63.0% | 43.4% | respins |
| … + `--beam-size 10` (vechi) | 57.8% | 40.7% | neconcludent (CER mai slab), nu s-a adoptat |
| … + VAD prag 0.35 (vechi) | 59.8% | 38.0% | neconcludent |
| setarea adoptată, pe referința corectată | 59.9% | 40.4% | — |
| **+ hotwords (`glossary/hotwords.*.txt`)** | **55.6%** | **33.1%** | **adoptat** (cost ~+40% timp de ASR) |
| + prompt de domeniu | 71.1% | 51.2% | respins |
| + prompt + hotwords | 71.9% | 55.0% | respins |

Descompunerea erorilor la setarea adoptată (fără hotwords): 44.8% cuvinte corecte, 37.9% înlocuite, 17.3% lipsă
(mai ales intervenții scurte suprapuse peste alt vorbitor), 4.3% în plus. Cuvintele lipsă nu vin din VAD (pragul
mai sensibil nu le-a recuperat), ci din vorbirea suprapusă.

## Amestec de limbi (`python -m evaluation.lang_mix`)

„Ședință” sintetică de 7.9 min din fragmente reale cu transcriere cunoscută: 24 în română moldovenească (Kaggle,
doar videoclipurile păstrate pentru test), 12 în rusă și 6 în engleză (FLEURS dev), în blocuri de 1–4 fragmente.
Pipeline complet, cu diarizare.

| Configurație | ro: limbă ok / WER | ru: limbă ok / WER | en: limbă ok / WER |
|---|---|---|---|
| bonus ro 0.5 față de **orice** limbă | 100% / 59.3% | 92% / 25.6% | **50%** / 31.4% (engleza tradusă în română) |
| **preferință ro peste ru 0.5 (doar duelul ro–ru)** | 100% / 59.3% | 92% / 25.6% | **100% / 1.3%** |

Medpark cu regula nouă: identic (55.6% / 33.1%), deci fără regresie. Regula nouă e adoptată (`language_preference`).

**WER-ul pe română e supraestimat:** fragmentele Kaggle conțin adesea mai multă vorbire decât transcrierea lor
(ex. audio „…nu a fost posibil, cancerul deja avansase”, transcriere până la „posibil”). Transcrierea large-v3 pe
aceste fragmente e în mare corectă. Consecințe: (1) accentul moldovenesc pe vorbire clară nu e problema principală;
(2) etichetele Kaggle nu pot fi folosite la fine-tuning fără filtrare, altfel modelul ar învăța să omită vorbire.

## Viteza ASR (27.09)

Baza: ~3 decodări beam 5 per segment (ro și ru fără hotwords pentru alegerea limbii, apoi limba aleasă cu
hotwords). Medpark 12 min: ASR 113.8 s; amestecul de limbi 7.9 min: 71.9 s.

| Variantă | ASR Medpark | WER / CER Medpark | ASR amestec | ru: limbă ok / WER | Decizie |
|---|---|---|---|---|---|
| bază | 113.8 s | 55.6% / 33.1% | 71.9 s | 92% / 25.6% | — |
| B: hotwords și la decodările de comparație (fără decodare finală separată) | 89.2 s | 55.9% / 33.5% | 57.2 s | **83% / 41.0%** | respins: hotwords-urile românești umflă scorul ro și strică duelul cu rusa |
| A+B: + fără comparație când detectorul e sigur (≥ 0.8) pe o limbă care nu e „pierzătoare” | 88.3 s | 55.9% / 33.5% | — | — | câștig neglijabil pe Medpark: româna moldovenească e rar detectată sigur |

| C: comparația cu greedy (beam 1) | 109.8 s | 56.2% / 34.3%, 3.2% chirilic | 64.9 s | 100% / 14.5% | nu: scoruri pe altă scară decât cea pe care e reglată preferința ro>ru 0.5 (Medpark primește rusă); câștig mic |
| C + batch 16 | 131.3 s | idem | 79.6 s | idem | respins: mai lent |
| **D: comparația decodată direct din encoderul de la detecție** (`compare_from_encoder`) | **79.6 s** | **55.6% / 33.1%** | **50.3 s** | 92% / 25.6% | **adoptat**: rezultate identice, −30% timp |

Măsurare pe 32 de segmente Medpark: encoder 3.6 s, decodare ro+ru 5.0 s (greedy) / 6.0 s (beam 5). Lățimea
beam-ului contează puțin; costul era encoderul rulat de 4 ori per segment (detecție, ro, ru, final), acum de 2 ori.

Concluzie: comparația între limbi trebuie făcută în condiții identice (fără hotwords/prompt).

Engleză scurtă tradusă (găsit pe 2 h de engleză TTS): 94 din 1734 de segmente ieșeau în ro/ru, fiindcă traducerea
are scor mai bun decât transcrierea pe fraze scurte, iar preferința ro>ru sărea peste engleză. `compare_skip_prob:
0.8` (detecție sigură pe o limbă care nu e ru => fără comparație) + preferința doar în duelul ro–ru: 12 -> 1 pe
primele 15 min; Medpark și amestecul de limbi identice (55.6% / 33.1%; en 100%, ru 92%).

Glosar extins (27.09): +10 termeni de terapie intensivă ca hotwords ro/en (dobutamină, meropenem, vancomicină,
amikacină, gazometrie, hemocultură, hidronefroză, nefrostomă, atelectazie, șoc septic; promptul ro la 216/223 tokeni):
Medpark 56.4% / 34.0% (față de 55.6% / 33.1%), en WER 3.9% (față de 1.3%), ru 26.9% (față de 25.6%). **Respins**;
termenii rămân în terms.tsv fără hotword. Hotword-ul se poate activa acum per limbă (coloana `hotword`: `ro,en`).

Normalizarea audio (ffmpeg): `aresample=16000` înainte de `loudnorm` era de 2× mai rapid (12 min: 13.6 s -> 6.5 s),
dar schimbă audio-ul: Medpark cap-coadă WER 58.5% / CER 35.2%, 4.4% chirilic (față de 55.6% / 33.1%). **Revenit**;
o rulare nouă de la zero (normalizare, diarizare, ASR) reproduce exact 55.6% / 33.1%.

## Cap-coadă (RTX 5070, 27.09)

| Înregistrare | normalize | diarizare + VAD | ASR | LLM | Total |
|---|---|---|---|---|---|
| Medpark 12 min, prin pagina web (n8n) | | | | | 3:54 (înainte 5:20) |
| 2 h sintetic (engleză TTS în buclă, alt pacient la ~50 s) | 63 s | 170 s | 917 s | 1465 s (125 de fragmente) | 43:37 |

| 2 h sintetic, versiunea finală (27.09, prin pagina web, cu un joc deschis pe GPU) | 251 s | 218 s | 779 s | 1557 s (97 de fragmente) | **47:00** |

LLM-ul pe 2 h: 9 apeluri de împărțire (128 s), 126 de extracții (~10 s fiecare, dominate de generarea răspunsului).
În versiunea finală: 8 apeluri de împărțire (115 s), 97 de extracții de ~14,5 s: viteza de generare e normală
(~66 tokeni/s), dar un răspuns are ~994 de tokeni (față de ~634): ~2,3 cazuri per fragment, constatări care repetă
deciziile, două rezumate. ASR-ul: toate cele 1 729 de segmente în engleză (înainte, 94 traduse în ro/ru).
Fișierul sintetic e cel mai rău caz pentru LLM; o ședință reală de 2 h cu 20–40 de pacienți ar avea de 3–4 ori mai
puține extracții (estimat ~27–30 min total, nemăsurat).

## Procesul-verbal față de faptele confirmate (Medpark, 27.09)

`python -m evaluation.mom_checklist tests/reference/medpark_facts.json JOB -v`: 43 de fapte confirmate de echipă
(patul 8: 15, patul 9: 8, boxa: 20), căutate în cazul pacientului corect. Lista e locală (date medicale).
qwen3:8b variază de la o rulare la alta cu ±3–4 fapte, deci fiecare variantă e rulată cu 3 seed-uri.

| Variantă | Rulări | Medie |
|---|---|---|
| procesul-verbal din 26.09 după-amiază | 1 | 49% |
| prompt + filtrele din 27.09 (adoptat) | 67%, 70%, 65% | **67%** |
| + reguli de ortografie medicală, „nu scrie ce nu s-a discutat”, „starea nu e decizie” | 70%, 65%, 60% | 65%, respins |
| + a doua trecere pe fragmentul fiecărui pacient (`complete.enabled`) | 67%, 74%, 63% | 68%, +20% timp, oprit |

Din faptele lipsă, 3 nu există în transcriere (creatinină 240, linie arterială, Diacarb): le-a pierdut ASR-ul.
Restul sunt în replici lungi și dense, pe care modelul de 8B le rezumă incomplet.

## Lungimea segmentelor (27.09)

Diarizarea refolosită, doar segmentarea și ASR-ul refăcute. Whisper e antrenat pe ferestre de 30 s.

| Variantă | Medpark WER / CER | amestec: ro WER | ru: limbă corectă | Decizie |
|---|---|---|---|---|
| tăiere peste 15 s, max 25 s (vechi) | 55.6% / 33.1% | 59.3% | 92% | — |
| **S1: tăiere peste 25 s, max 28 s** | **54.7% / 31.7%** | 59.3% | 92% | **adoptat** (aceeași viteză) |
| S2: S1 + unește pauzele aceluiași vorbitor până la 1 s | 54.2% / 32.1% | 58.3% | **83%** | respins: unește replici în limbi diferite |
| S3: S2 + segment minim 2 s | 55.9% / 34.4% | 58.3% | 83% | respins |

Pe transcrierea S1, LLM-ul găsea sub-părți ale unui pacient ca „subiecte” („Intervenție: stentare”, „Drenul
închis”). De aceea și un subiect venit imediat după un pacient cere un semn de trecere în primele cuvinte ale replicii
(„punctul…”, „protocolul…”, „gărzi”, „incident”…); la fel un pacient nou („pacientul…”, pat/boxă/salon), căutat tot
la începutul replicii („...a pacientului” din mijlocul frazei nu e un anunț). Rezultat: Medpark 3 pacienți în 3/3
rulări, consiliu_30min 12/12 puncte și 54/57 fapte esențiale (95%), ședința administrativă doar subiecte.

## LLM pe ședințe lungi: repetiții și viteză (27.09)

Pe 2 h (fișierul sintetic: o ședință de 5 min repetată de 24 de ori), procesul-verbal avea 165 de constatări pentru
~7 puncte reale (aceleași fapte, reformulate la fiecare reluare), iar LLM-ul scria ~994 de tokeni per fragment.

| Variantă (2 h, doar LLM) | Fragmente | Tokeni/apel | Timp LLM | Puncte | Constatări |
|---|---|---|---|---|---|
| înainte | 97 | 994 | 26 min | 11 | 165 |
| + răspunsuri scurte, constatări reformulate unite, subiecte duplicate unite | 164 | 691 | 31 min | 13 | 65 |
| + fragment nou la fiecare anunț de punct („First item…”) | 142 | 897 | 33 min | 12 | 122 |
| **+ anunțul doar mută începutul pus de model (adoptat)** | 97 | 1015 | 26 min | 13 | 117 |

Concluzii: (1) viteza pe 2 h depinde de numărul de fragmente, nu de instrucțiunile de răspuns scurt: cu 97 de
fragmente modelul scrie tot ~1 000 de tokeni per apel (scăderea la 691 venea din fragmentele mai mici); (2) unirea
constatărilor reformulate scade repetițiile (165 -> 117); (3) fragmentele adăugate la anunțuri făceau LLM-ul mai lent
și dublau punctele, iar un punct ratat la împărțire e separat oricum la extracție, deci anunțul doar mută începutul
pus de model (71 de începuturi aliniate pe 2 h). Pe consiliu_30min: 12/12 puncte în toate rulările (89–93% din faptele
esențiale), ~6% mai rapid; Medpark: 3/3 pacienți în 3/3 rulări.

Următorul câștig real de viteză ar fi 2 fragmente procesate în paralel de Ollama (`OLLAMA_NUM_PARALLEL=2`), dar
cere memorie video în plus (două contexte de până la 16k tokeni); netestat, pentru că placa de pe calculatorul de
demo nu e cunoscută.

## Corecția după glosar (`postprocess.glossary_correction`, 27.09): respinsă

Glosarul extins cu vocabular de terapie intensivă (germeni, antibiotice, antifungice, specialități; `hotword` 0,
deci ASR-ul nu se schimbă). Pe transcrierea Medpark corecțiile sunt bune (prag 80: picreatinină → creatinină,
gluconazol → fluconazol, neprostoma → nefrostomă, hidroniferoză → hidronefroză, telectizie → atelectazie,
acarbă → diacarbă), WER 55.6% -> 55.3%. Dar pe **text corect** (2 906 cuvinte din ședințele de test) corectorul
schimbă sensul chiar la pragul cel mai strict (88): colecistului → colecistitului, dureze → diureze; la 84:
tromboliză → tromboză, apendice → apendicite; la 80: Doamna → Dopamina. Într-un document medical o corecție greșită
e mai periculoasă decât un cuvânt vizibil deformat => rămâne oprită.

## Formate și cazuri-limită (prin pagina web, 27.09)

Primele 2 min din Medpark convertite în formatele pe care le poate aduce cineva:

| Fișier | Rezultat | WER pe fragmentul 0:00–1:32 |
|---|---|---|
| m4a original | ok | 54.2% |
| video mp4 / mov / webm, ogg-opus, flac, wav stereo 48 kHz | ok | 56.9% (mp4) |
| telefon: AMR 8 kHz (.3gp), mp3 8 kHz | ok | **64.8%** (AMR): banda îngustă costă ~10 puncte |
| liniște 60 s, muzică/zgomot 60 s | proces-verbal „nu s-a detectat vorbire”, nimic inventat | — |
| video fără sunet | eroare clară: „Fișierul nu conține niciun stream audio” | — |
| text redenumit .mp3 | eroare clară: „Fișierul nu poate fi citit ca audio sau video” | — |

Înainte de corecție, liniștea și muzica opreau pipeline-ul (0 segmente => `asr.jsonl` nu era scris).

## Nevalidat încă

- Bonusul ro 0.5 pe **rusă reală**: dacă rusa vorbită iese transcrisă ca română, bonusul trebuie scăzut.
- Orice setare pe alte înregistrări medicale (nu există).
