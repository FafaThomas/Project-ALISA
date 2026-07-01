import time

from TTS.api import TTS

from config.config import (
    DEVICE,
    XTTS_MODEL,
    XTTS_CACHE,
)


class XTTSManager:

    def __init__(self):

        print("=" * 60)
        print(" XTTS Manager")
        print("=" * 60)
        print(f"Model        : {XTTS_MODEL}")
        print(f"Device       : {DEVICE}")
        print(f"Cache Folder : {XTTS_CACHE}")
        print()

        start = time.perf_counter()

        print("[1/3] Initializing XTTS...")
        print("      First launch downloads the model.")
        print()

        self.tts = TTS(
            model_name=XTTS_MODEL,
            gpu=DEVICE == "cuda",
        )

        elapsed = time.perf_counter() - start

        print()
        print("[2/3] XTTS Ready!")
        print(f"Initialization Time : {elapsed:.2f} sec")
        print("=" * 60)

    def speak(
        self,
        text: str,
        speaker_wav: str,
        language: str,
        output_path: str,
    ):

        print("=" * 60)
        print(" XTTS Synthesis")
        print("=" * 60)
        print(f"Language      : {language}")
        print(f"Speaker WAV   : {speaker_wav}")
        print(f"Output        : {output_path}")
        print()

        start = time.perf_counter()

        self.tts.tts_to_file(
            text=text,
            speaker_wav=speaker_wav,
            language=language,
            file_path=output_path,
        )

        elapsed = time.perf_counter() - start

        print()
        print("✓ Speech generated successfully.")
        print(f"Synthesis Time : {elapsed:.2f} sec")
        print("=" * 60)