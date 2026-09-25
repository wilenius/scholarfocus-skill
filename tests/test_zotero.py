from scholarlib.apis.zotero_local import (
    DEFAULT_BASE,
    ZoteroClient,
    ZoteroLocalClient,
)


def test_local_backend_remains_the_default():
    client = ZoteroClient()

    assert client.backend == "local"
    assert client.base_url == DEFAULT_BASE
    assert client._auth_headers() == {}
    assert ZoteroLocalClient is ZoteroClient


def test_web_backend_builds_user_url_and_auth_headers():
    client = ZoteroClient(
        backend="web",
        library_type="user",
        library_id="12345",
        api_key="secret",
    )

    assert client.base_url == "https://api.zotero.org/users/12345"
    assert client._auth_headers() == {
        "Zotero-API-Version": "3",
        "Zotero-API-Key": "secret",
    }


def test_web_backend_can_read_standard_environment_variables(monkeypatch):
    monkeypatch.setenv("ZOTERO_LIBRARY_TYPE", "group")
    monkeypatch.setenv("ZOTERO_LIBRARY_ID", "67890")
    monkeypatch.setenv("ZOTERO_API_KEY", "from-environment")

    client = ZoteroClient(backend="web")

    assert client.base_url == "https://api.zotero.org/groups/67890"
    assert client._auth_headers()["Zotero-API-Key"] == "from-environment"


def test_collection_listing_paginates(monkeypatch):
    client = ZoteroClient()
    pages = [
        [{"key": str(i), "data": {"name": f"Collection {i}"}} for i in range(100)],
        [{"key": "100", "data": {"name": "Collection 100"}}],
    ]
    starts = []

    def fake_get(path, params, *, use_cache):
        assert path == "/collections"
        assert use_cache is False
        starts.append(params["start"])
        return pages.pop(0)

    monkeypatch.setattr(client, "get", fake_get)

    assert len(client.collections()) == 101
    assert starts == [0, 100]


def test_web_item_recovers_better_bibtex_key_from_extra():
    record = ZoteroClient.to_record({
        "data": {
            "key": "ABCD1234",
            "itemType": "book",
            "title": "A Book",
            "extra": "Original Date: 2020\nCitation Key: author2020book",
        }
    })

    assert record is not None
    assert record.zotero_citekey == "author2020book"
