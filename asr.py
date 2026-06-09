"""Offline voice control — a Vosk + faster-whisper hybrid, tuned for both
low latency *and* reliable number recognition.

Recognized commands:
  "on" / "cruise" / "engage" / "go"   -> engage at current speed
  "off" / "cancel" / "stop"           -> disengage
  "resume" / "back"                   -> re-engage at the last set speed
  "faster" / "up" / "more"            -> +STEP_KMH
  "slower" / "down" / "less"          -> -STEP_KMH
  "set <number>"  (e.g. "set 100")    -> target that speed

How the hybrid works:
  * Vosk runs continuously and fires the single-word commands from *partial*
    results — the instant the word is heard, no end-of-utterance wait. This
    is what makes on/off/faster feel instant.
  * The moment Vosk hears "set", we record a short window and hand it to
    faster-whisper, which is far better at digits than a small Vosk model
    ("three hundred" vs "one hundred"). The number is parsed from Whisper's
    transcription (it returns '100' or '1 0 0', both handled).
  * If faster-whisper isn't installed, "set <number>" gracefully falls back
    to Vosk's own spoken-number parsing.

Everything is optional: missing vosk / sounddevice / models just disables
voice; missing faster-whisper just disables the better number path.
"""

import collections
import json
import os
import queue
import threading
import time

import config

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

# Each command maps to its trigger words. Short, distinct keywords with a few
# natural synonyms — easier to say and faster to recognize than phrases.
ENGAGE = "engage"
CANCEL = "cancel"
RESUME = "resume"
FASTER = "faster"
SLOWER = "slower"

_KEYWORDS = {
    "on": ENGAGE, "cruise": ENGAGE, "engage": ENGAGE, "go": ENGAGE,
    "off": CANCEL, "cancel": CANCEL, "stop": CANCEL,
    "resume": RESUME, "back": RESUME,
    "faster": FASTER, "up": FASTER, "more": FASTER, "increase": FASTER,
    "slower": SLOWER, "down": SLOWER, "less": SLOWER,
    "reduce": SLOWER, "decrease": SLOWER,
}

# Words that begin a "set <number>" phrase — handled on the final result only,
# since they need the trailing number words to be complete.
_SET_WORDS = {"set", "speed", "target"}

# Build the recognizer grammar from everything we can act on. No "[unk]":
# constraining hard to the vocabulary makes the decoder commit to a known
# word instead of hedging, which is both faster and more accurate here.
_GRAMMAR = json.dumps(
    sorted(set(_KEYWORDS) | _SET_WORDS | set(_UNITS) | set(_TENS) | {"hundred"})
)


def _resolve_model_dir():
    """First model directory that exists on disk, or None.

    Prefers config.VOSK_MODEL_DIRS (better-number model first), falling back
    to the legacy single VOSK_MODEL_DIR for older configs.
    """
    here = os.path.dirname(__file__)
    candidates = list(getattr(config, "VOSK_MODEL_DIRS", []) or [])
    legacy = getattr(config, "VOSK_MODEL_DIR", None)
    if legacy and legacy not in candidates:
        candidates.append(legacy)
    for rel in candidates:
        path = os.path.join(here, rel)
        if os.path.isdir(path):
            return path
    return None


def _digit_sequence(words):
    """Digit-by-digit speech -> number. ['one','zero','zero'] -> 100.

    Single digit words are acoustically distinct, so the model nails them
    far more reliably than 'one hundred' (which it often hears as 'three
    hundred'). Only kicks in when *every* number word is a 0-9 unit, so it
    doesn't fight the word-number path for 'eighty' etc.
    """
    digits = [_UNITS[w] for w in words if w in _UNITS]
    nums = [w for w in words if w in _UNITS or w in _TENS or w == "hundred"]
    if not digits or len(digits) != len(nums) or len(digits) < 2:
        return None
    val = 0
    for d in digits:
        val = val * 10 + d
    return val


def words_to_number(words):
    """Spoken number -> int. Returns None if not a number.

    Two forms, tried in this order:
      digit-by-digit : 'one zero zero'         -> 100   (most reliable)
      word-number    : 'one hundred twenty'    -> 120
    """
    seq = _digit_sequence(words)
    if seq is not None:
        return seq

    current, found = 0, False
    for w in words:
        if w in _UNITS:
            current += _UNITS[w]
            found = True
        elif w in _TENS:
            current += _TENS[w]
            found = True
        elif w == "hundred":
            current = (current or 1) * 100
            found = True
        elif found:
            break  # number already captured, stop at first non-number word
    return current if found and current > 0 else None


def parse_number_text(text):
    """Pull a speed out of free-form transcription (e.g. Whisper output).

    Whisper returns digits, not words. It may write a multi-digit number
    solid ('100'), or split a spoken digit sequence into separate tokens
    ('1 0 0', '8-0', '9 5'). Join any run of single digits into one number;
    otherwise take the first multi-digit run. Falls back to the spoken-word
    parser if no digits appear at all.
    """
    if not text:
        return None
    import re
    # Tokens of digits, in order. '8-0' -> ['8','0']; '120' -> ['120'].
    tokens = re.findall(r"\d+", text)
    if tokens:
        # If everything is a single digit, it's a spoken sequence: join it.
        if all(len(t) == 1 for t in tokens) and len(tokens) > 1:
            joined = int("".join(tokens))
            if joined > 0:
                return joined
        # Otherwise the first run that already reads as a full number.
        for t in tokens:
            if int(t) > 0:
                return int(t)
    cleaned = re.sub(r"[^a-z ]", " ", text.lower())
    return words_to_number(cleaned.split())


class WhisperNumber:
    """Lazy faster-whisper wrapper that turns a PCM buffer into a speed int.

    Loaded on first use so app start isn't blocked by the model load, and so
    a missing faster-whisper install simply disables the feature.
    """

    def __init__(self, notify=print):
        self._notify = notify
        self._model = None
        self._ok = None  # None=untried, True/False=available

    @property
    def available(self):
        if self._ok is None:
            self._load()
        return self._ok

    def _load(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            self._notify("[voice] whisper numbers off (pip install faster-whisper)")
            self._ok = False
            return
        try:
            t0 = time.monotonic()
            self._model = WhisperModel(
                getattr(config, "WHISPER_MODEL", "base.en"),
                device=getattr(config, "WHISPER_DEVICE", "cpu"),
                compute_type=getattr(config, "WHISPER_COMPUTE", "int8"),
            )
            self._ok = True
            self._notify("[voice] whisper numbers ready (%s, %.1fs)" %
                         (getattr(config, "WHISPER_MODEL", "base.en"),
                          time.monotonic() - t0))
        except Exception as e:
            self._notify("[voice] whisper load failed: %s" % e)
            self._ok = False

    def number_from_pcm(self, pcm_bytes, sample_rate):
        """int16 mono PCM -> spoken number, or None."""
        if not self.available:
            return None
        import numpy as np
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return None
        # The clip is "set <number>"; prompt with that shape so Whisper writes
        # the speed as digits. No VAD trim here — the window is already tight,
        # and trimming risks clipping a quickly-spoken number.
        segments, _ = self._model.transcribe(
            audio, language="en", beam_size=1,
            initial_prompt="set the speed to", vad_filter=False)
        text = " ".join(s.text for s in segments).strip()
        num = parse_number_text(text)
        if num is None:
            self._notify("[voice] whisper heard %r (no number)" % text)
        return num


class VoiceControl(threading.Thread):
    def __init__(self, cruise, get_speed, notify=print):
        super().__init__(name="voice", daemon=True)
        self._cruise = cruise
        self._get_speed = get_speed
        self._notify = notify
        self._stop = threading.Event()
        # Runtime mute toggle (HUD "voice on/off"): when cleared the audio
        # stream keeps running but recognized commands are ignored, so
        # toggling is instant and doesn't reload the model.
        self._enabled = threading.Event()
        self._enabled.set()
        self._audio = queue.Queue()
        # Debounce: remember the last command + when it fired so a partial
        # hit isn't immediately repeated by the final result.
        self._last_cmd = None
        self._last_cmd_t = 0.0
        # Hybrid number recognition via faster-whisper (lazy-loaded).
        self._whisper = (WhisperNumber(self._notify)
                         if getattr(config, "WHISPER_NUMBERS", False) else None)
        # Rolling pre-roll buffer: always holds the most recent few seconds of
        # raw audio so "set <number>" said in one breath is captured even
        # though the trigger only fires partway through.
        self._preroll = collections.deque()
        self._preroll_bytes = 0

    def stop(self):
        self._stop.set()

    @property
    def enabled(self):
        return self._enabled.is_set()

    def set_enabled(self, on):
        if on:
            self._enabled.set()
        else:
            self._enabled.clear()
        self._notify("[voice] %s" % ("listening" if on else "muted"))

    def run(self):
        try:
            import sounddevice as sd
            from vosk import Model, KaldiRecognizer, SetLogLevel
        except ImportError as e:
            self._notify("[voice] disabled (missing package: %s)" % e.name)
            return

        model_dir = _resolve_model_dir()
        if model_dir is None:
            self._notify("[voice] disabled (no Vosk model found — "
                         "run download_model.ps1)")
            return
        self._notify("[voice] model: %s" % os.path.basename(model_dir))

        SetLogLevel(-1)
        rec = KaldiRecognizer(Model(model_dir), config.MIC_SAMPLE_RATE, _GRAMMAR)
        rec.SetWords(False)
        use_partial = getattr(config, "VOICE_PARTIAL_HOTWORDS", True)
        blocksize = getattr(config, "MIC_BLOCKSIZE", 1000)
        # Warm up Whisper now (off the audio path) so the first "set" is fast.
        if self._whisper is not None:
            threading.Thread(target=lambda: self._whisper.available,
                             name="whisper-warmup", daemon=True).start()

        def callback(indata, frames, t, status):
            self._audio.put(bytes(indata))

        try:
            with sd.RawInputStream(samplerate=config.MIC_SAMPLE_RATE,
                                   blocksize=blocksize, dtype="int16",
                                   channels=1, callback=callback):
                self._notify("[voice] listening (say: on / off / faster / "
                             "slower / resume / set <number>)")
                while not self._stop.is_set():
                    try:
                        data = self._audio.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    # Muted: keep draining the stream and the pre-roll buffer
                    # so resuming is seamless, but recognize nothing.
                    if not self._enabled.is_set():
                        self._push_preroll(data)
                        rec.Reset()
                        continue
                    self._push_preroll(data)
                    if rec.AcceptWaveform(data):
                        # End of utterance: full result. Handles everything,
                        # including the Vosk fallback for "set <number>".
                        text = json.loads(rec.Result()).get("text", "")
                        if text:
                            self._handle(text, final=True)
                    elif use_partial:
                        text = json.loads(rec.PartialResult()).get("partial", "")
                        if not text:
                            continue
                        # Hybrid: the instant Vosk hears "set", transcribe the
                        # number with Whisper. The pre-roll buffer already holds
                        # the audio of "set <number>", so no pause is needed.
                        if (self._whisper is not None
                                and (_SET_WORDS & set(text.split()))
                                and self._whisper.available):
                            self._whisper_set_number(rec)
                            continue
                        # Otherwise act on decisive single-word commands now.
                        self._handle(text, final=False)
        except Exception as e:
            self._notify("[voice] disabled (mic error: %s)" % e)

    # ---- rolling pre-roll buffer ----------------------------------------

    def _push_preroll(self, data):
        """Append a chunk and trim to WHISPER_PREROLL_S of history."""
        sr = config.MIC_SAMPLE_RATE
        cap = int(sr * getattr(config, "WHISPER_PREROLL_S", 1.2)) * 2  # bytes
        self._preroll.append(data)
        self._preroll_bytes += len(data)
        while self._preroll_bytes > cap and len(self._preroll) > 1:
            self._preroll_bytes -= len(self._preroll.popleft())

    @staticmethod
    def _rms(data):
        import numpy as np
        a = np.frombuffer(data, dtype=np.int16)
        if a.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(a.astype(np.float32) ** 2)))

    def _whisper_set_number(self, rec):
        """Transcribe "set <number>" using pre-roll + a short post-roll.

        The pre-roll already contains the audio up to and including the number
        if it was spoken in one breath. We then collect a little trailing
        audio, stopping early once it goes quiet, and transcribe the whole
        window. Vosk is reset so the captured words don't re-trigger anything.
        """
        sr = config.MIC_SAMPLE_RATE
        buf = bytearray(b"".join(self._preroll))  # everything heard so far

        # Post-roll: keep collecting until max duration or a run of silence.
        post_cap = int(sr * getattr(config, "WHISPER_POSTROLL_S", 1.0)) * 2
        sil_need = getattr(config, "WHISPER_SILENCE_S", 0.4)
        sil_rms = getattr(config, "WHISPER_SILENCE_RMS", 350)
        collected, quiet_for = 0, 0.0
        deadline = time.monotonic() + getattr(config, "WHISPER_POSTROLL_S", 1.0) + 0.5
        while (collected < post_cap and time.monotonic() < deadline
               and not self._stop.is_set()):
            try:
                chunk = self._audio.get(timeout=0.3)
            except queue.Empty:
                break
            self._push_preroll(chunk)
            buf.extend(chunk)
            collected += len(chunk)
            secs = len(chunk) / 2 / sr
            quiet_for = quiet_for + secs if self._rms(chunk) < sil_rms else 0.0
            if quiet_for >= sil_need:
                break  # speaker finished the number

        rec.Reset()                     # drop Vosk's partial for these words
        self._preroll.clear()           # used; start fresh
        self._preroll_bytes = 0
        num = self._whisper.number_from_pcm(bytes(buf), sr)
        if num is not None:
            speed = self._get_speed()
            self._notify("[voice] %s" % self._cruise.set_target(num, speed))
        else:
            self._notify("[voice] no number heard with 'set'")
        # Hold debounce briefly so trailing audio can't immediately refire.
        self._last_cmd, self._last_cmd_t = "set", time.monotonic()

    # ---- command dispatch ------------------------------------------------

    def _fire(self, cmd):
        """Run a single-word command, debounced. Returns True if it ran."""
        now = time.monotonic()
        if cmd == self._last_cmd and (now - self._last_cmd_t) < \
                getattr(config, "VOICE_DEBOUNCE_S", 0.6):
            return False
        self._last_cmd, self._last_cmd_t = cmd, now
        speed = self._get_speed()
        if cmd == FASTER:
            t = self._cruise.adjust(config.STEP_KMH)
            self._notify("[voice] faster -> %s" % (t and "%d km/h" % t))
        elif cmd == SLOWER:
            t = self._cruise.adjust(-config.STEP_KMH)
            self._notify("[voice] slower -> %s" % (t and "%d km/h" % t))
        elif cmd == CANCEL:
            self._cruise.disengage()
            self._notify("[voice] cruise off")
        elif cmd == RESUME:
            self._notify("[voice] %s" % self._cruise.resume(speed))
        elif cmd == ENGAGE:
            self._notify("[voice] %s" % self._cruise.engage(speed))
        return True

    def _handle(self, text, final):
        words = text.split()
        if not words:
            return

        # "set <number>" needs the trailing number, so only act on the final
        # result. Do this before keyword scanning so "set ... " isn't eaten by
        # a stray keyword in the number words.
        if final and (_SET_WORDS & set(words)):
            anchor = next((i for i, w in enumerate(words)
                           if w in _SET_WORDS), None)
            num = words_to_number(words[anchor + 1:])
            if num is not None:
                speed = self._get_speed()
                self._notify("[voice] %s" % self._cruise.set_target(num, speed))
                # Block the debounce so a follow-up partial doesn't refire.
                self._last_cmd, self._last_cmd_t = "set", time.monotonic()
                return
            if final:
                self._notify("[voice] heard 'set' but no number: %r" % text)
            return

        # Single-word commands. Scan the *last* keyword in the hypothesis so a
        # correction ("up ... down") lands on what the user said most recently.
        for w in reversed(words):
            cmd = _KEYWORDS.get(w)
            if cmd:
                self._fire(cmd)
                return
