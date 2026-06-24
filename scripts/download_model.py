"""
Downloads Mistral-7B-Instruct-v0.2 Q4_K_M (~4.1 GB) from Hugging Face.

Usage:
    python scripts/download_model.py

The model is saved to ./models/ and is .gitignored.
Set HF_TOKEN env var if you hit rate limits (free HF account token).
"""

import os
import sys
from pathlib import Path

try:
    from huggingface_hub import hf_hub_download
except ImportError:
    print("huggingface_hub not installed. Run: uv pip install huggingface_hub")
    sys.exit(1)

REPO_ID   = "TheBloke/Mistral-7B-Instruct-v0.2-GGUF"
FILENAME  = "mistral-7b-instruct-v0.2.Q4_K_M.gguf"
LOCAL_DIR = Path(__file__).parent.parent / "models"

def main():
    LOCAL_DIR.mkdir(exist_ok=True)
    dest = LOCAL_DIR / FILENAME
    if dest.exists():
        print(f"Already downloaded: {dest}")
        return

    print(f"Downloading {FILENAME} (~4.1 GB) …")
    path = hf_hub_download(
        repo_id=REPO_ID,
        filename=FILENAME,
        local_dir=str(LOCAL_DIR),
        token=os.environ.get("HF_TOKEN"),
    )
    print(f"Saved to: {path}")
    print(f"\nNext step:")
    print(f"  MODEL_PATH={path} ./scripts/start_node.sh")

if __name__ == "__main__":
    main()
