# LLM: extracția procesului-verbal (llm_input.json → minutes.json)

Citește `AI/jobs/<job_id>/llm_input.json` (scris de `AI/pipeline/pack_for_llm.py`) și produce `minutes.json`:
cazurile discutate, deciziile cu citat verbatim și timp, ETA-ul și întrebările deschise.
Rulează **doar** pe Ollama local (`qwen3:8b`), fără alte apeluri de rețea: URL-urile non-locale sunt
refuzate, iar proxy-urile din mediu sunt ignorate.

## Locul în repo

```
AI/     pipeline-ul audio (echipa ASR)        LLM/    acest modul
```

`LLM/` nu modifică nimic din `AI/`. Importă doar `pipeline.pack_for_llm` / `pipeline.common` (read-only;
`LLM/__init__.py` pune `AI/` pe `sys.path`) și, pentru un job real, scrie rezultatele lângă
`AI/jobs/<id>/llm_input.json` (`minutes.json`, `llm_debug/`, `review.md`, `mom.*`). Rulările manuale merg în
`LLM/runs/` (ignorat de git). Configurarea e separată: `LLM/config.yaml` + variabile `LLM_*`.

GPU: `qwen3:8b` ocupă ~6.2 GB VRAM și Ollama îl ține încărcat `keep_alive` (5 min) după ultimul apel.
Nu rula extracția în paralel cu ASR-ul pe aceeași placă; dacă ASR urmează imediat, setează `LLM_KEEP_ALIVE=0`
(serviciul o face): modelul rămâne încărcat pe durata extracției și e scos din VRAM la final.

## Rulare

Toate comenzile se rulează din rădăcina repo-ului. Pregătire unică: Ollama instalat și `ollama pull qwen3:8b`;
un mediu Python 3.11+ cu `pip install -r LLM\requirements.txt` (`pyyaml`, `rapidfuzz`; mediul pipeline-ului
din `AI/` le are deja).

```powershell
.\.venv\Scripts\python.exe -m LLM.extract AI\jobs\<job_id>\llm_input.json --date 2026-09-21
.\.venv\Scripts\python.exe -m LLM.extract <job_id> --date 2026-09-21 --out minute.json
```

Opțiuni: `--out` (implicit `minutes.json` lângă intrare), `--debug-dir` (implicit `llm_debug/` lângă
intrare), `--config` (implicit `LLM/config.yaml`), `--think-final` (`think: true` pentru apelul final).

Din pipeline, după ce s-a scris `llm_input.json`:

```python
from LLM.extract import extract, extract_job
extract_job(job_id, "2026-09-21")          # AI/jobs/<id>/minutes.json + etapa „llm” în status.json (sare dacă există)
report = extract(path, "2026-09-21")       # direct pe un fișier; întoarce raportul rulării
```

## Modificări 26.09 (după review-ul pe Medpark)

Review-ul procesului-verbal Medpark a arătat pacienți amestecați (boxa pusă la patul 9), pacienți de la final
lipsă și toate valorile clinice omise (creatinină, antibiotice cu doze, proceduri). Cauze și schimbări:

- **`facts`** pe fiecare caz (`schemas.FACT_CATEGORIES`: diagnostic, istoric, analize, imagistică,
  microbiologie, tratament, procedură, monitorizare, evoluție). Regula 6 spunea că „constatările nu sunt decizii”,
  dar schema nu avea unde să le pună, deci erau aruncate. Acum trec prin `sanitize` (categorie validă,
  doar română), `merge` (fără duplicate între ferestre, ordonate în timp) și `mom.py` („Constatări clinice”).
- **Modul pe pacienți** (`segment.enabled`, implicit activ): un apel scurt (`prompts/segment.md`) împarte
  ședința pe pacienți (ordine + moment de început), apoi fiecare pacient e extras separat
  (`prompts/patient.md`), cu 2 replici de context. Pe Medpark, extracția dintr-o singură fereastră dădea
  2 pacienți; cu împărțirea: patul 8, patul 9, boxa și pacienții de la final. Dacă transcrierea nu încape
  într-un apel sau apelul eșuează, se folosesc ferestrele ASR, ca înainte (`run.json`: `path`).
  În acest mod, `attribution.fix_attribution` nu mai mută decizii între pacienți: fragmentul stabilește
  pacientul, iar mutarea după „ultimul număr pomenit” lua pacientul anterior când cel curent e numit fără număr.
- `sanitize.fix_bed_numbers`: „patul nou” (fără număr) → „patul 9” (transcrierea pierde „ă” din „nouă”).
- Prompt: regulile 11, 13, 15, 16 (indicii de schimbare a pacientului, întrebări deschise, `facts`, toți
  pacienții). Exemplele din reguli sunt fictive, nu din ședința de test.
- Config: `stages.extract.num_predict` 4096 (răspunsul cu `facts` e mai lung), `max_num_ctx` 16384 (~7.5 GB VRAM
  cu qwen3:8b), `extract.max_facts` 20.
- Testate și respinse pe Medpark: `qwen3:14b` (tot 2 pacienți, de 3× mai lent), `think: true` la extracție
  (fără câștig clar), ferestre ASR mai mici sau replici de 300 de tokeni (împărțire prea fină).

## Modificări 27.09 (după testul de 2 h)

- **Ședințe lungi**: împărțirea pe pacienți se face pe bucăți consecutive de ~15 min (`segment.window_tokens` 3000);
  fiecare bucată știe ce pacient continuă, începuturile vecine cu același pat/boxă/salon/număr se unesc
  (`extract.same_patient`), iar o bucată eșuată se reîncearcă pe jumătăți. Cu bucăți de 30 min, qwen3:8b punea
  aceeași etichetă tuturor pacienților sau răspunsul era tăiat.
- **Decizii repetate** (recapitulare, pacient reluat): aceeași decizie, același status și aceleași numere → una
  singură, ultima apariție (`merge._collapse`, eveniment `drop_restated_decision`). Pe 2 h: 127 → 28 de decizii.
- **`case_key` fără diagnostic**: diagnosticul din cheie era copiat de la primul pacient la toți ceilalți
  („Patul 5, pneumonie comună”); acum doar identificarea, diagnosticul e în `topic`.
- **Viteză**: cu `keep_alive` 0 modelul se reîncărca la fiecare apel (~2.6 s) și reevalua tot promptul; acum rămâne
  încărcat pe durata rulării, iar `num_ctx`, odată mărit, rămâne stabil (altfel Ollama reîncarcă modelul).
  Medpark: ~3 min → 79 s.
- Reguli noi în `system.md`: unitatea de măsură doar dacă a fost spusă; zecimale rostite „X și Y”.
- **Nu doar pacienți**: unitatea procesului-verbal e „punctul discutat”, un pacient sau un alt subiect
  (organizare, gărzi, echipamente, protocoale, buget, incidente, instruiri). Împărțirea caută puncte (`segment.md`),
  subiectele au `case_key` cu tema („Organizare: …”, „Echipamente: …”) și categoria `informație`; exemplul
  few-shot are și un punct organizatoric. Testat pe `manual_tests/administrativ` (ședință fără pacienți: niciun
  pacient inventat, toate subiectele și deciziile), `consiliu_30min` (8 pacienți + 4 subiecte) și Medpark.
- **Doar informația de bază**: 3–6 elemente esențiale per punct (`extract.max_facts` 8), nu toate valorile.
- **Procesul-verbal** (`mom.py`): per punct diagnostic și istoric, stare clinică, paraclinic, tratament și
  proceduri, informații (subiecte), plan / decizii cu replica sursă, în așteptare; fără ordine de zi și termene vagi.
- Verificări în cod pe textul modelului: unități nespuse (inclusiv în rezumate), ziua săptămânii după replica
  citată, afirmații despre ce „nu s-a discutat”, chei prea lungi, chei deformate înlocuite cu identificatorul
  fragmentului („Pacient boxă”), timpul lipit în citate.
- **Împărțirea pe puncte verificată pe transcriere** (`extract.patient_windows`), pentru că modelul de 8B lua un
  diagnostic nou, o complicație sau istoricul operațiilor drept pacient nou (Medpark: 5–6 „pacienți” în loc de 3):
  - un pacient nou e păstrat doar dacă primele 10 cuvinte ale replicii lui au un semn: pat/boxă/salon/rezervă,
    „pacientul/pacienta”, „cazul”, „următorul”, „trecem” (și în rusă/engleză) — `NEW_PATIENT_CUE`;
  - un subiect venit imediat după un pacient cere un semn de trecere („punctul…”, „protocolul…”, „gărzi”,
    „incident”, „audit”… — `NEW_TOPIC_CUE`); subiectele după subiecte (ședințe de organizare) nu sunt afectate;
  - o replică ce începe cu alt loc (patul N, boxa, salonul N) deschide pacientul pe care modelul l-a ratat;
  - „Primul pacient” (deschiderea) se unește cu pacientul numit imediat după; aceleași pat/boxă/salon = același
    pacient la unire (`merge.key_score`), orice alte numere (vârsta);
  - o replică ce începe cu anunțul unui punct („Punctul patru, puțin administrativ”, „Mai am un punct...”, „Next
    item”; `TOPIC_ANNOUNCE`, primele 4 cuvinte) deschide subiectul ratat de model;
  - în fragmentul unui pacient, un „subiect” scos de model („Echipamente: monitorizare”) e unit cu pacientul, dacă
    fragmentul nu conține un anunț de punct;
  - ce s-a unit sau adăugat apare în `llm_debug/segment.response.json` (`dropped_no_cue`, `added_by_location`).
- **Fără repetiții în procesul-verbal** (`merge.py`): o constatare reformulată (pacient reluat, recapitulare) apare o
  singură dată (`same_fact`: aceleași numere, aceeași negație — „fără febră” ≠ „febră” —, rădăcini aproape identice;
  rămâne formularea cu mai multe cuvinte); constatările care doar repetă o decizie dispar; subiectele cu aceeași temă
  și altă categorie („Organizare: program MRI” = „Echipamente: programul MRI”) sunt un singur punct.
- **Răspunsuri mai scurte** în modul pe pacienți: cel mult 6 constatări, rezumatul cazului și al fragmentului câte o
  propoziție (`consiliu_30min`: 628 -> ~565 de tokeni generați per fragment, 187 -> ~177 s).
  Rezultat: Medpark 3 pacienți în toate rulările, `consiliu_30min` 12/12 puncte, ședința administrativă doar subiecte.
- **Măsurare**: `python -m evaluation.mom_checklist <listă> <job|minutes.json> -v` (din `AI/`): faptele esențiale
  găsite la pacientul corect și numărul de puncte față de cel așteptat. Liste: `AI/evaluation/checklists/`
  (ședințe fictive) și `AI/tests/reference/` (Medpark, locală, date medicale).

## Cum lucrează

1. **Încărcare** (`loader.py`): adaptorul transformă `llm_input.json` în `Meeting` / `Window` / `Turn`.
   Textul curat al unei replici e `line` fără `[mm:ss] SPEAKER:` și fără ` [?]`. `turn_ids` e
   `[prima, ultima]` (acceptă și lista completă). O replică e nesigură dacă are `low_confidence`, `[?]`,
   `low_confidence_ratio` peste prag sau (când ASR-ul îl va adăuga) `overlap: true`.
2. **Verificarea contextului**: pentru fiecare fereastră, `(prompt fix + tokens_est) × token_factor +
   antet + num_predict` față de `num_ctx`. Depășirile sunt raportate (`context_check.json`, `run.json`),
   niciodată trunchiate: `on_overflow: expand` mărește `num_ctx` până la `max_num_ctx`, `skip` sare fereastra.
3. **O fereastră** (`n_windows == 1`): un apel scoate rezumatul și toate cazurile.
4. **Mai multe ferestre**: un apel per fereastră, în ordine. Antetul conține data, cazurile cunoscute
   (`case_key — stare`) și rezumatul ferestrei anterioare. Primele `overlap_turns` linii sunt marcate
   CONTEXT (nu se extrage din ele).
5. **Verificări în cod după fiecare fereastră** (`quotes.py`, `attribution.py`):
   - fiecare citat → `turn_id` (timestamp + rapidfuzz pe replicile ferestrei; fără potrivire: `null`);
   - decizie pusă la alt pacient: pacientul „curent” al replicii citate e ultimul pacient/salon numit
     la sau înaintea ei. Dacă diferă de numărul din `case_key`, decizia (și ETA-ul din aceeași replică)
     se mută la cazul potrivit, inclusiv la un caz cunoscut din ferestrele anterioare;
   - aceeași replică revendicată de mai multe cazuri rămâne doar la cazul potrivit;
   - `eta.raw` trebuie să fie în transcriere: o parafrază apropiată e înlocuită cu fragmentul exact
     din replică, altfel (ex. o traducere) devine `null`. Confuziile de tip fără echivoc se corectează:
     zi a săptămânii / „mâine” marcate `absolute` → `relative`; „каждые 6 часов” / „zilnic” → `recurring`;
   - un caz repetat de ≥ 3 ori în același răspuns e semnalat (buclă). Schema are `maxItems` și
     reîncercarea folosește alt `seed`.
6. **Reduce** (`merge.py`):
   - se elimină deciziile din replicile de context deja extrase în fereastra anterioară;
   - cazurile se grupează după `case_key` (rapidfuzz; numere diferite = pacienți diferiți; perechile
     ambigue primesc un apel scurt „același caz?”);
   - deciziile se ordonează în timp; ultimul ETA cu tip ≠ `none` câștigă;
   - `superseded`: pentru fiecare caz cu ≥ 2 decizii, un apel scurt („care decizii au fost schimbate
     sau anulate de una ulterioară?”) pe toate deciziile cazului. Dacă apelul eșuează, se folosește
     `replaces_previous` din extracție;
   - un apel final scrie `meeting_summary` și ordonează cazurile pe teme.

Un apel eșuat (timeout, JSON invalid, răspuns tăiat) se reîncearcă o dată. Apoi e notat în
`run.json` și rularea continuă cu restul ferestrelor.

## Ieșire (`minutes.json`)

```json
{
  "meeting_summary": "3–5 propoziții în română, cu [mm:ss]",
  "cases": [{
    "case_key": "Pacient 48, cardiologie",
    "topic": "…",
    "discussion_summary": "… [00:32]",
    "decisions": [{"decision": "…", "status": "aprobat", "quote": "verbatim, limba originală",
                   "timestamp": "00:32", "turn_id": 3, "superseded": false, "needs_review": false}],
    "eta": {"type": "relative", "raw": "вечером", "date": null, "condition": null, "needs_review": false},
    "open_questions": ["… [00:41]"]
  }]
}
```

- `status`: `aprobat` | `respins` | `amânat` | `necesită investigații suplimentare` | `în discuție`
- `eta.type`: `absolute` | `relative` | `duration` | `conditional` | `vague` | `recurring` | `none`.
  `raw` = expresia exactă (`null` la `none`), `condition` doar la `conditional`, `date` mereu `null`
  (o calculează validarea).
- `timestamp` = `mm:ss` al replicii citate, ca în transcriere. `turn_id` e pus de cod; `null` = citatul
  nu a fost găsit în fereastră.
- `needs_review` rămâne `false`: îl setează validarea (ex. `turn_id: null` sau replică nesigură; vezi
  `turn_uncertain` în `llm_debug/quotes.json`).
- Listele goale sunt valide.

## Debug: `AI/jobs/<job_id>/llm_debug/` (rescris la fiecare rulare)

| Fișier | Conținut |
|---|---|
| `context_check.json` | estimarea de tokeni per fereastră față de `num_ctx` |
| `window_NNN.prompt.txt` | mesajele trimise (system, exemplul few-shot, fereastra) |
| `window_NNN.response.json` | răspunsul brut, JSON-ul parsat, corecțiile, cazurile după rezolvarea citatelor |
| `quotes.json` | citat → replică: scor, potrivire de timp, `exact`, `in_context`, `turn_uncertain` |
| `checks.json` | corecțiile din cod: decizii mutate la alt pacient, revendicări duble, `eta.raw` negăsit |
| `same_case.json` | întrebările „același caz?” și răspunsurile |
| `supersede.json` | întrebările „ce decizii au fost înlocuite?” și răspunsurile |
| `merge.json` | evenimentele de unificare (duplicate eliminate, potriviri fuzzy) și cazurile rezultate |
| `final.prompt.txt`, `final.response.json` | apelul final |
| `run.json` | timpi, tokeni per apel, erori, avertismente, `token_factor_observed`, configurația |

## Configurare (`LLM/config.yaml`)

| Cheie | Implicit | Variabilă de mediu |
|---|---|---|
| `ollama.url` | `http://localhost:11434` (doar local) | `LLM_OLLAMA_URL` |
| `ollama.model` | `qwen3:8b` | `LLM_MODEL` |
| `ollama.timeout_s` / `retries` | 300 / 1 | `LLM_TIMEOUT_S` / `LLM_RETRIES` |
| `ollama.num_ctx` / `temperature` | 8192 / 0.15 | `LLM_NUM_CTX` / `LLM_TEMPERATURE` |
| `ollama.keep_alive` | `5m` | `LLM_KEEP_ALIVE` |
| `ollama.repeat_penalty` | 1.1 | |
| `stages.<extract,same_case,supersede,final>.think` | `false` | `LLM_THINK_EXTRACT`, `LLM_THINK_SAME_CASE`, `LLM_THINK_SUPERSEDE`, `LLM_THINK_FINAL` (0/1) |
| `stages.*.num_predict` | 2048 / 32 / 128 / 1024 | |
| `extract.max_cases` / `max_decisions` | 12 / 8 (`maxItems` în schemă) | |
| `context.token_factor` | 1.55 (măsurat pe qwen3:8b) | `LLM_TOKEN_FACTOR` |
| `context.on_overflow` / `max_num_ctx` | `expand` / 12288 | `LLM_ON_OVERFLOW` / `LLM_MAX_NUM_CTX` |
| `context.header_max_cases` / `header_summaries` | 40 / 1 | |
| `input.uncertain_ratio` | 0.5 | `LLM_UNCERTAIN_RATIO` |
| `quotes.min_score` / `min_score_same_ts` / `short_quote_chars` | 80 / 65 / 12 | |
| `merge.same_score` / `diff_score` | 90 / 60 | |
| `merge.supersede` | `llm` (apel per caz) / `flag` (doar `replaces_previous`) / `all` | `LLM_SUPERSEDE` |

Promptul se editează în `LLM/prompts/`: `system.md` (reguli), `window.md` / `single.md` (mesajul cu
transcrierea), `same_case.md`, `supersede.md`, `final.md`, `fewshot.json` (exemplul RO/RU/EN, trecut
prin același șablon ca apelul real).

## Performanță și memorie (qwen3:8b, RTX 4070 Laptop 8 GB)

- Pe GPU, o fereastră durează 10–25 s; pe CPU, 70–130 s. `ollama ps` trebuie să arate `100% GPU`.
  Pe laptopurile cu comutator de GPU (Lenovo Legion: GPU Working Mode), placa NVIDIA poate fi
  deconectată pe baterie. Atunci Ollama rulează pe CPU.
- La `num_ctx` 8192, modelul ocupă ~6.2 GB VRAM.
- Transcrierea costă ~1.56 tokeni Qwen per `tokens_est`, iar promptul fix (reguli + exemplu) ~2 900
  tokeni. Ferestrele implicite ale ASR (`pack.window_tokens: 3500`) cer ~12k context, deci
  `on_overflow: expand`, strâns pe 8 GB. Recomandare pentru echipa ASR: `window_tokens` ≈ 2000–2500.

## Teste

```powershell
.\.venv\Scripts\python.exe -m LLM.tests                       # offline, fără Ollama: loader, context, citate, verificări, merge, flux complet cu client fals
.\.venv\Scripts\python.exe -m LLM.tests.test_scenarios        # cu qwen3:8b real; sărit dacă Ollama nu răspunde
                                                              # strict: pacientul corect per decizie, tipul exact de ETA, eta.raw verbatim
.\.venv\Scripts\python.exe -m LLM.tests.test_scenarios 30min  # doar regresia pe consiliul realist de 30 min (fixtures/consiliu_30min.json)
.\.venv\Scripts\python.exe -m LLM.tests.fixtures.build        # regenerează fixture-urile
```

## Teste manuale: dai un JSON, vezi punctele principale și termenele

Două foldere:
- `LLM/test_meetings/`: ședințele de test, câte un `.json` per ședință (vezi README-ul de acolo);
- `LLM/test_moms/`: procesul-verbal generat pentru fiecare, `<nume>.md`, rescris la fiecare rulare.

```powershell
.\.venv\Scripts\python.exe -m LLM.manual all --date 2026-09-26   # toate ședințele -> LLM/test_moms/*.md + tabel
.\.venv\Scripts\python.exe -m LLM.manual consiliu_30min          # doar una, după numele fișierului
```

Pentru o ședință nouă: pune JSON-ul în `LLM/test_meetings/` și rulează-l. Rulările lasă în git
diferențe în `LLM/test_moms/`, deci se vede ușor ce s-a schimbat în MoM după o modificare de prompt
sau de cod.

Orice alt JSON merge și direct, fără să-l copiezi în folder:

```powershell
.\.venv\Scripts\python.exe -m LLM.manual sedinta.json --date 2026-09-21    # rulează modelul și afișează rezultatul
.\.venv\Scripts\python.exe -m LLM.manual AI\jobs\<job_id>\llm_input.json      # un job real din pipeline
.\.venv\Scripts\python.exe -m LLM.manual review sedinta.json               # reafișează, fără model
```

JSON-ul poate fi `llm_input.json` (ieșirea pipeline-ului), `transcript.json` (segmentele din
postprocess, se împachetează cu `pack_for_llm`) sau o listă de replici cu `"line"`. Data: `--date`,
altfel câmpul `"meeting_date"` din JSON, altfel azi. Rezultatele: lângă `AI/jobs/<id>/llm_input.json`
pentru un job real, altfel în `LLM/runs/<nume_fișier>/` (ignorat de git).

În terminal:

```
PUNCTELE PRINCIPALE
Pacientul 3 din salonul 2 are programat control CT pe 5 octombrie [00:06]. ...

TERMENE
  C1 Pacientul 3, salon 2
     când: „5 octombrie” (dată fixă)  [00:06]
     ce:   Programarea controlului cu CT pe 5 octombrie
  C4 Pacientul 14
     când: „Dacă troponina a doua” (condiționat, dacă rezultatul a doua troponină)  [00:38]
  ...
CAZURI ȘI DECIZII
  C1 Pacientul 3, salon 2: Evaluare post-colecistectomie
     ✓ aprobat: Programarea controlului cu CT pe 5 octombrie  [00:06]
DE VERIFICAT: 3 corecții în cod, 0 citate negăsite, 0 avertismente, 0 erori
```

`când` e expresia exactă din transcriere. Data concretă o calculează validarea, nu modelul.

La fiecare rulare, în folderul rezultatelor:

| Fișier | Pentru cine |
|---|---|
| `mom.html` / `mom.md` | **procesul-verbal (MoM) pentru revizuire**, generat din `minutes.json` de `LLM/mom.py` (Python, fără model, deci nu adaugă nimic): rezumat, ordinea de zi, discuții și decizii pe puncte, sinteza deciziilor și termenelor, întrebări deschise, note pentru revizuire. Textul e în română; citatele exacte apar doar ca adnotări „spus în înregistrare”. |
| `review.md` | raportul tehnic: fiecare decizie lângă replica citată, corecțiile din cod, transcrierea marcată, listă de verificare |
| `minutes.json`, `llm_debug/` | ieșirea brută și tot ce s-a trimis/primit de la model |

`python -m LLM.mom AI\jobs\<job_id>` regenerează MoM-ul pentru orice job cu `minutes.json`.

Experimente (fiecare variantă în folderul ei, ca să le poți compara):

```powershell
.\.venv\Scripts\python.exe -m LLM.manual sedinta.json --window-tokens 500      # ferestre mai mici -> LLM\runs\sedinta-w500\
$env:LLM_THINK_EXTRACT = "1"; .\.venv\Scripts\python.exe -m LLM.manual sedinta.json --tag think; Remove-Item Env:LLM_THINK_EXTRACT
```

Ședință scrisă ca text, în loc de JSON:

```powershell
.\.venv\Scripts\python.exe -m LLM.manual new consiliu_cardio     # creează LLM/manual_tests/consiliu_cardio/meeting.txt
.\.venv\Scripts\python.exe -m LLM.manual consiliu_cardio         # -> LLM/runs/consiliu_cardio/
```

`meeting.txt` are o replică pe linie, în formatul ferestrelor ASR: `[mm:ss] SPEAKER_01: text [?]`.
Timpul și vorbitorul sunt opționale, `[?]` = încredere scăzută, iar o linie goală = pauză (subiect nou).
Setările `# date:`, `# window_tokens:` (mic, ex. 300, pentru mai multe ferestre) și `# overlap:` stau
în antet. Poți lipi direct linii din `windows[i].text` ale unui job real. `llm_input.json` se
construiește cu `make_turns` / `make_windows` din `pipeline/pack_for_llm.py`, deci are formatul real.

`review.md` conține: rezumatul ședinței, tabelul subiectelor principale, tabelul termenelor, fiecare caz cu deciziile
lângă replica citată (⚠ replică nesigură, ⛔ citat negăsit, deciziile înlocuite tăiate), corecțiile
făcute în cod, transcrierea cu liniile citate marcate (`▶ C1`) și o listă de verificare de bifat.
`meeting.txt` se păstrează în git (`LLM/manual_tests/`); ieșirile din `LLM/runs/` nu.

Fixture-urile din `LLM/tests/fixtures/` sunt generate cu `make_turns` / `make_windows` din
`pipeline/pack_for_llm.py`, deci au exact formatul real. `llm_input.example.json` reproduce exemplul
din documentația ASR.
