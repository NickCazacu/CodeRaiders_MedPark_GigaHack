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

## Nevalidat încă

- Bonusul ro 0.5 pe **rusă reală**: dacă rusa vorbită iese transcrisă ca română, bonusul trebuie scăzut.
- Orice setare pe alte înregistrări medicale (nu există).
