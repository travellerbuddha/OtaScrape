import json

from otascrape.cli import main


def test_inspect_summarizes_capture(tmp_path, capsys):
    stem = tmp_path / "probe_x"
    body = {"data": {"accommodations": [{"deals": [{"advertiser": {"name": "Booking.com"}, "price": {"amount": 3450, "currency": "TRY"}},
                                                    {"advertiser": {"name": "Expedia"}, "price": {"amount": 3980, "currency": "TRY"}}]}]}}
    stem.with_suffix(".json").write_text(json.dumps([
        {"source": {"url": "https://x/graphql", "method": "POST", "post": '{"operationName":"AccommodationSearch"}'}, "body": body},
        {"source": {}, "body": {"unrelated": 1}}]))
    stem.with_suffix(".html").write_text('<html><script id="s1">var a={"advertiser":"x","price":1}</script><p>Fiyat ₺ 3.450</p></html>')
    assert main(["inspect", str(stem.with_suffix(".json"))]) == 0
    out = capsys.readouterr().out
    assert "op=AccommodationSearch" in out
    assert "data.accommodations[].deals[].price.amount  x2  örn: 3450 | 3980" in out
    assert "data.accommodations[].deals[].advertiser.name  x2" in out and "Booking.com" in out
    assert "<script id=s1" in out and "₺ 3.450" in out


def test_inspect_old_json_format(tmp_path, capsys):
    stem = tmp_path / "old"
    stem.with_suffix(".json").write_text(json.dumps([{"deal": {"price": 5}}]))
    assert main(["inspect", str(stem)]) == 0
    assert "deal.price" in capsys.readouterr().out
