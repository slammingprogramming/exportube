import responses

from exportube.metadata_enrichment.discogs_provider import DiscogsProvider, SEARCH_URL


@responses.activate
def test_search_recordings_normalizes_results():
    responses.add(
        responses.GET, SEARCH_URL,
        json={"results": [
            {"id": 12345, "title": "Daft Punk - Discovery", "year": 2001, "country": "France",
             "format": ["Vinyl", "Album"]},
        ]},
        status=200,
    )
    provider = DiscogsProvider(token="fake-token", user_agent="exportube-test/0.1")
    results = provider.search_recordings("Daft Punk", "One More Time", album="Discovery")
    assert len(results) == 1
    assert results[0]["artist"] == "Daft Punk"
    assert results[0]["track"] == "Discovery"
    assert results[0]["release_date"] == "2001"
    assert results[0]["release_country"] == "France"
    assert results[0]["musicbrainz_recording_id"] is None  # never fabricated
    assert results[0]["recording_duration_seconds"] is None  # documented scope limit


@responses.activate
def test_empty_track_returns_no_results_without_network_call():
    provider = DiscogsProvider(token="fake-token", user_agent="exportube-test/0.1")
    results = provider.search_recordings("Artist", "")
    assert results == []
    assert len(responses.calls) == 0


@responses.activate
def test_network_error_returns_empty_list_not_exception():
    responses.add(responses.GET, SEARCH_URL, status=500)
    provider = DiscogsProvider(token="fake-token", user_agent="exportube-test/0.1")
    results = provider.search_recordings("Artist", "Track")
    assert results == []


@responses.activate
def test_results_are_cached():
    call_count = {"n": 0}

    def _callback(request):
        call_count["n"] += 1
        return (200, {}, '{"results": [{"id": 1, "title": "A - B", "year": 2020}]}')

    responses.add_callback(responses.GET, SEARCH_URL, callback=_callback)

    class DictCache:
        def __init__(self):
            self.store = {}

        def get_or_fetch(self, namespace, key, fetch_fn, force_refresh=False):
            cache_key = (namespace, key)
            if cache_key not in self.store:
                self.store[cache_key] = fetch_fn()
            return self.store[cache_key]

    cache = DictCache()
    provider = DiscogsProvider(token="fake-token", user_agent="exportube-test/0.1", cache=cache)
    provider.search_recordings("Artist", "Track")
    provider.search_recordings("Artist", "Track")
    assert call_count["n"] == 1
