
from decimal import Decimal, InvalidOperation, getcontext
from datetime import datetime, timezone

getcontext().prec = 50


def solve(request: dict, evidence: list[dict]) -> dict:
    def parse_dt(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

    def decimal_from(value):
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def decimal_to_str(value: Decimal):
        if value is None:
            return None
        s = format(value, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s == "-0":
            s = "0"
        return s

    def visible_by_knowledge(rec, kt):
        rt = parse_dt(rec.get("recorded_at"))
        return rt is None or rt <= kt

    def canonical_offer(rec):
        return (
            rec.get("offer_id"),
            rec.get("version"),
            rec.get("valid_from"),
            rec.get("valid_to"),
            tuple(rec.get("eligible_skus") or []),
            tuple(rec.get("eligible_plans") or []),
            tuple(rec.get("excluded_segments") or []),
            str(rec.get("credit_amount")) if rec.get("credit_amount") is not None else None,
            rec.get("currency"),
        )

    def chosen_account(records, account_id, kt):
        candidates = [r for r in records if r.get("kind") == "account" and r.get("account_id") == account_id]
        if not candidates:
            return None

        def sort_key(r):
            for k in ("recorded_at", "knowledge_time", "event_time"):
                dt = parse_dt(r.get(k))
                if dt is not None:
                    return (dt, 1)
            return (datetime.min.replace(tzinfo=timezone.utc), 0)

        candidates.sort(key=sort_key)
        return candidates[-1]

    def chosen_plan(records, plan_id, kt):
        candidates = [
            r for r in records
            if r.get("kind") == "catalog_link"
            and r.get("catalog_kind") == "plan"
            and r.get("catalog_id") == plan_id
            and visible_by_knowledge(r, kt)
        ]
        if not candidates:
            return None

        def sort_key(r):
            dt = parse_dt(r.get("recorded_at"))
            return dt if dt is not None else datetime.min.replace(tzinfo=timezone.utc)

        candidates.sort(key=sort_key)
        return candidates[-1]

    req_event_time = parse_dt(request.get("event_time"))
    req_knowledge_time = parse_dt(request.get("knowledge_time"))
    account_id = request.get("account_id")

    account = chosen_account(evidence, account_id, req_knowledge_time) if account_id and req_knowledge_time else None

    # Default outputs
    promotion_eligible = None
    net_billed = None
    support_correct = None
    plan_price = None

    # ---------------- Promotion eligibility ----------------
    promo_records = [
        r for r in evidence
        if r.get("kind") == "promotion"
        and r.get("account_id") == account_id
        and visible_by_knowledge(r, req_knowledge_time)
    ]

    if account is None or not promo_records:
        promotion_eligible = None
    else:
        # Conflict check for same offer_id + version among visible records
        groups = {}
        for r in promo_records:
            oid = r.get("offer_id")
            ver = r.get("version")
            if oid is None or ver is None:
                continue
            groups.setdefault((oid, ver), []).append(r)

        conflict = False
        for _, grp in groups.items():
            if len(grp) > 1:
                canon = {canonical_offer(g) for g in grp}
                if len(canon) > 1:
                    conflict = True
                    break
        if conflict:
            promotion_eligible = None
        else:
            # Highest visible version wins per offer_id
            by_offer = {}
            for r in promo_records:
                oid = r.get("offer_id")
                ver = r.get("version")
                if oid is None or ver is None:
                    continue
                cur = by_offer.get(oid)
                if cur is None or int(ver) > int(cur.get("version")):
                    by_offer[oid] = r

            if not by_offer:
                promotion_eligible = None
            else:
                activation_time = parse_dt(account.get("activation_time"))
                sku = account.get("sku")
                plan_id = account.get("plan_id")
                segment = account.get("segment")

                if activation_time is None or sku is None or plan_id is None or segment is None:
                    promotion_eligible = None
                else:
                    usable_offer_seen = False
                    eligible = False

                    for offer in by_offer.values():
                        vf = parse_dt(offer.get("valid_from"))
                        vt = parse_dt(offer.get("valid_to"))
                        eligible_skus = offer.get("eligible_skus")
                        eligible_plans = offer.get("eligible_plans")
                        excluded_segments = offer.get("excluded_segments")

                        if (
                            vf is None
                            or vt is None
                            or eligible_skus is None
                            or eligible_plans is None
                            or excluded_segments is None
                            or offer.get("offer_id") is None
                            or offer.get("version") is None
                        ):
                            continue

                        usable_offer_seen = True

                        in_window = vf <= activation_time < vt
                        sku_ok = sku in eligible_skus
                        plan_ok = plan_id in eligible_plans
                        segment_ok = segment not in excluded_segments

                        if in_window and sku_ok and plan_ok and segment_ok:
                            eligible = True
                            break

                    promotion_eligible = True if eligible else (False if usable_offer_seen else None)

    # ---------------- Net billed ----------------
    if account is not None:
        acct_currency = account.get("currency")
        service_period = account.get("service_period")
        ledger_complete_through = parse_dt(account.get("ledger_complete_through"))

        if (
            acct_currency is None
            or service_period is None
            or ledger_complete_through is None
            or req_knowledge_time is None
            or req_event_time is None
            or ledger_complete_through < req_knowledge_time
        ):
            net_billed = None
        else:
            billing_records = [
                r for r in evidence
                if r.get("kind") == "billing"
                and r.get("account_id") == account_id
                and r.get("service_period") == service_period
                and parse_dt(r.get("event_time")) is not None
                and parse_dt(r.get("posted_at")) is not None
                and parse_dt(r.get("event_time")) <= req_event_time
                and parse_dt(r.get("posted_at")) <= req_knowledge_time
            ]

            # Validate currencies / amounts / ids and dedupe by event_id
            grouped = {}
            invalid = False
            for r in billing_records:
                currency = r.get("currency")
                event_id = r.get("event_id")
                amount = decimal_from(r.get("amount"))
                et = parse_dt(r.get("event_time"))
                pt = parse_dt(r.get("posted_at"))
                sp = r.get("service_period")

                if (
                    currency is None
                    or currency != acct_currency
                    or event_id is None
                    or amount is None
                    or et is None
                    or pt is None
                    or sp is None
                ):
                    invalid = True
                    break

                canon = (amount, currency, et.isoformat(), pt.isoformat(), sp)
                grouped.setdefault(event_id, set()).add(canon)

            if invalid:
                net_billed = None
            else:
                for event_id, cset in grouped.items():
                    if len(cset) > 1:
                        invalid = True
                        break

                if invalid:
                    net_billed = None
                else:
                    total = Decimal("0")
                    for event_id, cset in grouped.items():
                        canon = next(iter(cset))
                        total += canon[0]
                    net_billed = decimal_to_str(total)

    # ---------------- Support correctness ----------------
    if account is None:
        support_correct = None
    else:
        acct_currency = account.get("currency")
        support_records = [
            r for r in evidence
            if r.get("kind") == "support"
            and r.get("account_id") == account_id
            and r.get("currency") == acct_currency
            and r.get("recorded_at") is not None
            and parse_dt(r.get("recorded_at")) is not None
            and parse_dt(r.get("recorded_at")) <= req_knowledge_time
            and decimal_from(r.get("asserted_net")) is not None
        ]

        if not support_records:
            support_correct = None
        else:
            asserted_values = {decimal_from(r.get("asserted_net")) for r in support_records}
            if len(asserted_values) != 1 or net_billed is None:
                support_correct = None
            else:
                asserted = next(iter(asserted_values))
                support_correct = (decimal_to_str(asserted) == net_billed)

    # ---------------- Plan price ----------------
    if account is not None:
        plan_id = account.get("plan_id")
        lines = account.get("lines")
        plan_rec = chosen_plan(evidence, plan_id, req_knowledge_time) if plan_id is not None else None

        if plan_rec is not None and lines is not None:
            eligible_lines = plan_rec.get("eligible_lines")
            line_prices = plan_rec.get("line_prices")
            if (
                isinstance(eligible_lines, list)
                and isinstance(line_prices, list)
                and len(eligible_lines) == len(line_prices)
            ):
                idx = None
                for i, n in enumerate(eligible_lines):
                    if n == lines:
                        idx = i
                        break
                if idx is not None:
                    lp = decimal_from(line_prices[idx])
                    plan_price = decimal_to_str(lp) if lp is not None else None

    return {
        "promotion_eligible": promotion_eligible,
        "net_billed": net_billed,
        "support_correct": support_correct,
        "plan_price": plan_price,
    }
