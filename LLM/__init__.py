"""Extracția procesului-verbal (MoM) din transcrierea ședinței; vezi LLM/README.md.

Codul echipei ASR (pachetul `pipeline`) stă în AI/. Îl punem pe sys.path ca să-l importăm
(pack_for_llm, common) fără să-l copiem; LLM/ nu modifică nimic din AI/.
"""
import sys
from pathlib import Path

_AI = Path(__file__).resolve().parent.parent / "AI"
if str(_AI) not in sys.path:
    sys.path.append(str(_AI))
