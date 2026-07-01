import time

from faster_whisper import WhisperModel

from config.config import (
    DEVICE,
    COMPUTE_TYPE,
    WHISPER_MODEL,
    WHISPER_CACHE,
)


class WhisperManager:

    def __init__(self):

        print("=" * 60)
        print(" Whisper Manager")
        print("=" * 60)
        print(f"Model        : {WHISPER_MODEL}")
        print(f"Device       : {DEVICE}")
        print(f"Compute Type : {COMPUTE_TYPE}")
        print(f"Cache Folder : {WHISPER_CACHE}")
        print()

        start = time.perf_counter()

        print("[1/3] Initializing Whisper...")
        print("      First launch downloads the model.")
        print()

        self.model = WhisperModel(
            WHISPER_MODEL,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=str(WHISPER_CACHE),
        )

        elapsed = time.perf_counter() - start

        print()
        print("[2/3] Whisper Ready")
        print(f"Initialization Time : {elapsed:.2f} sec")
        print("=" * 60)

    def transcribe(self, audio_path: str):

        print(f"[Whisper] Transcribing {audio_path}")

        start = time.perf_counter()

        segments, info = self.model.transcribe(
            audio_path,
            beam_size=5,
        )

        text = "".join(segment.text for segment in segments).strip()

        elapsed = time.perf_counter() - start

        print(f"[Whisper] Completed in {elapsed:.2f} sec")

        return {
            "text": text,
            "language": info.language,
            "probability": info.language_probability,
        }