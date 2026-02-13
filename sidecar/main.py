import os
import sys
import json
import argparse
import inspect
import warnings

# Suppress typeguard instrumentation warnings
warnings.filterwarnings("ignore", message="instrumentor did not find the target function")

# Auto-agree to Coqui TTS license terms
os.environ["COQUI_TOS_AGREED"] = "1"

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

from TTS.api import TTS
from faster_whisper import WhisperModel
import torch

import time
from pydub import AudioSegment
import re

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--params", type=str, required=True)
    args = parser.parse_args()

    params = json.loads(args.params)
    
    # Advanced Paths
    custom_dir = params.get("custom_output_path")
    filename = params.get("output_filename", "output.wav")
    if not filename.endswith(".wav"): filename += ".wav"
    
    models_dir = os.path.join(get_base_path(), "..", "models")
    xtts_path = os.path.join(models_dir, "xtts_v2")
    whisper_path = os.path.join(models_dir, "whisper")
    
    if custom_dir and os.path.exists(custom_dir):
        output_path = custom_dir
    else:
        output_path = os.path.join(get_base_path(), "..", "output")
    
    os.makedirs(xtts_path, exist_ok=True)
    os.makedirs(whisper_path, exist_ok=True)
    os.makedirs(output_path, exist_ok=True)

    output_file = os.path.join(output_path, filename)

    try:
        # Device selection with fallback
        req_device = params.get("device", "auto")
        if req_device == "cuda":
            if torch.cuda.is_available():
                device = "cuda"
            else:
                print("WARNING: CUDA requested but not available. Falling back to CPU.")
                device = "cpu"
        elif req_device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = req_device
            
        print(f"Using device: {device}")
        
        # Load XTTS-v2
        print(f"Loading XTTS-v2 model...")
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)

        # Synthesis Configuration
        pause_sentence = params.get("pause_sentence", 0.3) * 1000 # ms
        pause_paragraph = params.get("pause_paragraph", 0.8) * 1000 # ms
        text = params["text"]

        # If no custom pauses needed, use direct synthesis for speed
        if pause_sentence == 0 and pause_paragraph == 0:
            print(f"Synthesizing voice directly to {output_file}...")
            tts.tts_to_file(text=text, speaker_wav=params["speaker_wav"], language=params["language"], file_path=output_file, speed=params.get("speed", 1.0), temperature=params.get("temperature", 0.75))
        else:
            print(f"Synthesizing voice with custom pauses...")
            # Split into paragraphs
            paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
            combined_audio = AudioSegment.empty()
            
            temp_dir = os.path.join(get_base_path(), "temp_chunks")
            os.makedirs(temp_dir, exist_ok=True)
            
            for i, p in enumerate(paragraphs):
                # Split paragraph into sentences
                sentences = [s.strip() for s in re.split(r'(?<=[.?!])\s+', p) if s.strip()]
                paragraph_audio = AudioSegment.empty()
                
                for j, s in enumerate(sentences):
                    print(f"Processing: {s[:30]}...")
                    temp_chunk = os.path.join(temp_dir, f"chunk_{i}_{j}.wav")
                    tts.tts_to_file(
                        text=s,
                        speaker_wav=params["speaker_wav"],
                        language=params["language"],
                        file_path=temp_chunk,
                        speed=params.get("speed", 1.0),
                        temperature=params.get("temperature", 0.75)
                    )
                    
                    audio_chunk = AudioSegment.from_wav(temp_chunk)
                    paragraph_audio += audio_chunk
                    
                    # Add sentence pause
                    if j < len(sentences) - 1:
                        paragraph_audio += AudioSegment.silent(duration=pause_sentence)
                    
                    # Clean up
                    try: os.remove(temp_chunk)
                    except: pass
                
                combined_audio += paragraph_audio
                
                # Add paragraph pause
                if i < len(paragraphs) - 1:
                    combined_audio += AudioSegment.silent(duration=pause_paragraph)

            combined_audio.export(output_file, format="wav")

        # Transcription (SRT)
        if params.get("export_srt"):
            print(f"Generating SRT using Faster-Whisper...")
            whisper_model = WhisperModel("base", device=device, compute_type="float32" if device=="cpu" else "float16", download_root=whisper_path)
            segments, info = whisper_model.transcribe(output_file, beam_size=5)
            srt_content = generate_srt(segments)
            
            srt_file = output_file.replace(".wav", ".srt")
            with open(srt_file, "w", encoding="utf-8") as f:
                f.write(srt_content)
            print(f"SRT saved to {srt_file}")

        print(f"SUCCESS|{output_file}")
        
    except Exception as e:
        print(f"ERROR: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
