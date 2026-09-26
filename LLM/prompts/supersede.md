Deciziile luate pentru același caz („{{case_key}}”) într-o ședință medicală, în ordine cronologică:

{{decisions}}

Care decizii au fost schimbate, anulate sau înlocuite de o decizie ULTERIOARĂ?
- O decizie e înlocuită doar dacă o decizie ulterioară privește ACEEAȘI acțiune și o schimbă sau o anulează. Exemple: „hemodializă mâine” apoi „anulăm hemodializa”; „operăm luni” apoi „operația se mută joi”; „ceftriaxonă” apoi „schimbăm antibioticul pe meropenem”.
- Decizii despre acțiuni diferite (ex.: „CT azi” și „oprim anticoagulantul”) NU se înlocuiesc una pe alta.
- O decizie ulterioară care doar confirmă, repetă sau detaliază una anterioară NU o înlocuiește.
- Ultima decizie ({{last_index}}) nu poate fi înlocuită.

Răspunde JSON: {"superseded": [indicii deciziilor înlocuite]}; listă goală dacă niciuna.
