Data ședinței: {{date}}
Fragmentul {{number}} din {{n}}: discuția despre „{{label}}”, de la [{{start}}]. Fragmentele au fost delimitate automat pe pacienți; la margini pot apărea câteva replici despre alt pacient.

Pacienți deja extrași din fragmentele anterioare (dacă e același pacient, folosește EXACT același case_key):
{{known_cases}}

=== CONTEXT dinaintea fragmentului: doar pentru înțelegere, NU extrage din aceste linii ===
{{context_lines}}
=== FRAGMENT: extrage de aici ===
{{window_lines}}
=== SFÂRȘIT ===

Returnează JSON:
- "cases": pacientul „{{label}}”, ca un singur caz (`case_key`: pat/boxă/salon/număr), cu "facts" (TOATE informațiile clinice din FRAGMENT: analize cu valori, tratament cu doze, proceduri, investigații, microbiologie, evoluție) și "decisions". Adaugă alt caz DOAR dacă fragmentul discută clar și alt pacient (ex. mai mulți pacienți trecuți în revistă scurt la final: câte un caz pentru fiecare);
- "summary": 1–3 propoziții în română despre fragment, cu [mm:ss] după fiecare afirmație.
