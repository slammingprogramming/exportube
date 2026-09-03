from datetime import timezone

from tune_history.history_import.normalize import compute_dedup_key, normalize_timestamp


def test_parses_iso8601_takeout_json_timestamp():
    dt = normalize_timestamp("2024-01-15T03:14:21.123Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).year == 2024


def test_parses_takeout_html_formatted_timestamp():
    dt = normalize_timestamp("Jan 15, 2024, 3:14:21 AM UTC")
    assert dt is not None
    assert dt.month == 1 and dt.day == 15


def test_us_timezone_abbreviations_resolve_to_correct_utc_offset():
    # Regression test: confirmed against a real Google Takeout
    # watch-history.html export, where every entry is timestamped like
    # "Mar 21, 2025, 11:04:24 PM EDT". dateutil cannot resolve "EDT" on
    # its own and silently returns a naive datetime still in local time;
    # without the tzinfos table in normalize.py, normalize_timestamp would
    # wrongly stamp that naive local time as if it were already UTC --
    # off by exactly the zone's offset, on every single entry.
    edt = normalize_timestamp("Mar 21, 2025, 11:04:24 PM EDT")
    assert edt.hour == 3 and edt.day == 22  # 23:04 EDT (UTC-4) -> 03:04 UTC next day
    est = normalize_timestamp("Jan 21, 2025, 11:04:24 PM EST")
    assert est.hour == 4 and est.day == 22  # UTC-5
    pst = normalize_timestamp("Mar 21, 2025, 11:04:24 PM PST")
    assert pst.hour == 7 and pst.day == 22  # UTC-8
    pdt = normalize_timestamp("Mar 21, 2025, 11:04:24 PM PDT")
    assert pdt.hour == 6 and pdt.day == 22  # UTC-7


def test_narrow_no_break_space_before_am_pm_is_handled():
    # Real Takeout HTML separates seconds from AM/PM with U+202F (narrow
    # no-break space), not a regular ASCII space. Build the string with
    # the exact codepoint so this doesn't silently degrade into testing
    # an ordinary space instead.
    raw = "Mar 21, 2025, 11:04:24" + chr(0x202F) + "PM EDT"
    dt = normalize_timestamp(raw)
    assert dt is not None
    assert dt.hour == 3 and dt.day == 22


def test_unparseable_timestamp_returns_none_not_guess():
    assert normalize_timestamp("not a real date") is None
    assert normalize_timestamp("") is None
    assert normalize_timestamp(None) is None


def test_dedup_key_stable_for_identical_input():
    from datetime import datetime
    dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    k1 = compute_dedup_key("takeout_json", "abc", "url", dt, "title")
    k2 = compute_dedup_key("takeout_json", "abc", "url", dt, "title")
    assert k1 == k2


def test_dedup_key_differs_for_different_watch_times():
    from datetime import datetime
    dt1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    dt2 = datetime(2024, 2, 1, tzinfo=timezone.utc)
    k1 = compute_dedup_key("takeout_json", "abc", "url", dt1, "title")
    k2 = compute_dedup_key("takeout_json", "abc", "url", dt2, "title")
    assert k1 != k2


def test_dedup_key_falls_back_to_raw_when_no_timestamp():
    k1 = compute_dedup_key("takeout_json", None, None, None, None, raw_fallback="entry-A")
    k2 = compute_dedup_key("takeout_json", None, None, None, None, raw_fallback="entry-B")
    assert k1 != k2
