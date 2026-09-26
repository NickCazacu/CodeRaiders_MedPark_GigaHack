"""Toate testele offline (fără Ollama):  python -m LLM.tests
Scenariile cu modelul real:            python -m LLM.tests.test_scenarios
"""
import sys

from LLM.tests import test_checks, test_extract, test_loader, test_merge, test_quotes
from LLM.tests.helpers import run

failed = 0
for mod in (test_loader, test_quotes, test_merge, test_checks, test_extract):
    print(f"\n━━ {mod.__name__} ━━")
    failed += run(vars(mod))
print(f"\n{'TOTUL OK' if not failed else f'{failed} teste picate'}")
sys.exit(1 if failed else 0)
