
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from collections import defaultdict

def solve(request: dict, evidence: list[dict]) -> dict:
    def parse_dt(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        return None

    def dec(value):
        if value is None:
            return None
        try:
            if isinstance(value, Decimal):
                return value
            return Decimal(str(value))
        except Exception:
            return None

    def dec_str(value):
        if value is None:
            return None
        d = dec(value)
        if d is None:
            return None
        if d == 0:
            return "0"
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s

    req_account_id = request.get("account_id")
    req_case_id = request.get("case_id")
    event_time = parse_dt(request.get("event_time"))
    knowledge_time = parse_dt(request.get("knowledge_time"))

    # Filter to relevant records
    rel = []
    for r in evidence or []:
        if req_account_id is not None and r.get("account_id") != req_account_id:
            continue
        if req_case_id is not None and r.get("case_id") != req_case_id:
            continue
        rel.append(r)

    # ---------- Account ----------
    account_candidates = []
    for r in rel:
        if r.get("kind") != "account":
            continue
        rt = parse_dt(r.get("recorded_at"))
        if rt is not None and knowledge_time is not None and rt > knowledge_time:
            continue
        account_candidates.append((rt or parse_dt(r.get("event_time")) or datetime.min.replace(tzinfo=timezone.utc), r))
    account = None
    if account_candidates:
        account_candidates.sort(key=lambda x: (x[0], x[1].get("id", "")))
        account = account_candidates[-1][1]

    # ---------- Promotion ----------
    promo_result = None
    promo_recs = []
    for r in rel:
        if r.get("kind") != "promotion":
            continue
        rt = parse_dt(r.get("recorded_at"))
        if rt is not None and knowledge_time is not None and rt > knowledge_time:
            continue
        promo_recs.append(r)

    if not account or not promo_recs:
        promo_result = None
    else:
        # Highest visible version wins
        version_groups = defaultdict(list)
        for r in promo_recs:
            v = r.get("version")
            version_groups[v].append(r)

        # Choose max version among visible promotion records
        def version_key(v):
            try:
                return int(v)
            except Exception:
                return float("-inf")

        max_version = max(version_groups.keys(), key=version_key)
        chosen = version_groups[max_version]

        # Conflicting same-version authoritative offers require null
        def promo_sig(r):
            return (
                r.get("offer_id"),
                r.get("version"),
                r.get("valid_from"),
                r.get("valid_to"),
                tuple(r.get("eligible_skus") or []),
                tuple(r.get("eligible_plans") or []),
                tuple(r.get("excluded_segments") or []),
                str(r.get("credit_amount")) if r.get("credit_amount") is not None else None,
                r.get("currency"),
            )

        sigs = {promo_sig(r) for r in chosen}
        if len(sigs) > 1:
            promo_result = None
        else:
            offer = chosen[0]
            # Required fields
            activation_time = parse_dt(account.get("activation_time"))
            valid_from = parse_dt(offer.get("valid_from"))
            valid_to = parse_dt(offer.get("valid_to"))
            sku = account.get("sku")
            plan_id = account.get("plan_id")
            segment = account.get("segment")
            eligible_skus = offer.get("eligible_skus")
            eligible_plans = offer.get("eligible_plans")
            excluded_segments = offer.get("excluded_segments")

            if (
                activation_time is None
                or valid_from is None
                or valid_to is None
                or sku is None
                or plan_id is None
                or segment is None
                or eligible_skus is None
                or eligible_plans is None
                or excluded_segments is None
            ):
                promo_result = None
            else:
                in_window = (activation_time >= valid_from) and (activation_time < valid_to)
                sku_ok = sku in eligible_skus
                plan_ok = plan_id in eligible_plans
                segment_ok = segment not in excluded_segments
                promo_result = bool(in_window and sku_ok and plan_ok and segment_ok)

    # ---------- Net billed ----------
    net_billed = None
    account_currency = account.get("currency") if account else None
    service_period = account.get("service_period") if account else None
    ledger_complete_through = parse_dt(account.get("ledger_complete_through")) if account else None

    if (
        account is None
        or event_time is None
        or knowledge_time is None
        or account_currency is None
        or service_period is None
        or ledger_complete_through is None
        or ledger_complete_through < knowledge_time
    ):
        net_billed = None
    else:
        # Candidate billing records
        billing = []
        for r in rel:
            if r.get("kind") != "billing":
                continue
            if r.get("service_period") != service_period:
                continue
            rt_event = parse_dt(r.get("event_time"))
            rt_posted = parse_dt(r.get("posted_at"))
            if rt_event is None or rt_posted is None:
                continue
            if rt_event > event_time or rt_posted > knowledge_time:
                continue
            billing.append(r)

        # Deduplicate by business event_id
        grouped = defaultdict(list)
        for r in billing:
            eid = r.get("event_id")
            key = eid if eid is not None else r.get("id")
            grouped[key].append(r)

        conflict = False
        total = Decimal("0")
        for key, items in grouped.items():
            # identical business copies only
            canonical = None
            for r in items:
                sig = (
                    r.get("amount"),
                    r.get("currency"),
                    r.get("event_time"),
                    r.get("posted_at"),
                    r.get("service_period"),
                )
                if canonical is None:
                    canonical = sig
                elif sig != canonical:
                    conflict = True
                    break
            if conflict:
                break

            r = items[0]
            cur = r.get("currency")
            amt = dec(r.get("amount"))
            if cur is None or cur != account_currency or amt is None:
                conflict = True
                break
            total += amt

        if conflict:
            net_billed = None
        else:
            net_billed = dec_str(total)

    # ---------- Support correctness ----------
    support_correct = None
    support_recs = []
    for r in rel:
        if r.get("kind") != "support":
            continue
        rt = parse_dt(r.get("recorded_at"))
        if rt is None or knowledge_time is None or rt > knowledge_time:
            continue
        support_recs.append(r)

    if account is None or net_billed is None or not support_recs:
        support_correct = None
    else:
        # Pick latest visible support note
        support_recs.sort(key=lambda r: (parse_dt(r.get("recorded_at")) or datetime.min.replace(tzinfo=timezone.utc), r.get("id", "")))
        support = support_recs[-1]
        sup_currency = support.get("currency")
        asserted_net = dec(support.get("asserted_net"))
        account_currency = account.get("currency")
        if sup_currency is None or account_currency is None or sup_currency != account_currency or asserted_net is None:
            support_correct = None
        else:
            support_correct = (asserted_net == dec(net_billed))

    # ---------- Plan price ----------
    plan_price = None
    if account is not None:
        plan_id = account.get("plan_id")
        line_count = account.get("lines")
        if plan_id is not None and line_count is not None:
            plan_recs = []
            for r in rel:
                if r.get("kind") != "catalog_link":
                    continue
                if r.get("catalog_kind") != "plan":
                    continue
                if r.get("catalog_id") != plan_id:
                    continue
                rt = parse_dt(r.get("recorded_at"))
                if rt is not None and knowledge_time is not None and rt > knowledge_time:
                    continue
                plan_recs.append((rt or datetime.min.replace(tzinfo=timezone.utc), r))
            if plan_recs:
                plan_recs.sort(key=lambda x: (x[0], x[1].get("id", "")))
                plan = plan_recs[-1][1]
                eligible_lines = plan.get("eligible_lines")
                line_prices = plan.get("line_prices")
                try:
                    line_int = int(line_count)
                except Exception:
                    line_int = None
                if (
                    line_int is not None
                    and isinstance(eligible_lines, list)
                    and isinstance(line_prices, list)
                    and len(eligible_lines) == len(line_prices)
                    and line_int in eligible_lines
                ):
                    idx = eligible_lines.index(line_int)
                    if idx < len(line_prices):
                        plan_price = str(line_prices[idx]) if line_prices[idx] is not None else None

    return {
        "promotion_eligible": promo_result,
        "net_billed": net_billed,
        "support_correct": support_correct,
        "plan_price": plan_price,
    }
