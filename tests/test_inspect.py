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


def test_inspect_dom_diagnostics(tmp_path, capsys):
    stem = tmp_path / "d"
    stem.with_suffix(".json").write_text("[]")
    stem.with_suffix(".html").write_text(
        '<html><title>Hotel X</title><h1>Hotel X</h1><body><div data-testid="deal-list"><section data-testid="deal-row">'
        '<span data-testid="advertiser-name">Booking.com</span><span>₺15,549</span><span>₺93,293 total</span></section></div>'
        '<p>21 Oct - 28 Oct</p></body></html>')
    assert main(["inspect", str(stem)]) == 0
    out = capsys.readouterr().out
    assert "h1: Hotel X" in out and "21 Oct - 28 Oct" in out
    assert "deal-rowx1" in out and "advertiser-namex1" in out
    assert "span < section[deal-row] < div[deal-list]" in out and "Booking.com | ₺15,549 | ₺93,293 total" in out
