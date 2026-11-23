
from decimal import Decimal, InvalidOperation
from datetime import datetime
from collections import defaultdict

def solve(request: dict, evidence: list[dict]) -> dict:
    def parse_dt(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v)
            except Exception:
                return None
        return None

    def parse_decimal(v):
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except Exception:
            return None

    def dec_to_str(d):
        if d is None:
            return None
        if not isinstance(d, Decimal):
            d = parse_decimal(d)
        if d is None:
            return None
        if d == 0:
            return "0"
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s == "-0":
            s = "0"
        return s

    def norm_list(x):
        if x is None:
            return None
        if not isinstance(x, list):
            return None
        return tuple(sorted(str(i) for i in x))

    req_case_id = request.get("case_id")
    req_account_id = request.get("account_id")
    req_event_time = parse_dt(request.get("event_time"))
    req_knowledge_time = parse_dt(request.get("knowledge_time"))

    out = {
        "promotion_eligible": None,
        "net_billed": None,
        "support_correct": None,
        "plan_price": None,
    }

    # Filter records to this account/case when present
    scoped = []
    for r in evidence:
        if r.get("account_id") != req_account_id:
            continue
        if req_case_id is not None and r.get("case_id") != req_case_id:
            continue
        scoped.append(r)

    accounts = [r for r in scoped if r.get("kind") == "account"]
    if not accounts:
        return out

    # Choose a canonical account record (latest visible snapshot if multiple)
    def account_rank(r):
        return (
            parse_dt(r.get("knowledge_time")) or datetime.min,
            parse_dt(r.get("event_time")) or datetime.min,
        )

    account = max(accounts, key=account_rank)

    activation_time = parse_dt(account.get("activation_time"))
    account_currency = account.get("currency")
    plan_id = account.get("plan_id")
    sku = account.get("sku")
    segment = account.get("segment")
    lines_raw = account.get("lines")
    service_period = account.get("service_period")
    ledger_complete_through = parse_dt(account.get("ledger_complete_through"))

    # ---------- plan_price ----------
    def compute_plan_price():
        if plan_id is None or lines_raw is None:
            return None
        try:
            line_count = int(lines_raw)
        except Exception:
            return None

        plans = [
            r for r in scoped
            if r.get("kind") == "catalog_link" and r.get("catalog_kind") == "plan" and r.get("catalog_id") == plan_id
        ]
        if not plans:
            return None

        # Resolve same catalog_id conflicting records
        def plan_sig(r):
            return (
                tuple(r.get("eligible_lines")) if isinstance(r.get("eligible_lines"), list) else None,
                tuple(r.get("line_prices")) if isinstance(r.get("line_prices"), list) else None,
                r.get("title"),
            )

        sigs = {plan_sig(r) for r in plans}
        if len(sigs) > 1:
            return None

        plan = plans[0]
        eligible_lines = plan.get("eligible_lines")
        line_prices = plan.get("line_prices")
        if not isinstance(eligible_lines, list) or not isinstance(line_prices, list):
            return None
        if len(eligible_lines) != len(line_prices):
            return None

        matches = [i for i, ln in enumerate(eligible_lines) if ln == line_count]
        if not matches:
            return None

        # If the same line appears multiple times, all prices must agree
        prices = []
        for idx in matches:
            if idx >= len(line_prices):
                return None
            prices.append(parse_decimal(line_prices[idx]))
        if any(p is None for p in prices):
            return None
        if len({p for p in prices}) > 1:
            return None
        return dec_to_str(prices[0])

    out["plan_price"] = compute_plan_price()

    # ---------- promotion_eligible ----------
    def compute_promotion_eligible():
        if activation_time is None or sku is None or plan_id is None or segment is None:
            return None

        visible = []
        for r in scoped:
            if r.get("kind") != "promotion":
                continue
            recorded_at = parse_dt(r.get("recorded_at"))
            if recorded_at is None or req_knowledge_time is None or recorded_at > req_knowledge_time:
                continue
            if r.get("offer_id") is None or r.get("version") is None:
                return None
            visible.append(r)

        if not visible:
            return None

        by_offer = defaultdict(list)
        for r in visible:
            by_offer[r.get("offer_id")].append(r)

        any_evaluable = False
        any_eligible = False

        for offer_id, records in by_offer.items():
            # Highest visible version wins
            max_version = max(r.get("version") for r in records if r.get("version") is not None)
            top = [r for r in records if r.get("version") == max_version]
            if not top:
                continue

            def promo_sig(r):
                return (
                    parse_dt(r.get("valid_from")),
                    parse_dt(r.get("valid_to")),
                    norm_list(r.get("eligible_skus")),
                    norm_list(r.get("eligible_plans")),
                    norm_list(r.get("excluded_segments")),
                    parse_decimal(r.get("credit_amount")),
                    r.get("currency"),
                )

            sigs = {promo_sig(r) for r in top}
            if len(sigs) > 1:
                return None

            rec = top[0]
            vf = parse_dt(rec.get("valid_from"))
            vt = parse_dt(rec.get("valid_to"))
            eligible_skus = rec.get("eligible_skus")
            eligible_plans = rec.get("eligible_plans")
            excluded_segments = rec.get("excluded_segments")
            if vf is None or vt is None:
                continue
            if not isinstance(eligible_skus, list) or not isinstance(eligible_plans, list) or not isinstance(excluded_segments, list):
                continue

            any_evaluable = True
            if vf <= activation_time < vt and sku in eligible_skus and plan_id in eligible_plans and segment not in excluded_segments:
                any_eligible = True

        if not any_evaluable:
            return None
        return any_eligible

    out["promotion_eligible"] = compute_promotion_eligible()

    # ---------- net_billed ----------
    def compute_net_billed():
        if req_event_time is None or req_knowledge_time is None:
            return None
        if account_currency is None or service_period is None:
            return None
        if ledger_complete_through is None or ledger_complete_through < req_knowledge_time:
            return None

        bills = [
            r for r in scoped
            if r.get("kind") == "billing" and r.get("service_period") == service_period
        ]

        # Time filter first
        candidates = []
        for r in bills:
            pt = parse_dt(r.get("posted_at"))
            et = parse_dt(r.get("event_time"))
            if pt is None or et is None:
                return None
            if et > req_event_time or pt > req_knowledge_time:
                continue
            eid = r.get("event_id")
            if eid is None:
                return None
            amt = parse_decimal(r.get("amount"))
            ccy = r.get("currency")
            if amt is None or ccy is None:
                return None
            candidates.append(r)

        if not candidates:
            return "0"

        by_eid = defaultdict(list)
        for r in candidates:
            by_eid[r.get("event_id")].append(r)

        total = Decimal("0")
        for eid, records in by_eid.items():
            def bill_sig(r):
                return (
                    parse_decimal(r.get("amount")),
                    r.get("currency"),
                    parse_dt(r.get("event_time")),
                    parse_dt(r.get("posted_at")),
                    r.get("service_period"),
                )

            sigs = {bill_sig(r) for r in records}
            if len(sigs) > 1:
                return None

            rec = records[0]
            if rec.get("currency") != account_currency:
                continue
            amt = parse_decimal(rec.get("amount"))
            if amt is None:
                return None
            total += amt

        return dec_to_str(total)

    out["net_billed"] = compute_net_billed()

    # ---------- support_correct ----------
    def compute_support_correct():
        if account_currency is None:
            return None
        visible = []
        for r in scoped:
            if r.get("kind") != "support":
                continue
            recorded_at = parse_dt(r.get("recorded_at"))
            if recorded_at is None or req_knowledge_time is None or recorded_at > req_knowledge_time:
                continue
            if r.get("currency") != account_currency:
                continue
            if r.get("asserted_net") is None:
                return None
            if parse_decimal(r.get("asserted_net")) is None:
                return None
            visible.append(r)

        if not visible:
            return None

        asserted_values = {parse_decimal(r.get("asserted_net")) for r in visible}
        if None in asserted_values:
            return None
        if len(asserted_values) > 1:
            return None

        if out["net_billed"] is None:
            return None

        asserted = next(iter(asserted_values))
        verified = parse_decimal(out["net_billed"])
        if asserted is None or verified is None:
            return None
        return asserted == verified

    out["support_correct"] = compute_support_correct()

    return out
