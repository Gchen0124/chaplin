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


def test_create_and_get_demo(tmp_path):
    s = make_store(tmp_path)
    did = s.create_demo(
        source_lang="auto", target_lang="en", duration_s=5.0,
        screen_path="/d/screen.webm", audio_path=None, status="saved",
    )
    row = s.get_demo(did)
    assert row["id"] == did
    assert row["status"] == "saved"
    assert row["utterances"] == []
    assert row["export_path"] is None


def test_replace_and_edit_utterances_ignores_timestamps(tmp_path):
    s = make_store(tmp_path)
    did = s.create_demo(
        source_lang="zh", target_lang="en", duration_s=5.0,
        screen_path="/d/screen.webm", status="saved",
    )
    s.replace_utterances(did, [
        {"start_s": 0.2, "end_s": 1.8, "original_text": "你好",
         "english_text": "Hello.", "source_lang": "zh"},
        {"start_s": 2.0, "end_s": 3.4, "original_text": "打开设置",
         "english_text": "Open Settings.", "source_lang": "zh"},
    ])
    row = s.get_demo(did)
    assert [u["idx"] for u in row["utterances"]] == [0, 1]
    uid = row["utterances"][0]["id"]
    s.update_utterance_texts(did, [(uid, "Hi there."), ("missing", "nope")])
    row = s.get_demo(did)
    assert row["utterances"][0]["english_text"] == "Hi there."
    assert row["utterances"][0]["start_s"] == 0.2
    assert row["utterances"][1]["english_text"] == "Open Settings."


def test_update_demo_status(tmp_path):
    s = make_store(tmp_path)
    did = s.create_demo(
        source_lang="en", target_lang="en", duration_s=2.0,
        screen_path="/d/screen.webm", status="saved",
    )
    s.update_demo(did, status="transcribed", source_lang="en", warning="polish skipped")
    row = s.get_demo(did)
    assert row["status"] == "transcribed"
    assert row["warning"] == "polish skipped"
