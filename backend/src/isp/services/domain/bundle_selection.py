"""Bounded bundle selection using catalog prices and Decimal arithmetic."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import product
import re

_PATTERN = re.compile(
    r'\s+(?:for\s+)?(?P<op>under|below|less than|up to|at most|no more than)\s+'
    r'\$(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)\s+(?:in\s+)?'
    r'(?:total|combined|altogether)\s*[.!?]?$', re.I,
)


@dataclass(frozen=True)
class Budget:
    amount: Decimal
    inclusive: bool

    def allows(self, amount: Decimal) -> bool:
        return amount <= self.amount if self.inclusive else amount < self.amount


def extract_budget(query: str) -> tuple[str, Budget | None]:
    match = _PATTERN.search(query)
    if not match:
        return query, None
    return query[:match.start()].strip(), Budget(
        Decimal(match['amount'].replace(',', '')),
        match['op'].lower() in {'up to', 'at most', 'no more than'},
    )


def _price(card: dict) -> Decimal | None:
    # Only the numeric indexed price, never a formatted/monthly payment string.
    value = card.get('bundle_price')
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        price = Decimal(str(value))
        return price if price.is_finite() and price >= 0 else None
    except InvalidOperation:
        return None


def select_bundles(groups: list[list[dict]], budget: Budget, limit: int = 3):
    """One distinct product per intent, ranked by total then retrieval order.

    The caller retrieves up to five cheapest candidates per intent; at most four
    intents keeps the Cartesian search bounded to 625 combinations.
    """
    if not 2 <= len(groups) <= 4:
        return []
    priced = [[(c, p) for c in group[:5] if c.get('id') and (p := _price(c)) is not None]
              for group in groups]
    found = []
    seen = set()
    for combo in product(*priced):
        ids = tuple(c['id'] for c, _ in combo)
        if len(set(ids)) != len(ids) or ids in seen:
            continue
        seen.add(ids)
        total = sum((p for _, p in combo), Decimal('0'))
        if budget.allows(total):
            found.append(([dict(c) for c, _ in combo], total))
    found.sort(key=lambda item: item[1])
    return found[:limit]


def render_bundles(groups: list[list[dict]], budget: Budget) -> tuple[list[dict], str]:
    bundles = select_bundles(groups, budget)
    prefix = 'Bundle selection: '
    if not bundles:
        return [], prefix + 'No qualifying bundle found among the retrieved products with known prices. Try a higher total budget.'
    cards, lines = [], []
    for i, (items, total) in enumerate(bundles, 1):
        label = f'Bundle {i} — ${total:.2f} total'
        lines.append(label + ': ' + ' + '.join(c.get('title', '') for c in items))
        for card in items:
            card.update(label=label, bundle_id=f'bundle-{i}', bundle_total=f'{total:.2f}')
            cards.append(card)
    return cards, prefix + '\n\n'.join(lines) + '\n\nTotals use catalog prices before tax and shipping; no monthly financing amounts.'
