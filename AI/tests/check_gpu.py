"""Verifică torch + CTranslate2/faster-whisper pe CUDA.  python tests/check_gpu.py"""
import time

import numpy as np
import torch

print("torch", torch.__version__, "| CUDA build", torch.version.cuda, "| cuDNN", torch.backends.cudnn.version())
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0), "| capability", torch.cuda.get_device_capability(0))
    print("arch list:", torch.cuda.get_arch_list())
    x = torch.randn(2048, 2048, device="cuda", dtype=torch.float16)
    print("matmul fp16 pe GPU OK:", float((x @ x).float().mean()) is not None)

import ctranslate2

print("ctranslate2", ctranslate2.__version__, "| cuda devices:", ctranslate2.get_cuda_device_count())
print("compute types cuda:", sorted(ctranslate2.get_supported_compute_types("cuda")))

from faster_whisper import WhisperModel

t0 = time.perf_counter()
model = WhisperModel("tiny", device="cuda", compute_type="float16")
audio = np.zeros(16000 * 5, dtype=np.float32)
segments, info = model.transcribe(audio, beam_size=1)
list(segments)
print(f"faster-whisper tiny pe cuda/float16 OK ({time.perf_counter() - t0:.1f} s)")
