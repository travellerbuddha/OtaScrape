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


def test_inspect_block_skeleton(tmp_path, capsys):
    stem = tmp_path / "b"
    deals = {"data": {"accommodationDeals": {"accommodationId": 151839, "deals": [
        {"advertiser": {"id": 395, "name": "Agoda"}, "pricePerStayObject": {"amount": 82268, "eurocents": 149000}},
        {"advertiser": {"id": 7, "name": "Hotel Site"}, "pricePerStayObject": {"amount": 93293, "eurocents": 170000}},
        {"advertiser": {"id": 9, "name": "Expedia"}}]}}}
    stem.with_suffix(".json").write_text(json.dumps([
        {"source": {"url": "x", "method": "POST", "post": '{"operationName":"other"}'}, "body": {"a": 1}},
        {"source": {"url": "https://t/graphql?accommodationDealsQuery", "method": "POST",
                    "post": '{"variables":{"dateRange":"2026-10-12/2026-10-18"}}'}, "body": deals}]))
    assert main(["inspect", str(stem), "--block", "1", "--block", "9"]) == 0
    out = capsys.readouterr().out
    assert "accommodationDealsQuery" in out and '"dateRange":"2026-10-12/2026-10-18"' in out
    assert '"name": "Agoda"' in out and '"name": "Hotel Site"' in out
    assert "…(+1 öğe daha)" in out and "böyle bir blok yok" in out
