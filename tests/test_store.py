from app.store import Store
from app.schemas import VocabHighlight


def make_store(tmp_path):
    s = Store(str(tmp_path / "chaplin.db"))
    s.init_db()
    return s


def test_save_and_get_session(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(
        source_lang="en", target_lang="en",
        original_text="i go store", refined_text="I went to the store.",
        vsr_raw_text="I GO STORE", video_path="/v.webm", audio_path="/a.wav",
        duration_s=3.2, confidence=0.91, input_source="audio",
    )
    assert isinstance(sid, str) and sid
    rows = s.list_sessions(limit=10)
    assert len(rows) == 1
    assert rows[0]["original_text"] == "i go store"
    assert rows[0]["id"] == sid


def test_sessions_ordered_newest_first(tmp_path):
    s = make_store(tmp_path)
    first = s.save_session(source_lang="en", target_lang="en", original_text="one",
                           refined_text="One.", vsr_raw_text="ONE", video_path="", audio_path=None,
                           duration_s=2.0, confidence=None, input_source="lip")
    second = s.save_session(source_lang="en", target_lang="en", original_text="two",
                            refined_text="Two.", vsr_raw_text="TWO", video_path="", audio_path=None,
                            duration_s=2.0, confidence=None, input_source="lip")
    rows = s.list_sessions(limit=10)
    assert rows[0]["id"] == second
    assert rows[1]["id"] == first


def test_save_and_list_vocab(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(source_lang="en", target_lang="en", original_text="big house",
                         refined_text="substantial home", vsr_raw_text="", video_path="",
                         audio_path=None, duration_s=2.0, confidence=None, input_source="lip")
    s.save_vocab(sid, [
        VocabHighlight(original_phrase="big", refined_phrase="substantial", reason="precise"),
        VocabHighlight(original_phrase="house", refined_phrase="home", reason="warmer"),
    ], source_lang="en")
    vocab = s.list_vocab()
    assert len(vocab) == 2
    assert {v["refined_phrase"] for v in vocab} == {"substantial", "home"}


def test_star_vocab(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(source_lang="en", target_lang="en", original_text="x", refined_text="X.",
                         vsr_raw_text="", video_path="", audio_path=None, duration_s=2.0,
                         confidence=None, input_source="lip")
    s.save_vocab(sid, [VocabHighlight(original_phrase="x", refined_phrase="ex", reason="r")],
                 source_lang="en")
    vid = s.list_vocab()[0]["id"]
    updated = s.set_vocab_starred(vid, True)
    assert updated["starred"] is True
    starred_only = s.list_vocab(starred=True)
    assert len(starred_only) == 1


def test_vocab_search(tmp_path):
    s = make_store(tmp_path)
    sid = s.save_session(source_lang="en", target_lang="en", original_text="x", refined_text="X.",
                         vsr_raw_text="", video_path="", audio_path=None, duration_s=2.0,
                         confidence=None, input_source="lip")
    s.save_vocab(sid, [
        VocabHighlight(original_phrase="happy", refined_phrase="elated", reason="r"),
        VocabHighlight(original_phrase="sad", refined_phrase="forlorn", reason="r"),
    ], source_lang="en")
    found = s.list_vocab(q="elat")
    assert len(found) == 1 and found[0]["refined_phrase"] == "elated"
