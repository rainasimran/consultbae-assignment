import unittest
from unittest.mock import patch

from app.audio_analysis import analyze_audio


class AudioAnalysisMissingBinaryTests(unittest.TestCase):
    @patch("app.audio_analysis.subprocess.run", side_effect=FileNotFoundError("ffmpeg not found"))
    def test_missing_ffmpeg_returns_empty_analysis(self, _mock_run):
        result = analyze_audio("fake.wav")

        self.assertIsNone(result["duration_sec"])
        self.assertIsNone(result["sample_rate_hz"])
        self.assertIsNone(result["bitrate_kbps"])
        self.assertIsNone(result["loudness_dbfs"])
        self.assertIsNone(result["noise_estimate"])


if __name__ == "__main__":
    unittest.main()
