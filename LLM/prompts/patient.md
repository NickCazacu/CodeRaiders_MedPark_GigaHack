Data ședinței: {{date}}
Fragmentul {{number}} din {{n}}: discuția despre „{{label}}”, de la [{{start}}]. Fragmentele au fost delimitate automat pe puncte (pacienți sau alte subiecte); la margini pot apărea câteva replici despre alt punct.

Puncte deja extrase din fragmentele anterioare (dacă e același punct, folosește EXACT același case_key):
{{known_cases}}

=== CONTEXT dinaintea fragmentului: doar pentru înțelegere, NU extrage din aceste linii ===
{{context_lines}}
=== FRAGMENT: extrage de aici ===
{{window_lines}}
=== SFÂRȘIT ===

Returnează JSON:
- "cases": punctul „{{label}}”, ca un singur caz, cu "facts" (informația esențială din FRAGMENT, 3–6 elemente: la un pacient diagnosticul, valorile-cheie, tratamentul și procedurile importante; la un subiect problema, cifrele, termenele, responsabilii, cu categoria „informație”) și "decisions". `case_key`: la un pacient pat/boxă/salon/număr; la un subiect tema („Organizare: …”, „Echipamente: …”). Dacă fragmentul nu e despre un pacient anume, NU crea un pacient. Adaugă alt caz DOAR dacă fragmentul discută clar și alt punct (ex. mai mulți pacienți trecuți în revistă scurt la final: câte un caz pentru fiecare);
- "summary": 1–3 propoziții în română despre fragment, cu [mm:ss] după fiecare afirmație.
