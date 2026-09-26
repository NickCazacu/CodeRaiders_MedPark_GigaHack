# Transcrieri de referință

Fișierele `*.txt` de aici sunt transcrieri făcute manual pe înregistrări reale. Sunt date medicale, deci **nu intră în git**
(vezi `.gitignore`). Doar acest README e urmărit.

Format:
```
# fragment 0:00-1:32
[Speaker1] 0:00 - 0:15: text exact, cum se aude
[Speaker2] 0:15 - 0:17: ...
```
- `# fragment start-end`: fereastra de timp evaluată. Transcrieți tot ce se aude în ea.
- Textul dintre `[ ]` din interiorul unei replici (de exemplu `[aici nu se intlege]`) e ignorat.
- Diacriticele, majusculele și punctuația nu contează la evaluare.

Evaluare: `python -m tests.evaluate tests/reference/medpark.txt JOB1 JOB2 ...`
