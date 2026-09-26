# Ședințe de test

Câte un fișier `.json` per ședință. Pentru fiecare, `python -m LLM.manual all` generează procesul-verbal
în `LLM/test_moms/<nume>.md` (același nume ca fișierul de aici).

```powershell
.\.venv\Scripts\python.exe -m LLM.manual all --date 2026-09-26     # toate ședințele
.\.venv\Scripts\python.exe -m LLM.manual consiliu_30min            # doar una (numele fișierului, fără .json)
```

Formatul acceptat:
- `llm_input.json` din pipeline (`AI/jobs/<job_id>/llm_input.json`): obiect cu `turns` și `windows`;
- `transcript.json` (segmentele ASR, cu `text`): ferestrele se construiesc cu `pipeline/pack_for_llm.py`;
- o listă de replici cu `"line"`.

Data ședinței: câmpul `"meeting_date": "YYYY-MM-DD"` din JSON, altfel `--date`, altfel azi.

| Fișier | Ce testează |
|---|---|
| `consiliu_30min.json` | consiliu realist de 30 min, 148 replici, RO/RU/EN: 8 pacienți, puncte administrative, o decizie schimbată la final (patul 8), vârste care seamănă cu numere de pacient, nume de medici |
| `exemplu_asr_2_ferestre.json` | exemplul din documentația ASR (2 ferestre cu suprapunere) |
| `scurt_doua_cazuri.json` | ședință scurtă, 2 cazuri clare, o singură fereastră |
| `raport_garda_fara_decizii.json` | raport de gardă fără nicio decizie (rezultat gol corect) |
| `tipuri_de_termene.json` | câte un tip de termen: dată fixă, relativ, durată, condiționat, vag, periodic, fără termen |
| `decizie_pe_replica_nesigura.json` | decizie pe o replică `[?]` (încredere scăzută) |
| `sedinta_lunga_6_ferestre.json` | 6 ferestre: caz discutat devreme și decis târziu, decizie schimbată, decizie în suprapunere |
