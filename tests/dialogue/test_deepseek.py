import httpx

from services.dialogue.deepseek import DeepSeekClient


def _client(handler):
    return DeepSeekClient(api_key="test-key", transport=httpx.MockTransport(handler), timeout=0.01)


def test_deepseek_classifies_rate_limit_without_leaking_response():
    client = _client(lambda request: httpx.Response(429, request=request, json={"error": "quota"}))
    result = client.reply([{"role": "user", "content": "hello"}], "req-1")
    assert result["status"] == "rate_limited"
    assert result["text"] == ""
    assert result["request_id"] == "req-1"


def test_deepseek_classifies_timeout():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    assert _client(timeout).reply([{"role": "user", "content": "hello"}])["status"] == "timeout"


def test_deepseek_classifies_upstream_and_network_errors():
    upstream = _client(lambda request: httpx.Response(503, request=request))
    assert upstream.reply([{"role": "user", "content": "hello"}])["status"] == "upstream_error"

    def network(request):
        raise httpx.ConnectError("offline", request=request)

    assert _client(network).reply([{"role": "user", "content": "hello"}])["status"] == "network_error"

