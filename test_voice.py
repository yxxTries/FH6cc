"""Checks the voice stack end-to-end (model load + grammar + mic present).

Run with --listen to do a 15-second live mic test: speak commands and see
what the recognizer hears, without touching the game.
"""
import json
import os
import sys

import config
from asr import _GRAMMAR, _resolve_model_dir

from vosk import Model, KaldiRecognizer, SetLogLevel

SetLogLevel(-1)
model_dir = _resolve_model_dir()
assert model_dir, "no Vosk model found — run download_model.ps1"
print("using model: %s" % os.path.basename(model_dir))
rec = KaldiRecognizer(Model(model_dir), config.MIC_SAMPLE_RATE, _GRAMMAR)
print("model + grammar OK (%d words in grammar)" % len(json.loads(_GRAMMAR)))

import sounddevice as sd
default_in = sd.query_devices(kind="input")
print("default microphone: %s" % default_in["name"])

if "--listen" in sys.argv:
    import queue
    q = queue.Queue()
    blocksize = getattr(config, "MIC_BLOCKSIZE", 1000)
    with sd.RawInputStream(samplerate=config.MIC_SAMPLE_RATE, blocksize=blocksize,
                           dtype="int16", channels=1,
                           callback=lambda d, f, t, s: q.put(bytes(d))):
        print("listening for 15s — try: 'on', 'set eighty', 'faster', "
              "'slower', 'off'  (partial hits shown live)")
        import time
        end = time.monotonic() + 15
        last_partial = ""
        while time.monotonic() < end:
            data = q.get()
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result()).get("text", "")
                if text:
                    print("  final:   %r" % text)
                last_partial = ""
            else:
                p = json.loads(rec.PartialResult()).get("partial", "")
                if p and p != last_partial:
                    print("  partial: %r" % p)
                    last_partial = p
    print("done.")
