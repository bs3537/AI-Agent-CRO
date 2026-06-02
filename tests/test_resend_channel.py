"""W7 tests — Resend email channel + channel selection.

All offline: the Resend HTTP call is exercised through an injected fake client
(ResendChannel._post accepts `client=`), so no network and no real email.
"""
from __future__ import annotations

import pytest

from sma_monitor.outputs.channels import ResendChannel, ResendError, _split_recipients


# Minimal stand-in for an httpx.Response.
class _Resp:
    def __init__(self, status: int, payload: dict | None = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


# Minimal stand-in for an httpx.Client that records the POST it receives.
class _FakeClient:
    def __init__(self, resp: _Resp):
        self.resp = resp
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.resp


# A 2xx POST returns the message id and sends the expected multi-recipient,
# HTML+text payload with a bearer-auth header.
def test_resend_post_payload():
    ch = ResendChannel(
        api_key="re_test",
        from_addr="AI CRO <onboarding@resend.dev>",
        to_addrs=["a@x.com", "b@y.com"],
    )
    fake = _FakeClient(_Resp(200, {"id": "em_123"}))
    rid = ch._post(subject="S", html="<b>hi</b>", text="hi", client=fake)
    assert rid == "em_123"
    call = fake.calls[0]
    assert call["url"] == ResendChannel.RESEND_ENDPOINT
    assert call["headers"]["Authorization"] == "Bearer re_test"
    payload = call["json"]
    assert payload["to"] == ["a@x.com", "b@y.com"]
    assert payload["from"] == "AI CRO <onboarding@resend.dev>"
    assert payload["subject"] == "S"
    assert payload["html"] == "<b>hi</b>"
    assert payload["text"] == "hi"


# A non-2xx response raises ResendError so the assembler can log + continue.
def test_resend_raises_on_error():
    ch = ResendChannel(api_key="re_test", from_addr="x", to_addrs=["a@x.com"])
    fake = _FakeClient(_Resp(422, text="invalid from"))
    with pytest.raises(ResendError):
        ch._post(subject="S", text="hi", client=fake)


# send_risk_brief routes both the HTML and text bodies into _post.
def test_send_risk_brief_routes(monkeypatch):
    ch = ResendChannel(api_key="k", from_addr="f", to_addrs=["a@x.com"])
    captured: dict = {}
    monkeypatch.setattr(ch, "_post", lambda **kw: captured.update(kw) or "id")
    ch.send_risk_brief("2026-06-02", "Subj", "<h1>x</h1>", "x")
    assert captured["subject"] == "Subj"
    assert captured["html"] == "<h1>x</h1>"
    assert captured["text"] == "x"


# Comma/semicolon recipient strings split into a clean list.
def test_split_recipients():
    assert _split_recipients("a@x.com, b@y.com") == ["a@x.com", "b@y.com"]
    assert _split_recipients("a@x.com;b@y.com") == ["a@x.com", "b@y.com"]
    assert _split_recipients(" solo@x.com ") == ["solo@x.com"]
    assert _split_recipients(" , ,") == []


# When a Resend key is configured, build_channels uses Resend and NOT SMTP, with
# the recipient list split out. (settings is mutated via monkeypatch + restored.)
def test_build_channels_prefers_resend(monkeypatch):
    from sma_monitor.outputs import channels as ch_mod

    monkeypatch.setattr(ch_mod.settings, "resend_api_key", "re_x")
    monkeypatch.setattr(ch_mod.settings, "resend_email_from", "AI CRO <onboarding@resend.dev>")
    monkeypatch.setattr(ch_mod.settings, "resend_email_to", "a@x.com, b@y.com")
    monkeypatch.setattr(ch_mod.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(ch_mod.settings, "smtp_username", "u")
    monkeypatch.setattr(ch_mod.settings, "smtp_password", "p")
    monkeypatch.setattr(ch_mod.settings, "alert_email_from", "f@x.com")
    monkeypatch.setattr(ch_mod.settings, "alert_email_to", "t@x.com")

    built = ch_mod.build_channels()
    names = [c.name for c in built]
    assert "resend" in names
    assert "email" not in names  # Resend supersedes SMTP
    resend = next(c for c in built if c.name == "resend")
    assert resend.to_addrs == ["a@x.com", "b@y.com"]
