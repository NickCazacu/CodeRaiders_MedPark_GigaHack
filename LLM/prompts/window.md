Data ședinței: {{date}}
Fereastra {{window_number}} din {{n_windows}} a transcrierii.

Cazuri cunoscute din ferestrele anterioare (case_key — stare):
{{known_cases}}

Rezumatul ferestrelor anterioare:
{{previous_summary}}

=== CONTEXT din fereastra anterioară: deja procesat, NU extrage decizii din aceste linii ===
{{context_lines}}
=== FEREASTRA CURENTĂ: extrage de aici ===
{{window_lines}}
=== SFÂRȘIT ===

Lista de cazuri cunoscute NU e completă: pacienții noi care apar în FEREASTRA CURENTĂ trebuie extrași și ei. Nu adăuga un caz cunoscut dacă nu e discutat în FEREASTRA CURENTĂ.

Returnează JSON:
- "cases": toți pacienții discutați în FEREASTRA CURENTĂ, noi sau cunoscuți (pentru un caz cunoscut folosește același case_key), fiecare cu "facts" din FEREASTRA CURENTĂ (analize cu valori, tratament cu doze, proceduri, investigații, evoluție) și "decisions";
- "summary": 2–3 propoziții în română despre fereastra curentă, cu [mm:ss] după fiecare afirmație.
