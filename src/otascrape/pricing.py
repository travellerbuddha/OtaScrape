from __future__ import annotations


def per_night(total: float, nights: int) -> float:
    return total / nights


def party_size(adults: int, children: int, basis: str) -> int:
    """`basis`: 'adults' (yalnız yetişkin) veya 'all_guests' (yetişkin + çocuk)."""
    if basis == "adults":
        return adults
    if basis == "all_guests":
        return adults + children
    raise ValueError(f"Bilinmeyen pax_basis: {basis!r}")


def per_person_night(total: float, nights: int, adults: int, children: int, basis: str) -> float:
    return total / nights / party_size(adults, children, basis)


def convert(amount: float, currency: str, base_currency: str, fx: dict[str, float]) -> float | None:
    """`fx[X]` = 1 birim X'in `base_currency` karşılığı. Kur yoksa None."""
    if currency == base_currency:
        return amount
    rate = fx.get(currency)
    return None if rate is None else amount * rate
