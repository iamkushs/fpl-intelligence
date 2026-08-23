import asyncio

import pytest

from fpl_intelligence.research.web_retrieval import PageRetrievalError, ScraplingPageRetriever


class Response:
    def __init__(self, text, *, body=None, status=200, url="https://example.com/final", headers=None):
        self.text, self.status, self.url = text, status, url
        self.body = body
        self.headers = headers or {"content-type": "text/html"}


class Static:
    def __init__(self, response): self.response, self.calls = response, 0
    async def get(self, url, **kwargs): self.calls += 1; return self.response


class Dynamic:
    def __init__(self, response): self.response, self.calls = response, 0
    def fetch(self, url, **kwargs): self.calls += 1; return self.response


def test_static_success_cleans_content_and_metadata():
    page = ScraplingPageRetriever(static_client=Static(Response("<html><head><meta property='og:title' content='Title'><meta property='article:published_time' content='2026-01-01'></head><body><nav>ignore</nav><article>Useful article content " + "word " * 30 + "</article><footer>ignore</footer><script>ignore</script></body></html>")), minimum_text_length=20).retrieve_page("https://example.com/a")
    assert page.fetch_mode == "static" and page.title == "Title" and page.published_at_raw == "2026-01-01"
    assert "ignore" not in page.text and len(page.content_hash) == 64 and page.retrieved_at.tzinfo is not None


def test_static_success_uses_scrapling_byte_body_when_text_handler_is_empty():
    markup = ("<main>Useful static content " + "word " * 30 + "</main>").encode()
    page = ScraplingPageRetriever(static_client=Static(Response("", body=markup)), minimum_text_length=20).retrieve_page("https://example.com/a")
    assert "Useful static content" in page.text


def test_js_shell_uses_dynamic_when_enabled():
    dynamic = Dynamic(Response("<main>" + "usable " * 30 + "</main>"))
    page = ScraplingPageRetriever(static_client=Static(Response("<body>Loading</body>")), dynamic_client=dynamic, dynamic_fallback=True, minimum_text_length=20).retrieve_page("https://example.com/a")
    assert page.fetch_mode == "dynamic" and dynamic.calls == 1


def test_access_denial_never_uses_dynamic():
    dynamic = Dynamic(Response("<main>content</main>"))
    with pytest.raises(PageRetrievalError, match="blocked"):
        ScraplingPageRetriever(static_client=Static(Response("denied", status=403)), dynamic_client=dynamic, dynamic_fallback=True).retrieve_page("https://example.com/a")
    assert dynamic.calls == 0


@pytest.mark.parametrize("url", ["http://localhost/a", "http://127.0.0.1/a", "file:///tmp/a", "http://10.0.0.1/a"])
def test_unsafe_url_is_rejected_without_fetch(url):
    static = Static(Response("<main>content</main>"))
    with pytest.raises(PageRetrievalError, match="invalid_url"):
        ScraplingPageRetriever(static_client=static).retrieve_page(url)
    assert static.calls == 0


def test_static_client_is_bounded_even_when_it_ignores_timeout():
    class HangingStatic:
        async def get(self, url, **kwargs):
            await asyncio.sleep(1)

    with pytest.raises(PageRetrievalError, match="timeout"):
        ScraplingPageRetriever(static_client=HangingStatic(), timeout_seconds=0.01).retrieve_page("https://example.com/a")


def test_failed_static_url_does_not_prevent_next_static_success():
    class SequenceStatic:
        def __init__(self): self.responses = [Response(""), Response("", body=("<main>usable " + "word " * 30 + "</main>").encode())]
        async def get(self, url, **kwargs): return self.responses.pop(0)

    retriever = ScraplingPageRetriever(static_client=SequenceStatic(), minimum_text_length=20)
    with pytest.raises(PageRetrievalError, match="empty_content"):
        retriever.retrieve_page("https://example.com/failed")
    assert "usable" in retriever.retrieve_page("https://example.com/succeeds").text
