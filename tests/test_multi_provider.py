from tune_history.metadata_enrichment.base import MusicMetadataProvider
from tune_history.metadata_enrichment.multi_provider import FanOutProvider


class OkProvider(MusicMetadataProvider):
    name = "ok_provider"

    def search_recordings(self, artist, track, album=None, duration_seconds=None, limit=5):
        return [{"artist": artist, "track": track}]

    def lookup_by_isrc(self, isrc):
        return [{"isrc": isrc}]


class BrokenProvider(MusicMetadataProvider):
    name = "broken_provider"

    def search_recordings(self, artist, track, album=None, duration_seconds=None, limit=5):
        raise RuntimeError("simulated provider outage")

    def lookup_by_isrc(self, isrc):
        raise RuntimeError("simulated provider outage")


def test_fan_out_concatenates_results_from_all_providers():
    fan_out = FanOutProvider([OkProvider(), OkProvider()])
    results = fan_out.search_recordings("Artist", "Track")
    assert len(results) == 2


def test_fan_out_tags_each_result_with_its_provider():
    fan_out = FanOutProvider([OkProvider()])
    results = fan_out.search_recordings("Artist", "Track")
    assert results[0]["_provider"] == "ok_provider"


def test_one_broken_provider_does_not_break_the_others():
    fan_out = FanOutProvider([BrokenProvider(), OkProvider()])
    results = fan_out.search_recordings("Artist", "Track")
    assert len(results) == 1
    assert results[0]["_provider"] == "ok_provider"


def test_all_providers_broken_returns_empty_list_not_exception():
    fan_out = FanOutProvider([BrokenProvider(), BrokenProvider()])
    results = fan_out.search_recordings("Artist", "Track")
    assert results == []


def test_isrc_lookup_fans_out_too():
    fan_out = FanOutProvider([OkProvider(), BrokenProvider()])
    results = fan_out.lookup_by_isrc("USRC17607839")
    assert len(results) == 1
    assert results[0]["_provider"] == "ok_provider"
