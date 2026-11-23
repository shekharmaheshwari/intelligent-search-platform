
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

def solve(request: dict, evidence: list[dict]) -> dict:
    def parse_dt(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if not isinstance(v, str):
            return None
        s = v.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def dec(v):
        try:
            if isinstance(v, Decimal):
                return v
            return Decimal(str(v))
        except Exception:
            return None

    def dec_str(v):
        d = dec(v)
        if d is None:
            return None
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s == "-0":
            s = "0"
        return s

    req_account_id = request.get("account_id")
    req_case_id = request.get("case_id")
    req_event_time = parse_dt(request.get("event_time"))
    req_knowledge_time = parse_dt(request.get("knowledge_time"))

    if req_account_id is None or req_event_time is None or req_knowledge_time is None:
        return {
            "promotion_eligible": None,
            "net_billed": None,
            "support_correct": None,
            "plan_price": None,
        }

    def same_req(r):
        if r.get("account_id") != req_account_id:
            return False
        if req_case_id is not None and r.get("case_id") != req_case_id:
            return False
        return True

    def visible_at(record):
        # Earliest meaningful visibility timestamp for a record
        for key in ("recorded_at", "knowledge_time", "posted_at", "event_time"):
            dt = parse_dt(record.get(key))
            if dt is not None:
                return dt
        return None

    def visible(record):
        dt = visible_at(record)
        return dt is None or dt <= req_knowledge_time

    # Account record selection
    account_records = [r for r in evidence if r.get("kind") == "account" and same_req(r) and visible(r)]
    account = None
    if account_records:
        def acct_key(r):
            # Prefer latest knowledge_time/recorded_at/event_time among visible records
            ts = None
            for key in ("knowledge_time", "recorded_at", "event_time"):
                dt = parse_dt(r.get(key))
                if dt is not None and dt <= req_knowledge_time:
                    if ts is None or dt > ts:
                        ts = dt
            return ts or datetime.min.replace(tzinfo=timezone.utc)

        account_records.sort(key=acct_key, reverse=True)
        top = acct_key(account_records[0])
        same_top = [r for r in account_records if acct_key(r) == top]
        if len(same_top) == 1:
            account = same_top[0]
        else:
            # If multiple equally latest account snapshots differ in meaningful fields, ambiguous.
            def acct_sig(r):
                return (
                    r.get("sku"),
                    r.get("plan_id"),
                    r.get("segment"),
                    r.get("lines"),
                    r.get("activation_time"),
                    r.get("currency"),
                    r.get("service_period"),
                    r.get("ledger_complete_through"),
                )
            sigs = {acct_sig(r) for r in same_top}
            if len(sigs) == 1:
                account = same_top[0]
            else:
                account = None

    # Promotion eligibility
    promotion_eligible = None
    if account is not None:
        activation_time = parse_dt(account.get("activation_time"))
        sku = account.get("sku")
        plan_id = account.get("plan_id")
        segment = account.get("segment")
        if activation_time is None or sku is None or plan_id is None or segment is None:
            promotion_eligible = None
        else:
            promo_records = [r for r in evidence if r.get("kind") == "promotion" and same_req(r) and visible(r)]
            if not promo_records:
                promotion_eligible = None
            else:
                offer_ids = {r.get("offer_id") for r in promo_records if r.get("offer_id") is not None}
                if len(offer_ids) != 1:
                    promotion_eligible = None
                else:
                    offer_id = next(iter(offer_ids))
                    offer_records = [r for r in promo_records if r.get("offer_id") == offer_id and r.get("version") is not None]
                    if not offer_records:
                        promotion_eligible = None
                    else:
                        def ver_int(x):
                            try:
                                return int(x)
                            except Exception:
                                return None
                        versions = [ver_int(r.get("version")) for r in offer_records]
                        if any(v is None for v in versions):
                            promotion_eligible = None
                        else:
                            max_ver = max(versions)
                            top_version_records = [r for r in offer_records if ver_int(r.get("version")) == max_ver]

                            def promo_sig(r):
                                return (
                                    r.get("offer_id"),
                                    r.get("version"),
                                    r.get("valid_from"),
                                    r.get("valid_to"),
                                    tuple(r.get("eligible_skus")) if isinstance(r.get("eligible_skus"), list) else None,
                                    tuple(r.get("eligible_plans")) if isinstance(r.get("eligible_plans"), list) else None,
                                    tuple(r.get("excluded_segments")) if isinstance(r.get("excluded_segments"), list) else None,
                                    r.get("credit_amount"),
                                    r.get("currency"),
                                )

                            sigs = {promo_sig(r) for r in top_version_records}
                            if len(sigs) != 1:
                                promotion_eligible = None
                            else:
                                offer = top_version_records[0]
                                valid_from = parse_dt(offer.get("valid_from"))
                                valid_to = parse_dt(offer.get("valid_to"))
                                eligible_skus = offer.get("eligible_skus")
                                eligible_plans = offer.get("eligible_plans")
                                excluded_segments = offer.get("excluded_segments")
                                if (
                                    valid_from is None
                                    or valid_to is None
                                    or not isinstance(eligible_skus, list)
                                    or not isinstance(eligible_plans, list)
                                    or not isinstance(excluded_segments, list)
                                ):
                                    promotion_eligible = None
                                else:
                                    in_window = (activation_time >= valid_from) and (activation_time < valid_to)
                                    sku_ok = sku in eligible_skus
                                    plan_ok = plan_id in eligible_plans
                                    segment_ok = segment not in excluded_segments
                                    promotion_eligible = bool(in_window and sku_ok and plan_ok and segment_ok)

    # Net billed
    net_billed = None
    if account is not None:
        acct_currency = account.get("currency")
        service_period = account.get("service_period")
        complete_through = parse_dt(account.get("ledger_complete_through"))
        if acct_currency is None or service_period is None or complete_through is None or complete_through < req_knowledge_time:
            net_billed = None
        else:
            billing_records = [r for r in evidence if r.get("kind") == "billing" and same_req(r)]

            # Group by business event_id and deduplicate exact copies; conflicting copies -> null
            grouped = {}
            conflict = False
            for r in billing_records:
                event_id = r.get("event_id")
                if event_id is None:
                    conflict = True
                    break
                sig = (
                    r.get("amount"),
                    r.get("currency"),
                    r.get("event_time"),
                    r.get("posted_at"),
                    r.get("service_period"),
                )
                if event_id not in grouped:
                    grouped[event_id] = {"sig": sig, "record": r, "count": 1}
                else:
                    if grouped[event_id]["sig"] != sig:
                        conflict = True
                        break
                    grouped[event_id]["count"] += 1
            if not conflict:
                included = []
                for item in grouped.values():
                    r = item["record"]
                    if r.get("service_period") != service_period:
                        continue
                    et = parse_dt(r.get("event_time"))
                    pt = parse_dt(r.get("posted_at"))
                    amt = dec(r.get("amount"))
                    if et is None or pt is None or amt is None:
                        # Malformed record in scope; cannot safely establish net
                        conflict = True
                        break
                    if et <= req_event_time and pt <= req_knowledge_time:
                        included.append(r)
                if not conflict:
                    total = Decimal("0")
                    for r in included:
                        cur = r.get("currency")
                        if cur is None or cur != acct_currency:
                            net_billed = None
                            break
                        amt = dec(r.get("amount"))
                        if amt is None:
                            net_billed = None
                            break
                        total += amt
                    else:
                        net_billed = dec_str(total)

    # Support correctness
    support_correct = None
    if account is not None and net_billed is not None:
        acct_currency = account.get("currency")
        support_records = [r for r in evidence if r.get("kind") == "support" and same_req(r)]
        visible_support = []
        for r in support_records:
            rt = parse_dt(r.get("recorded_at"))
            if rt is not None and rt <= req_knowledge_time:
                visible_support.append(r)
        if not visible_support:
            support_correct = None
        else:
            def sup_key(r):
                rt = parse_dt(r.get("recorded_at"))
                return rt or datetime.min.replace(tzinfo=timezone.utc)
            visible_support.sort(key=sup_key, reverse=True)
            top = sup_key(visible_support[0])
            same_top = [r for r in visible_support if sup_key(r) == top]
            if len(same_top) > 1:
                sigs = {(r.get("asserted_net"), r.get("currency"), r.get("statement")) for r in same_top}
                if len(sigs) != 1:
                    support_correct = None
                else:
                    support_note = same_top[0]
                    sup_cur = support_note.get("currency")
                    asserted_net = dec(support_note.get("asserted_net"))
                    if sup_cur is None or sup_cur != acct_currency or asserted_net is None:
                        support_correct = None
                    else:
                        support_correct = (asserted_net == dec(net_billed))
            else:
                support_note = same_top[0]
                sup_cur = support_note.get("currency")
                asserted_net = dec(support_note.get("asserted_net"))
                if sup_cur is None or sup_cur != acct_currency or asserted_net is None:
                    support_correct = None
                else:
                    support_correct = (asserted_net == dec(net_billed))

    # Plan price
    plan_price = None
    if account is not None:
        plan_id = account.get("plan_id")
        lines = account.get("lines")
        if plan_id is None or lines is None:
            plan_price = None
        else:
            try:
                line_count = int(lines)
            except Exception:
                line_count = None
            if line_count is None:
                plan_price = None
            else:
                plan_records = [r for r in evidence if r.get("kind") == "catalog_link" and r.get("catalog_kind") == "plan" and r.get("catalog_id") == plan_id]
                # If plan records have visibility timestamps, respect them.
                plan_records = [r for r in plan_records if visible(r)]
                if not plan_records:
                    plan_price = None
                else:
                    def plan_key(r):
                        ts = None
                        for key in ("recorded_at", "knowledge_time", "event_time"):
                            dt = parse_dt(r.get(key))
                            if dt is not None and dt <= req_knowledge_time:
                                if ts is None or dt > ts:
                                    ts = dt
                        return ts or datetime.min.replace(tzinfo=timezone.utc)
                    plan_records.sort(key=plan_key, reverse=True)
                    top = plan_key(plan_records[0])
                    same_top = [r for r in plan_records if plan_key(r) == top]
                    if len(same_top) > 1:
                        sigs = {
                            (
                                r.get("eligible_lines"),
                                r.get("line_prices"),
                                r.get("title"),
                            )
                            for r in same_top
                        }
                        if len(sigs) != 1:
                            plan_price = None
                        else:
                            same_top = same_top[:1]
                    plan = same_top[0]
                    eligible_lines = plan.get("eligible_lines")
                    line_prices = plan.get("line_prices")
                    if not isinstance(eligible_lines, list) or not isinstance(line_prices, list) or len(eligible_lines) != len(line_prices):
                        plan_price = None
                    else:
                        matches = [i for i, n in enumerate(eligible_lines) if n == line_count]
                        if not matches:
                            plan_price = None
                        else:
                            prices = {dec(line_prices[i]) for i in matches if dec(line_prices[i]) is not None}
                            if not prices or len(prices) != 1:
                                plan_price = None
                            else:
                                plan_price = dec_str(next(iter(prices)))

    return {
        "promotion_eligible": promotion_eligible,
        "net_billed": net_billed,
        "support_correct": support_correct,
        "plan_price": plan_price,
    }
