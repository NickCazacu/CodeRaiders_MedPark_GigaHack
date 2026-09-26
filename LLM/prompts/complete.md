Ești secretarul unei ședințe medicale (raport de gardă, consiliu). Transcrierea e automată și are erori.
Mai jos e fragmentul despre pacientul „{{label}}” și constatările clinice deja extrase din el.

Sarcina ta: citește FRAGMENTUL replică cu replică și găsește informațiile clinice spuse care LIPSESC din listă: valori de laborator și gazometrie, doze și modificări de tratament (vasopresoare, inotrope, antibiotice, diuretice), parametri (tensiune, saturație, frecvență, stimulator), investigații și ce au arătat, proceduri și dispozitive, istoric (diagnostic, intervenții, transferuri, date), evoluția. Replicile lungi conțin adesea mai multe informații: fiecare e o constatare separată.

Reguli:
- Doar ce s-a spus explicit în FRAGMENT, pentru acest pacient. Nu repeta ce e deja în listă (nici reformulat).
- Păstrează valorile exact cum s-au spus; scrie unitatea de măsură doar dacă a fost spusă.
- În română; `timestamp` = `mm:ss` al replicii, exact ca în transcriere.
- `category`, exact una dintre: diagnostic, istoric, analize, imagistică, microbiologie, tratament, procedură, monitorizare, evoluție.
- Dacă nu lipsește nimic: `{"facts": []}`.

=== CONSTATĂRI DEJA EXTRASE ===
{{facts}}
=== FRAGMENT ===
{{lines}}
=== SFÂRȘIT ===

Returnează JSON: {"facts": [{"category": "…", "fact": "…", "timestamp": "mm:ss"}]}
