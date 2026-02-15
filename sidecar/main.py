import os
import sys
import json
import argparse
import inspect
import warnings
import unicodedata
import shutil

# Suppress typeguard instrumentation warnings
warnings.filterwarnings("ignore", message="instrumentor did not find the target function")
warnings.filterwarnings(
    "ignore",
    message="Environment variable TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD detected.*",
)

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"

warnings.filterwarnings(
    "ignore",
    message="The attention mask is not set and cannot be inferred.*",
)
warnings.filterwarnings(
    "ignore",
    message="`huggingface_hub` cache-system uses symlinks by default.*",
)
warnings.filterwarnings(
    "ignore",
    message="stft with return_complex=False is deprecated.*",
)


def configure_stdio_utf8():
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_stdio_utf8()

def configure_windows_dll_paths():
    if sys.platform != "win32":
        return

    path_candidates = []

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        path_candidates.append(exe_dir)
    else:
        # Dev mode: python sidecar from virtual environment
        venv_site = os.path.join(sys.prefix, "Lib", "site-packages")
        path_candidates.append(os.path.join(venv_site, "torch", "lib"))
        path_candidates.append(os.path.join(venv_site, "nvidia", "cublas", "bin"))

    for p in path_candidates:
        if not p or not os.path.exists(p):
            continue
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(p)
            except OSError:
                pass
        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")


configure_windows_dll_paths()

# Handle DLL loading for PyInstaller onefile mode with external DLLs
if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    # Add exe directory to DLL search path for Windows (Python 3.8+)
    if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(exe_dir)
    # Also add to PATH just in case
    os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")

# Monkeypatch inspect to avoid OSError
# This fixes issues with typeguard/inflect when bundled with PyInstaller
def apply_inspect_patch():
    original_getsource = inspect.getsource
    def patched_getsource(obj):
        try:
            return original_getsource(obj)
        except Exception:
            return ""
    inspect.getsource = patched_getsource
    
    original_getsourcelines = inspect.getsourcelines
    def patched_getsourcelines(obj):
        try:
            return original_getsourcelines(obj)
        except Exception:
            return ([], 0)
    inspect.getsourcelines = patched_getsourcelines

apply_inspect_patch()

from chatterbox.mtl_tts import ChatterboxMultilingualTTS
from faster_whisper import WhisperModel
from vieneu import Vieneu
import torch
import torchaudio

import time
from pydub import AudioSegment
import re

try:
    from vinorm import TTSnorm
except Exception:
    TTSnorm = None

try:
    from underthesea import sent_tokenize
except Exception:
    sent_tokenize = None

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# Configure pydub to find ffmpeg if bundled in the same folder
def configure_ffmpeg():
    base = get_base_path()
    ffmpeg_ext = ".exe" if sys.platform == "win32" else ""
    ffmpeg_path = os.path.join(base, f"ffmpeg{ffmpeg_ext}")
    ffprobe_path = os.path.join(base, f"ffprobe{ffmpeg_ext}")
    
    if os.path.exists(ffmpeg_path):
        AudioSegment.converter = ffmpeg_path
    if os.path.exists(ffprobe_path):
        AudioSegment.ffprobe = ffprobe_path

configure_ffmpeg()


def resolve_espeak_executable() -> str | None:
    # Prefer PATH lookup first.
    found = shutil.which("espeak-ng") or shutil.which("espeak")
    if found:
        return found

    # Common Windows install locations.
    candidates = [
        r"C:\Program Files\eSpeak NG\espeak-ng.exe",
        r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def ensure_espeak_available():
    exe = resolve_espeak_executable()
    if not exe:
        raise RuntimeError(
            "VieNeu-TTS requires eSpeak NG for phonemization. Install with: "
            "winget install --id eSpeak-NG.eSpeak-NG --exact --accept-source-agreements --accept-package-agreements"
        )

    exe_dir = os.path.dirname(exe)
    if exe_dir and exe_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")

def format_timestamp(seconds: float):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

def generate_srt(segments):
    srt_content = ""
    for i, segment in enumerate(segments, 1):
        srt_content += f"{i}\n"
        srt_content += f"{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}\n"
        srt_content += f"{segment.text.strip()}\n\n"
    return srt_content


def create_whisper_model(device: str, whisper_path: str):
    if device == "cuda":
        try:
            return WhisperModel(
                "base",
                device="cuda",
                compute_type="float16",
                download_root=whisper_path,
            )
        except Exception as e:
            print(f"WARNING: float16 not supported ({e}). Falling back to float32.")

    return WhisperModel(
        "base",
        device=device,
        compute_type="float32",
        download_root=whisper_path,
    )


def transcribe_reference_audio(runtime_cache, device: str, whisper_path: str, speaker_wav: str) -> str:
    whisper_model = runtime_cache.get("whisper_model")
    whisper_device = runtime_cache.get("whisper_device")
    if whisper_model is None or whisper_device != device:
        runtime_cache["whisper_model"] = create_whisper_model(device, whisper_path)
        runtime_cache["whisper_device"] = device

    segments, _ = runtime_cache["whisper_model"].transcribe(
        speaker_wav,
        beam_size=3,
        language="vi",
        task="transcribe",
    )
    ref_text = " ".join(seg.text.strip() for seg in segments if getattr(seg, "text", "").strip()).strip()
    if not ref_text:
        raise ValueError("Cannot derive Vietnamese reference text from speaker_wav for VieNeu-TTS.")
    return ref_text


def load_chatterbox_model(device: str):
    return ChatterboxMultilingualTTS.from_pretrained(device=torch.device(device))


def load_vieneu_model(device: str):
    ensure_espeak_available()
    # Prefer the PyTorch backbone for quality/stability and GPU support.
    return Vieneu(
        backbone_repo="pnnbao-ump/VieNeu-TTS-0.3B",
        backbone_device=device,
        codec_repo="neuphonic/distill-neucodec",
        codec_device=device,
    )


def normalize_language_code(value: str, default: str = "vi") -> str:
    if not value:
        return default
    lang = str(value).strip().lower().replace("_", "-")
    alias_map = {
        "vietnamese": "vi",
        "vi-vn": "vi",
        "eng": "en",
        "english": "en",
        "zh": "zh-cn",
        "zh-cn": "zh-cn",
        "chinese": "zh-cn",
    }
    return alias_map.get(lang, lang.split("-")[0])


def normalize_whisper_language(value: str):
    lang = normalize_language_code(value, default="vi")
    # Faster-Whisper expects short codes for some languages.
    if lang == "zh-cn":
        return "zh"
    return lang


def detect_usable_cuda():
    if not torch.cuda.is_available():
        return False, "CUDA is not available."

    try:
        props = torch.cuda.get_device_properties(0)
        # PyTorch cu128 binaries in this setup require >= sm_70.
        if props.major < 7:
            return (
                False,
                f"GPU {props.name} (sm_{props.major}{props.minor}) is not supported by current PyTorch CUDA build.",
            )
        _ = torch.zeros(1, device="cuda")
        return True, f"{props.name} (sm_{props.major}{props.minor})"
    except Exception as e:
        return False, f"CUDA initialization failed: {e}"


def normalize_vietnamese_text(text: str) -> str:
    cleaned = unicodedata.normalize("NFC", text)
    if TTSnorm is not None:
        try:
            cleaned = TTSnorm(cleaned, unknown=False, lower=False, rule=True)
        except Exception:
            pass

    cleaned = (
        cleaned.replace("..", ".")
        .replace("!.", "!")
        .replace("?.", "?")
        .replace(" .", ".")
        .replace(" ,", ",")
        .replace('"', "")
        .replace("'", "")
    )
    # Read standalone AI acronym as Vietnamese phonetics.
    cleaned = re.sub(r"\bA\.?I\b", "\u00E2y ai", cleaned, flags=re.IGNORECASE)
    cleaned = normalize_vietnamese_numbers(cleaned)
    return cleaned


def preview_text_for_log(text: str, limit: int = 240) -> str:
    single_line = " ".join(str(text).split())
    if len(single_line) <= limit:
        return single_line
    return single_line[:limit] + "...(truncated)"


VI_DIGITS = [
    "không",
    "một",
    "hai",
    "ba",
    "bốn",
    "năm",
    "sáu",
    "bảy",
    "tám",
    "chín",
]


def _read_two_digits_vi(n: int, full: bool = False) -> str:
    if n < 10:
        if full and n > 0:
            return f"lẻ {VI_DIGITS[n]}"
        return VI_DIGITS[n]

    tens = n // 10
    ones = n % 10

    if tens == 1:
        prefix = "mười"
    else:
        prefix = f"{VI_DIGITS[tens]} mươi"

    if ones == 0:
        return prefix
    if ones == 1 and tens > 1:
        return f"{prefix} mốt"
    if ones == 4 and tens > 1:
        return f"{prefix} tư"
    if ones == 5:
        return f"{prefix} lăm"
    return f"{prefix} {VI_DIGITS[ones]}"


def _read_three_digits_vi(n: int, full: bool = False) -> str:
    hundreds = n // 100
    rest = n % 100

    if hundreds == 0:
        return _read_two_digits_vi(rest, full)

    if rest == 0:
        return f"{VI_DIGITS[hundreds]} trăm"
    return f"{VI_DIGITS[hundreds]} trăm {_read_two_digits_vi(rest, True)}"


def number_to_vietnamese(n: int) -> str:
    if n == 0:
        return VI_DIGITS[0]
    if n < 0:
        return f"âm {number_to_vietnamese(abs(n))}"

    units = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]
    groups = []
    x = n
    while x > 0:
        groups.append(x % 1000)
        x //= 1000

    parts = []
    for i in range(len(groups) - 1, -1, -1):
        group_value = groups[i]
        if group_value == 0:
            continue
        full = i < len(groups) - 1
        group_text = _read_three_digits_vi(group_value, full)
        unit_text = units[i] if i < len(units) else ""
        parts.append(f"{group_text} {unit_text}".strip())

    return " ".join(parts).strip()


def _read_decimal_digits_vi(s: str) -> str:
    return " ".join(VI_DIGITS[int(ch)] for ch in s if ch.isdigit())


def normalize_vietnamese_numbers(text: str) -> str:
    def repl_date(match: re.Match) -> str:
        day = int(match.group(1))
        month = int(match.group(2))
        year = match.group(3)
        spoken = f"ngày {number_to_vietnamese(day)} tháng {number_to_vietnamese(month)}"
        if year:
            spoken += f" năm {number_to_vietnamese(int(year))}"
        return spoken

    def repl_time(match: re.Match) -> str:
        hour = int(match.group(1))
        minute = int(match.group(2))
        return f"{number_to_vietnamese(hour)} giờ {number_to_vietnamese(minute)}"

    def repl_percent(match: re.Match) -> str:
        raw = match.group(1)
        if "," in raw or "." in raw:
            left, right = re.split(r"[,.]", raw, maxsplit=1)
            return f"{number_to_vietnamese(int(left))} phẩy {_read_decimal_digits_vi(right)} phần trăm"
        return f"{number_to_vietnamese(int(raw))} phần trăm"

    def repl_number(match: re.Match) -> str:
        raw = match.group(0)
        if "/" in raw or ":" in raw:
            return raw

        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
            return number_to_vietnamese(int(re.sub(r"[.,]", "", raw)))

        if "," in raw or "." in raw:
            left, right = re.split(r"[,.]", raw, maxsplit=1)
            if right:
                return f"{number_to_vietnamese(int(left))} phẩy {_read_decimal_digits_vi(right)}"
            return number_to_vietnamese(int(left))

        return number_to_vietnamese(int(raw))

    # Common Vietnamese date and time forms.
    normalized = re.sub(r"\b([0-3]?\d)/([0-1]?\d)(?:/(\d{2,4}))?\b", repl_date, text)
    normalized = re.sub(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)\b", repl_time, normalized)
    normalized = re.sub(r"\b(\d+(?:[.,]\d+)?)\s*%", repl_percent, normalized)
    normalized = re.sub(r"\b\d+(?:[.,]\d+)?\b", repl_number, normalized)
    return normalized


def calculate_keep_len(text: str, lang: str) -> int:
    if lang in ["ja", "zh-cn"]:
        return -1

    word_count = len(text.split())
    num_punct = text.count(".") + text.count("!") + text.count("?") + text.count(",")

    if word_count < 5:
        return 15000 * word_count + 2000 * num_punct
    if word_count < 10:
        return 13000 * word_count + 2000 * num_punct
    return -1


def split_tts_sentences(text: str, lang: str):
    if lang in ["ja", "zh-cn"]:
        chunks = [s.strip() for s in text.split("\u3002") if s.strip()]
        return chunks if chunks else [text]

    if lang == "vi" and sent_tokenize is not None:
        try:
            chunks = [s.strip() for s in sent_tokenize(text) if s.strip()]
            chunks = _split_long_vi_chunks(chunks)
            return chunks if chunks else [text]
        except Exception:
            pass

    chunks = [s.strip() for s in re.split(r"(?<=[.?!])\s+", text) if s.strip()]
    if lang == "vi":
        chunks = _split_long_vi_chunks(chunks)
    return chunks if chunks else [text]


def _split_long_vi_chunks(chunks, max_words: int = 24):
    result = []
    for chunk in chunks:
        words = chunk.split()
        if len(words) <= max_words:
            result.append(chunk)
            continue

        pieces = [p.strip() for p in re.split(r"(?<=[,;:])\s+", chunk) if p.strip()]
        if len(pieces) <= 1:
            result.append(chunk)
            continue

        buf = ""
        buf_words = 0
        for piece in pieces:
            wc = len(piece.split())
            if buf and (buf_words + wc) > max_words:
                result.append(buf.strip())
                buf = piece
                buf_words = wc
            else:
                buf = f"{buf} {piece}".strip() if buf else piece
                buf_words += wc
        if buf:
            result.append(buf.strip())

    return result


def normalize_chatterbox_language(language: str) -> str:
    lang = normalize_language_code(language, default="en")
    if lang == "zh-cn":
        return "zh"
    return lang


def infer_chatterbox_to_file(
    chatterbox_model,
    text: str,
    language: str,
    speaker_wav: str,
    output_file: str,
    temperature: float,
    top_p: float = 1.0,
    repetition_penalty: float = 2.0,
):
    lang = normalize_chatterbox_language(language)
    supported = set(ChatterboxMultilingualTTS.get_supported_languages().keys())
    if lang not in supported:
        lang = "en"
    wav = chatterbox_model.generate(
        text=text,
        language_id=lang,
        audio_prompt_path=speaker_wav,
        temperature=max(0.05, min(float(temperature), 1.5)),
        top_p=max(0.1, min(float(top_p), 1.0)),
        repetition_penalty=max(1.0, min(float(repetition_penalty), 4.0)),
    )
    torchaudio.save(output_file, wav, chatterbox_model.sr)


def infer_vieneu_to_file(
    vieneu_model,
    text: str,
    speaker_wav: str,
    speaker_text: str,
    output_file: str,
    temperature: float,
):
    audio = vieneu_model.infer(
        text=text,
        ref_audio=speaker_wav,
        ref_text=speaker_text,
        temperature=max(0.1, min(float(temperature), 1.5)),
    )
    vieneu_model.save(audio, output_file)


def resolve_paths(params):
    custom_dir = params.get("custom_output_path")
    filename = params.get("output_filename", "output.wav")
    if not filename.endswith(".wav"):
        filename += ".wav"

    base_path = get_base_path()
    model_candidates = [
        os.path.join(base_path, "..", "models"),
        os.path.join(base_path, "models"),
        os.path.join(os.getcwd(), "models"),
    ]
    models_dir = next((p for p in model_candidates if os.path.exists(p)), model_candidates[0])
    chatterbox_path = os.path.join(models_dir, "chatterbox")
    vieneu_path = os.path.join(models_dir, "vieneu")
    whisper_path = os.path.join(models_dir, "whisper")
    
    if custom_dir and os.path.exists(custom_dir):
        output_path = custom_dir
    else:
        output_path = os.path.join(get_base_path(), "..", "output")
    output_file = os.path.join(output_path, filename)
    
    os.makedirs(chatterbox_path, exist_ok=True)
    os.makedirs(vieneu_path, exist_ok=True)
    os.makedirs(whisper_path, exist_ok=True)
    os.makedirs(output_path, exist_ok=True)
    return {
        "base_path": base_path,
        "models_dir": models_dir,
        "chatterbox_path": chatterbox_path,
        "vieneu_path": vieneu_path,
        "whisper_path": whisper_path,
        "output_path": output_path,
        "output_file": output_file,
    }


def ensure_runtime_models(params, paths, runtime_cache):
    req_device = params.get("device", "auto")
    cuda_ok, cuda_reason = detect_usable_cuda()
    if req_device == "cuda":
        if cuda_ok:
            device = "cuda"
        else:
            print(f"WARNING: CUDA requested but unusable ({cuda_reason}). Falling back to CPU.")
            device = "cpu"
    elif req_device == "auto":
        device = "cuda" if cuda_ok else "cpu"
        if device == "cpu" and not torch.cuda.is_available():
            print("INFO: CUDA not available. Using CPU.")
        elif device == "cpu":
            print(f"INFO: CUDA detected but unusable ({cuda_reason}). Using CPU.")
    else:
        device = req_device

    input_language = params.get("language", "vi")
    language = normalize_language_code(input_language, default="vi")
    use_vieneu = language == "vi"
    preload_all_tts = bool(params.get("preload_all_tts", False))

    runtime_cache["device"] = device
    print(f"Using device: {device}")

    should_load_vieneu = preload_all_tts or use_vieneu
    should_load_chatterbox = preload_all_tts or (not use_vieneu)

    if should_load_vieneu:
        if (
            runtime_cache.get("vieneu_model") is None
            or runtime_cache.get("vieneu_device") != device
        ):
            runtime_cache["vieneu_model"] = None
            runtime_cache["vieneu_device"] = device
            print("Loading VieNeu-TTS model...")
            runtime_cache["vieneu_model"] = load_vieneu_model(device)
            print("VieNeu-TTS model loaded.")
    if should_load_chatterbox:
        if (
            runtime_cache.get("chatterbox_model") is None
            or runtime_cache.get("chatterbox_device") != device
        ):
            runtime_cache["chatterbox_model"] = None
            runtime_cache["chatterbox_device"] = device
            print("Loading Chatterbox multilingual model...")
            runtime_cache["chatterbox_model"] = load_chatterbox_model(device)
            print("Chatterbox multilingual model loaded.")

    return {
        "device": device,
        "language": language,
        "use_vieneu": use_vieneu,
        "chatterbox_model": runtime_cache.get("chatterbox_model"),
        "vieneu_model": runtime_cache.get("vieneu_model"),
    }


def process_request(params, runtime_cache):
    paths = resolve_paths(params)

    try:
        rt = ensure_runtime_models(params, paths, runtime_cache)
        device = rt["device"]
        language = rt["language"]
        use_vieneu = rt["use_vieneu"]
        chatterbox_model = rt["chatterbox_model"]
        vieneu_model = rt["vieneu_model"]

        if params.get("warmup_only"):
            if params.get("export_srt", True):
                print("Preloading Faster-Whisper model...")
                whisper_model = runtime_cache.get("whisper_model")
                whisper_device = runtime_cache.get("whisper_device")
                if whisper_model is None or whisper_device != device:
                    runtime_cache["whisper_model"] = create_whisper_model(device, paths["whisper_path"])
                    runtime_cache["whisper_device"] = device
            print("SUCCESS|WARMUP")
            return "WARMUP"

        text = params["text"]
        print(f"TEXT_BEFORE_TTS|{preview_text_for_log(text)}")
        print(f"Synthesizing voice directly to {paths['output_file']}...")
        if use_vieneu:
            speaker_text = (params.get("speaker_text") or "").strip()
            if not speaker_text:
                print("Deriving speaker_text from speaker_wav for VieNeu-TTS...")
                speaker_text = transcribe_reference_audio(
                    runtime_cache=runtime_cache,
                    device=device,
                    whisper_path=paths["whisper_path"],
                    speaker_wav=params["speaker_wav"],
                )
            infer_vieneu_to_file(
                vieneu_model,
                text=text,
                speaker_wav=params["speaker_wav"],
                speaker_text=speaker_text,
                output_file=paths["output_file"],
                temperature=params.get("temperature", 1.0),
            )
        else:
            infer_chatterbox_to_file(
                chatterbox_model,
                text=text,
                language=language,
                speaker_wav=params["speaker_wav"],
                output_file=paths["output_file"],
                temperature=params.get("temperature", 0.8),
                top_p=params.get("top_p", 1.0),
                repetition_penalty=params.get("repetition_penalty", 2.0),
            )

        # Transcription (SRT)
        if params.get("export_srt"):
            print(f"Generating SRT using Faster-Whisper...")
            whisper_model = runtime_cache.get("whisper_model")
            whisper_device = runtime_cache.get("whisper_device")
            if whisper_model is None or whisper_device != device:
                runtime_cache["whisper_model"] = create_whisper_model(device, paths["whisper_path"])
                runtime_cache["whisper_device"] = device
            whisper_language = normalize_whisper_language(language)
            segments, info = runtime_cache["whisper_model"].transcribe(
                paths["output_file"],
                beam_size=5,
                language=whisper_language,
                task="transcribe",
            )
            srt_content = generate_srt(segments)
            
            srt_file = paths["output_file"].replace(".wav", ".srt")
            with open(srt_file, "w", encoding="utf-8") as f:
                f.write(srt_content)
            print(f"SRT saved to {srt_file}")

        print(f"SUCCESS|{paths['output_file']}")
        return paths["output_file"]
        
    except Exception as e:
        print(f"ERROR: {str(e)}", file=sys.stderr)
        raise


def run_daemon():
    runtime_cache = {}
    print("READY|DAEMON")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            action = str(msg.get("action", "synthesize")).strip().lower()
            if action == "shutdown":
                print("SUCCESS|SHUTDOWN")
                break
            params = msg.get("params", msg)
            process_request(params, runtime_cache)
        except Exception as e:
            print(f"ERROR|{str(e)}")
            continue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", type=str, required=False)
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
        return

    if not args.params:
        print("ERROR: missing --params", file=sys.stderr)
        sys.exit(1)

    params = json.loads(args.params)
    process_request(params, runtime_cache={})

if __name__ == "__main__":
    main()
