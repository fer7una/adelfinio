import unittest

from scripts.video_pipeline_v2 import extract_word_timings_for_event


def make_alignment(utterance_id: str, words: list[str]) -> dict:
    return {
        "utterance_id": utterance_id,
        "words": [
            {
                "index": idx,
                "text": word,
                "start_s": float(idx),
                "end_s": float(idx) + 0.5,
            }
            for idx, word in enumerate(words)
        ],
    }


class ExtractWordTimingsForEventTests(unittest.TestCase):
    def test_matches_exact_tokens(self) -> None:
        event = {"event_id": "ev1", "audio_text": "La monarquía visigoda"}
        alignment = make_alignment("utt_001", ["La", "monarquía", "visigoda"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 2))

    def test_matches_accent_insensitive_tokens(self) -> None:
        event = {"event_id": "ev2", "audio_text": "quién manda"}
        alignment = make_alignment("utt_002", ["quien", "manda"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 1))

    def test_matches_contracted_articles(self) -> None:
        event = {"event_id": "ev3", "audio_text": "del norte"}
        alignment = make_alignment("utt_003", ["de", "el", "norte"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 2))

    def test_matches_split_numeric_tokens(self) -> None:
        event = {"event_id": "ev3b", "audio_text": "en 711 llegan"}
        alignment = make_alignment("utt_003b", ["en", "7", "11", "llegan"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 3))

    def test_matches_close_transcription_typos(self) -> None:
        event = {"event_id": "ev3c", "audio_text": "reino visigodo"}
        alignment = make_alignment("utt_003c", ["reino", "visivodo"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 1))

    def test_matches_close_vowel_typos(self) -> None:
        event = {"event_id": "ev3d", "audio_text": "del reino"}
        alignment = make_alignment("utt_003d", ["del", "reyno"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 1))

    def test_tolerates_short_missing_tail_after_prefix_match(self) -> None:
        event = {"event_id": "ev4", "audio_text": "fácil en esas alturas que las"}
        alignment = make_alignment("utt_004", ["Fácil", "En", "esas", "alturas", "Kelas"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 3))

    def test_tolerates_full_missing_tail_when_alignment_ends(self) -> None:
        event = {"event_id": "ev4b", "audio_text": "del rey cubierto de flechas la derrota deshizo la columna"}
        alignment = make_alignment("utt_004b", ["Del", "rey", "cubierto", "de", "flechas"])
        self.assertEqual(extract_word_timings_for_event(event, alignment, 0), (0, 4))

    def test_raises_on_mid_sequence_mismatch(self) -> None:
        event = {"event_id": "ev5", "audio_text": "la costa nos esperan"}
        alignment = make_alignment("utt_005", ["La", "costa", "jamas", "esperan"])
        with self.assertRaises(RuntimeError):
            extract_word_timings_for_event(event, alignment, 0)


if __name__ == "__main__":
    unittest.main()
