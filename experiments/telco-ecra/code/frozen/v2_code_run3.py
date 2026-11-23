
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from collections import defaultdict

def solve(request: dict, evidence: list[dict]) -> dict:
    def parse_dt(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value))
            except Exception:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def parse_dec(value):
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def dec_to_str(value):
        if value is None:
            return None
        if not isinstance(value, Decimal):
            value = parse_dec(value)
            if value is None:
                return None
        if value == 0:
            return "0"
        s = format(value.normalize(), "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s == "-0":
            s = "0"
        return s

    def same_case(rec):
        req_case = request.get("case_id")
        req_acct = request.get("account_id")
        if req_acct is not None and rec.get("account_id") != req_acct:
            return False
        if req_case is not None and rec.get("case_id") is not None and rec.get("case_id") != req_case:
            return False
        return True

    req_event_time = parse_dt(request.get("event_time"))
    req_knowledge_time = parse_dt(request.get("knowledge_time"))
    req_account_id = request.get("account_id")
    req_case_id = request.get("case_id")

    account_recs = [r for r in evidence if r.get("kind") == "account" and same_case(r)]
    account = None
    if account_recs:
        # Prefer the first matching account record; benchmark data is expected to have one.
        account = account_recs[0]

    # Default outputs
    promotion_eligible = None
    net_billed = None
    support_correct = None
    plan_price = None

    acct_currency = account.get("currency") if account else None
    acct_service_period = account.get("service_period") if account else None
    acct_activation_time = parse_dt(account.get("activation_time")) if account else None
    acct_ledger_complete_through = parse_dt(account.get("ledger_complete_through")) if account else None
    acct_sku = account.get("sku") if account else None
    acct_plan_id = account.get("plan_id") if account else None
    acct_segment = account.get("segment") if account else None
    acct_lines = account.get("lines") if account else None

    # --- Promotion eligibility ---
    promo_recs = [r for r in evidence if r.get("kind") == "promotion" and same_case(r)]
    promo_visible = []
    if req_knowledge_time is not None:
        for r in promo_recs:
            rec_at = parse_dt(r.get("recorded_at"))
            if rec_at is not None and rec_at <= req_knowledge_time:
                promo_visible.append(r)

    promo_result = None
    if account is not None:
        if acct_activation_time is None:
            promo_result = None
        elif not promo_visible:
            promo_result = None
        else:
            grouped = defaultdict(list)
            for r in promo_visible:
                offer_id = r.get("offer_id")
                ver = r.get("version")
                if offer_id is None or ver is None:
                    promo_result = None
                    break
                try:
                    ver_int = int(ver)
                except Exception:
                    promo_result = None
                    break
                grouped[offer_id].append((ver_int, r))
            else:
                candidate_results = []
                for offer_id, items in grouped.items():
                    max_ver = max(v for v, _ in items)
                    top = [r for v, r in items if v == max_ver]
                    if not top:
                        promo_result = None
                        break
                    # Conflicting same-version records require null
                    def promo_canon(r):
                        return (
                            r.get("offer_id"),
                            int(r.get("version")) if r.get("version") is not None else None,
                            str(r.get("recorded_at")),
                            str(r.get("valid_from")),
                            str(r.get("valid_to")),
                            tuple(r.get("eligible_skus", [])) if isinstance(r.get("eligible_skus"), list) else None,
                            tuple(r.get("eligible_plans", [])) if isinstance(r.get("eligible_plans"), list) else None,
                            tuple(r.get("excluded_segments", [])) if isinstance(r.get("excluded_segments"), list) else None,
                            str(r.get("credit_amount")) if r.get("credit_amount") is not None else None,
                            r.get("currency"),
                        )

                    canon_set = {promo_canon(r) for r in top}
                    if len(canon_set) > 1:
                        promo_result = None
                        break

                    r = top[0]
                    valid_from = parse_dt(r.get("valid_from"))
                    valid_to = parse_dt(r.get("valid_to"))
                    eligible_skus = r.get("eligible_skus")
                    eligible_plans = r.get("eligible_plans")
                    excluded_segments = r.get("excluded_segments")

                    if (
                        valid_from is None or valid_to is None or
                        acct_sku is None or acct_plan_id is None or acct_segment is None or
                        not isinstance(eligible_skus, list) or
                        not isinstance(eligible_plans, list) or
                        not isinstance(excluded_segments, list)
                    ):
                        candidate_results.append(None)
                        continue

                    eligible = (
                        (valid_from <= acct_activation_time < valid_to) and
                        (acct_sku in eligible_skus) and
                        (acct_plan_id in eligible_plans) and
                        (acct_segment not in excluded_segments)
                    )
                    candidate_results.append(bool(eligible))

                if promo_result is None:
                    pass
                elif not candidate_results:
                    promo_result = None
                else:
                    if all(v is True for v in candidate_results):
                        promo_result = True
                    elif all(v is False for v in candidate_results):
                        promo_result = False
                    else:
                        promo_result = None

    promotion_eligible = promo_result

    # --- Net billed ---
    net_result = None
    if (
        account is not None and
        acct_currency is not None and
        acct_service_period is not None and
        acct_ledger_complete_through is not None and
        req_knowledge_time is not None and
        req_event_time is not None and
        acct_ledger_complete_through >= req_knowledge_time
    ):
        billing_recs = [
            r for r in evidence
            if r.get("kind") == "billing" and same_case(r) and r.get("service_period") == acct_service_period
        ]

        visible_bills = []
        invalid_billing = False
        for r in billing_recs:
            if r.get("currency") != acct_currency:
                invalid_billing = True
                break
            amt = parse_dec(r.get("amount"))
            event_t = parse_dt(r.get("event_time"))
            posted_t = parse_dt(r.get("posted_at"))
            event_id = r.get("event_id")
            if amt is None or event_t is None or posted_t is None or event_id is None:
                invalid_billing = True
                break
            if event_t <= req_event_time and posted_t <= req_knowledge_time:
                visible_bills.append(r)

        if not invalid_billing:
            groups = defaultdict(list)
            for r in visible_bills:
                groups[r.get("event_id")].append(r)

            deduped = []
            for event_id, items in groups.items():
                def bill_canon(r):
                    return (
                        r.get("event_id"),
                        str(r.get("amount")),
                        r.get("currency"),
                        str(r.get("event_time")),
                        str(r.get("posted_at")),
                        r.get("service_period"),
                    )
                canon_set = {bill_canon(r) for r in items}
                if len(canon_set) > 1:
                    invalid_billing = True
                    break
                deduped.append(items[0])

            if not invalid_billing:
                total = Decimal("0")
                for r in deduped:
                    total += parse_dec(r.get("amount"))
                net_result = total

    net_billed = dec_to_str(net_result) if net_result is not None else None

    # --- Support correctness ---
    support_result = None
    support_recs = [
        r for r in evidence
        if r.get("kind") == "support" and same_case(r)
    ]

    verified_net = net_result
    if account is not None and acct_currency is not None and req_knowledge_time is not None and verified_net is not None:
        support_candidates = []
        for r in support_recs:
            rec_at = parse_dt(r.get("recorded_at"))
            currency = r.get("currency")
            asserted = parse_dec(r.get("asserted_net"))
            if rec_at is None or currency != acct_currency or asserted is None:
                continue
            if rec_at <= req_knowledge_time:
                support_candidates.append((asserted, r))

        if support_candidates:
            asserted_values = {a for a, _ in support_candidates}
            if len(asserted_values) == 1:
                asserted_net = next(iter(asserted_values))
                support_result = (asserted_net == verified_net)
            else:
                support_result = None
        else:
            support_result = None

    support_correct = support_result

    # --- Plan price ---
    plan_price_result = None
    if account is not None and acct_plan_id is not None and acct_lines is not None:
        plan_recs = [
            r for r in evidence
            if r.get("kind") == "catalog_link"
            and r.get("catalog_kind") == "plan"
            and same_case(r)
            and r.get("catalog_id") == acct_plan_id
        ]
        if plan_recs:
            # If multiple identical plan records exist, accept; conflicting records -> null.
            def plan_canon(r):
                return (
                    r.get("catalog_id"),
                    tuple(r.get("eligible_lines", [])) if isinstance(r.get("eligible_lines"), list) else None,
                    tuple(r.get("line_prices", [])) if isinstance(r.get("line_prices"), list) else None,
                    r.get("title"),
                )

            canon_set = {plan_canon(r) for r in plan_recs}
            if len(canon_set) == 1:
                plan = plan_recs[0]
                eligible_lines = plan.get("eligible_lines")
                line_prices = plan.get("line_prices")
                try:
                    acct_line_count = int(acct_lines)
                except Exception:
                    acct_line_count = None

                if (
                    isinstance(eligible_lines, list)
                    and isinstance(line_prices, list)
                    and acct_line_count is not None
                    and len(eligible_lines) == len(line_prices)
                ):
                    seen = {}
                    conflict = False
                    for idx, line in enumerate(eligible_lines):
                        try:
                            line_int = int(line)
                        except Exception:
                            conflict = True
                            break
                        price = line_prices[idx]
                        if line_int in seen:
                            if str(seen[line_int]) != str(price):
                                conflict = True
                                break
                        else:
                            seen[line_int] = price

                    if not conflict and acct_line_count in seen:
                        plan_price_result = parse_dec(seen[acct_line_count])

    plan_price = dec_to_str(plan_price_result) if plan_price_result is not None else None

    return {
        "promotion_eligible": promotion_eligible,
        "net_billed": net_billed,
        "support_correct": support_correct,
        "plan_price": plan_price,
    }
