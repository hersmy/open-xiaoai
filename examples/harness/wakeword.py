"""One-thread keyword spotting of speaker PCM using upstream's small KWS model."""
from pathlib import Path
import numpy as np


class WakeWord:
    phrase = "大爱同学"

    def __init__(self, directory):
        import sherpa_onnx

        root = Path(directory)
        self.model = sherpa_onnx.KeywordSpotter(
            provider="cpu", num_threads=1, max_active_paths=8,
            keywords_score=2.0, keywords_threshold=.35, num_trailing_blanks=1,
            tokens=str(root / "tokens.txt"), keywords_file=str(root / "keywords.txt"),
            encoder=str(root / "encoder.onnx"), decoder=str(root / "decoder.onnx"),
            joiner=str(root / "joiner.onnx"),
        )
        self.stream = self.model.create_stream()

    def reset(self):
        self.stream = self.model.create_stream()

    def feed(self, pcm):
        if len(pcm) % 2:
            return None
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        self.stream.accept_waveform(16000, samples)
        while self.model.is_ready(self.stream):
            self.model.decode_stream(self.stream)
            result = self.model.get_result(self.stream)
            if result:
                self.model.reset_stream(self.stream)
                return result
        return None
