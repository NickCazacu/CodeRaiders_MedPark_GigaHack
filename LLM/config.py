"""Config: LLM/config.yaml + suprascrieri din variabile de mediu."""
import copy
import os
from pathlib import Path
from urllib.parse import urlparse

import yaml

LLM_DIR = Path(__file__).resolve().parent
REPO = LLM_DIR.parent
ROOT = REPO / "AI"  # pipeline-ul ASR: jobs/, config.yaml, pachetul pipeline
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# variabilă de mediu -> (secțiune, cheie, tip)
ENV = {
    "LLM_OLLAMA_URL": ("ollama", "url", str),
    "LLM_MODEL": ("ollama", "model", str),
    "LLM_TIMEOUT_S": ("ollama", "timeout_s", float),
    "LLM_RETRIES": ("ollama", "retries", int),
    "LLM_NUM_CTX": ("ollama", "num_ctx", int),
    "LLM_TEMPERATURE": ("ollama", "temperature", float),
    "LLM_KEEP_ALIVE": ("ollama", "keep_alive", str),
    "LLM_TOKEN_FACTOR": ("context", "token_factor", float),
    "LLM_MAX_NUM_CTX": ("context", "max_num_ctx", int),
    "LLM_ON_OVERFLOW": ("context", "on_overflow", str),
    "LLM_UNCERTAIN_RATIO": ("input", "uncertain_ratio", float),
    "LLM_SUPERSEDE": ("merge", "supersede", str),
}
# LLM_THINK_EXTRACT / LLM_THINK_SAME_CASE / LLM_THINK_FINAL = 0/1
BOOL = {"1": True, "true": True, "yes": True, "0": False, "false": False, "no": False}


def load_config(path=None, env=None):
    env = os.environ if env is None else env
    path = Path(path) if path else LLM_DIR / "config.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = copy.deepcopy(yaml.safe_load(f))
    for var, (sec, key, typ) in ENV.items():
        if env.get(var):
            cfg[sec][key] = typ(env[var])
    for stage in cfg["stages"]:
        v = env.get(f"LLM_THINK_{stage.upper()}")
        if v:
            cfg["stages"][stage]["think"] = BOOL[v.strip().lower()]
    check_local(cfg["ollama"]["url"])
    return cfg


def check_local(url):
    # constrângere fermă: niciun apel de rețea în afara serverului Ollama local
    host = urlparse(url).hostname
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"LLM_OLLAMA_URL trebuie să fie local (localhost/127.0.0.1), nu {url}")
