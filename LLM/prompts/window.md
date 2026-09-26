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

Lista de cazuri cunoscute NU e completă: punctele noi (pacienți sau alte subiecte) care apar în FEREASTRA CURENTĂ trebuie extrase și ele. Nu adăuga un caz cunoscut dacă nu e discutat în FEREASTRA CURENTĂ.

Returnează JSON:
- "cases": toate punctele discutate în FEREASTRA CURENTĂ (pacienți sau alte subiecte), noi sau cunoscute (pentru un caz cunoscut folosește același case_key), fiecare cu "facts" esențiale din FEREASTRA CURENTĂ (3–6: la un pacient diagnosticul, valorile-cheie, tratamentul; la un subiect problema, cifrele, termenele) și "decisions";
- "summary": 2–3 propoziții în română despre fereastra curentă, cu [mm:ss] după fiecare afirmație.
