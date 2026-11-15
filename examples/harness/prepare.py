"""Prepare only local model assets and the macOS Sherpa runtime library link."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent
URL = "https://gitee.com/idootop/artifacts/releases/download/open-xiaoai-kws/kws.tar.gz"
SHA256 = "690a38b94159f30f600494d5d6501f22083cbf6a967512833fb8e216707b07b9"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    runtime = ROOT / ".runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    archive = args.archive or runtime / "kws.tar.gz"
    if not archive.exists():
        subprocess.run(["curl", "-fL", "--retry", "2", "--connect-timeout", "10",
                        "--max-time", "180", URL, "-o", str(archive)], check=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("KWS archive checksum mismatch; no assets installed")
    models = runtime / "models"
    models.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        for filename in ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"):
            member = tar.getmember("models/" + filename)
            if not member.isfile() or member.size > 20 * 1024 * 1024:
                raise RuntimeError("Unexpected model archive member")
            with tar.extractfile(member) as source, (models / filename).open("wb") as output:
                shutil.copyfileobj(source, output)
    keyword = "d à ài t óng x ué"
    tokens = {line.split()[0] for line in (models / "tokens.txt").read_text().splitlines() if line.strip()}
    if not set(keyword.split()) <= tokens:
        raise RuntimeError("Model tokens do not support 大爱同学")
    (models / "keywords.txt").write_text(keyword + " :2.0 #0.35 @大爱同学\n")
    if sys.platform == "darwin":
        ort = importlib.util.find_spec("onnxruntime")
        sherpa = importlib.util.find_spec("sherpa_onnx")
        if not ort or not sherpa:
            raise RuntimeError("Run uv sync before prepare.py")
        library = Path(ort.origin).parent / "capi/libonnxruntime.1.23.2.dylib"
        destination = Path(sherpa.origin).parent / "lib/libonnxruntime.1.23.2.dylib"
        if not library.exists():
            raise RuntimeError("Pinned ONNX Runtime library missing")
        if not destination.exists():
            destination.symlink_to(library)
    from wakeword import WakeWord
    WakeWord(models)
    print("Verified and loaded local wake model: 大爱同学")


if __name__ == "__main__":
    main()
