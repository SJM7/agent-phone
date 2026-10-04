"""The committer is the part that can lose or repeat words. Keep it honest."""
from agent_phone.whistle_stream import SAMPLE_RATE, StreamCommitter, join_hypotheses


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def test_open_call_keeps_the_latest_hypothesis_and_the_audio():
    committer = StreamCommitter()
    committer.append(_silence(4))
    first = [{"word": "hello", "start": 0.2, "end": 0.6},
             {"word": "there", "start": 0.7, "end": 1.1}]
    revised = [{"word": "hello", "start": 0.2, "end": 0.6},
               {"word": "their", "start": 0.7, "end": 1.1}]
    committer.absorb({"text": "hello there", "words": first})
    committer.absorb({"text": "hello their", "words": revised})
    assert committer.text() == "hello their"
    assert abs(committer.duration() - 4) < 0.02


def test_hangup_pastes_the_full_buffer_transcript():
    committer = StreamCommitter()
    committer.append(_silence(8))
    committer.absorb({"text": "hello their", "words": []})
    committer.absorb({"text": "hello their now.", "words": [
        {"word": "hello", "start": 0.2, "end": 0.6},
        {"word": "their", "start": 0.7, "end": 1.1},
        {"word": "now.", "start": 1.2, "end": 1.6},
    ]}, final=True)
    assert committer.text() == "hello their now."


def test_a_long_call_closes_a_window_before_the_model_limit():
    committer = StreamCommitter()
    committer.append(_silence(26))
    words = [
        {"word": f"w{i}", "start": i * 0.8, "end": i * 0.8 + 0.4}
        for i in range(30)
    ]
    committer.absorb({"text": " ".join(word["word"] for word in words), "words": words})
    assert committer.closed_text.startswith("w0")
    assert committer.duration() < 8
    assert committer.duration() < 30


def test_seam_does_not_repeat_the_overlap():
    assert join_hypotheses(
        "leave the existing needle install",
        "needle install alone and keep going",
    ) == "leave the existing needle install alone and keep going"


def test_silence_does_not_fill_the_window():
    committer = StreamCommitter()
    committer.append(_silence(5))
    committer.absorb({"text": "", "words": []})
    assert committer.text() == ""
    assert committer.duration() <= 2.05
