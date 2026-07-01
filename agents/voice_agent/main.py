from models.whisper_manager import WhisperManager
from models.xtts_manager import XTTSManager


def main():

    whisper = WhisperManager()

    xtts = XTTSManager()

    print()
    print("===================================")
    print(" Voice Agent Initialized")
    print("===================================")


if __name__ == "__main__":
    main()