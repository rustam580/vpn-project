from __future__ import annotations

from tools.find_wbstream_token_candidates import find_candidates, load_token_candidate


def test_find_candidates_extracts_access_token_from_leveldb_log(tmp_path) -> None:
    profile = tmp_path / "WB Stream"
    leveldb = profile / "Local Storage" / "leveldb"
    leveldb.mkdir(parents=True)
    token = "eyJ" + "a" * 40 + "." + "b" * 40 + "." + "c" * 40
    (leveldb / "000003.log").write_text(
        '{"accessToken":"' + token + '","other":"value"}',
        encoding="utf-8",
    )

    candidates = find_candidates(profile)

    assert candidates
    assert candidates[0]["key"] == "accessToken"
    assert candidates[0]["value"] == token


def test_load_token_candidate_returns_selected_value(tmp_path) -> None:
    profile = tmp_path / "WB Stream"
    leveldb = profile / "Local Storage" / "leveldb"
    leveldb.mkdir(parents=True)
    (leveldb / "000003.log").write_text(
        '{"accessToken":"' + "x" * 80 + '"}',
        encoding="utf-8",
    )

    assert load_token_candidate(profile, index=0) == "x" * 80
