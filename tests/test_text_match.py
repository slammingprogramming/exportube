from tune_history.matching.text_match import artist_similarity, is_fuzzy_match, text_similarity


def test_identical_strings_match_fully():
    assert text_similarity("Daft Punk", "Daft Punk") == 1.0


def test_accented_characters_match():
    assert text_similarity("Beyonce", "Beyoncé") > 0.9


def test_punctuation_insensitive():
    assert text_similarity("Guns N' Roses", "Guns N Roses") > 0.9


def test_featuring_clause_stripped_for_artist_match():
    a = "Calvin Harris feat. Rihanna"
    b = "Calvin Harris"
    assert artist_similarity(a, b) > 0.9


def test_dissimilar_strings_score_low():
    assert text_similarity("Daft Punk", "Justin Bieber") < 0.5


def test_empty_strings_never_match():
    assert text_similarity("", "Daft Punk") == 0.0
    assert text_similarity(None, None) == 0.0


def test_is_fuzzy_match_threshold():
    assert is_fuzzy_match("One More Time", "One More Time", threshold=0.9)
    assert not is_fuzzy_match("One More Time", "Completely Different", threshold=0.9)
