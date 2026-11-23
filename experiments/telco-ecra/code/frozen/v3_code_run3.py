
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

def solve(request: dict, evidence: list[dict]) -> dict:
    def parse_dt(s):
        if s is None:
            return None
        if isinstance(s, datetime):
            return s
        if not isinstance(s, str):
            return None
        try:
            ss = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ss)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def dec_from(v):
        try:
            if isinstance(v, Decimal):
                return v
            return Decimal(str(v))
        except Exception:
            return None

    def dec_to_str(v):
        if v is None:
            return None
        try:
            d = v if isinstance(v, Decimal) else Decimal(str(v))
            # Avoid scientific notation; normalize trailing zeros
            s = format(d.normalize(), "f")
            if "." in s:
                s = s.rstrip("0").rstrip(".")
            return s if s != "" else "0"
        except Exception:
            return None

    def eq_business_billing(a, b):
        keys = ["event_id", "amount", "currency", "event_time", "posted_at", "service_period"]
        for k in keys:
            if a.get(k) != b.get(k):
                return False
        return True

    def normalized_offer(rec):
        # Only fields relevant to eligibility; sort list fields for stable comparison
        return {
            "offer_id": rec.get("offer_id"),
            "version": rec.get("version"),
            "valid_from": rec.get("valid_from"),
            "valid_to": rec.get("valid_to"),
            "eligible_skus": tuple(sorted(rec.get("eligible_skus") or [])),
            "eligible_plans": tuple(sorted(rec.get("eligible_plans") or [])),
            "excluded_segments": tuple(sorted(rec.get("excluded_segments") or [])),
            "credit_amount": rec.get("credit_amount"),
            "currency": rec.get("currency"),
        }

    account_id = request.get("account_id")
    case_id = request.get("case_id")
    req_event_time = parse_dt(request.get("event_time"))
    req_knowledge_time = parse_dt(request.get("knowledge_time"))

    relevant = []
    for rec in evidence:
        if account_id is not None and rec.get("account_id") != account_id:
            continue
        if case_id is not None and rec.get("case_id") != case_id:
            continue
        relevant.append(rec)

    # Account snapshot
    account_records = [r for r in relevant if r.get("kind") == "account"]
    account = None
    if account_records:
        # Prefer exact request-time snapshot if present, otherwise last record in input order
        exact = []
        for r in account_records:
            if r.get("event_time") == request.get("event_time") and r.get("knowledge_time") == request.get("knowledge_time"):
                exact.append(r)
        account = exact[0] if exact else account_records[0]

    # ---------------- Promotion eligibility ----------------
    promotion_eligible = None
    promo_records = [r for r in relevant if r.get("kind") == "promotion"]
    visible_promos = []
    for r in promo_records:
        recorded_at = parse_dt(r.get("recorded_at"))
        if recorded_at is not None and req_knowledge_time is not None and recorded_at <= req_knowledge_time:
            visible_promos.append(r)

    if account is not None and visible_promos:
        # Highest visible version wins
        versions = []
        for r in visible_promos:
            ver = r.get("version")
            try:
                ver_int = int(ver)
            except Exception:
                continue
            versions.append(ver_int)

        if not versions:
            promotion_eligible = None
        else:
            max_ver = max(versions)
            top = []
            for r in visible_promos:
                try:
                    if int(r.get("version")) == max_ver:
                        top.append(r)
                except Exception:
                    pass

            # Conflicting same-version authoritative offers require null
            if not top:
                promotion_eligible = None
            else:
                canon = normalized_offer(top[0])
                conflict = False
                for r in top[1:]:
                    if normalized_offer(r) != canon:
                        conflict = True
                        break
                if conflict:
                    promotion_eligible = None
                else:
                    offer = top[0]
                    act = parse_dt(account.get("activation_time"))
                    valid_from = parse_dt(offer.get("valid_from"))
                    valid_to = parse_dt(offer.get("valid_to"))
                    sku = account.get("sku")
                    plan_id = account.get("plan_id")
                    segment = account.get("segment")

                    required_fields = [
                        act, valid_from, valid_to,
                        sku, plan_id, segment,
                        offer.get("eligible_skus"), offer.get("eligible_plans"), offer.get("excluded_segments")
                    ]
                    if any(x is None for x in required_fields[:3]) or sku is None or plan_id is None or segment is None:
                        promotion_eligible = None
                    else:
                        elig_skus = offer.get("eligible_skus") or []
                        elig_plans = offer.get("eligible_plans") or []
                        excluded_segments = offer.get("excluded_segments") or []
                        if act < valid_from or act >= valid_to:
                            promotion_eligible = False
                        elif sku not in elig_skus:
                            promotion_eligible = False
                        elif plan_id not in elig_plans:
                            promotion_eligible = False
                        elif segment in excluded_segments:
                            promotion_eligible = False
                        else:
                            promotion_eligible = True
    else:
        promotion_eligible = None

    # ---------------- Net billed ----------------
    net_billed = None
    net_value = None
    if account is not None and req_event_time is not None and req_knowledge_time is not None:
        service_period = account.get("service_period")
        acct_currency = account.get("currency")
        complete_through = parse_dt(account.get("ledger_complete_through"))
        if service_period is not None and acct_currency is not None and complete_through is not None and complete_through >= req_knowledge_time:
            billings = [
                r for r in relevant
                if r.get("kind") == "billing" and r.get("service_period") == service_period
            ]

            # Include only ledger events visible by event time and knowledge time
            included = []
            for r in billings:
                et = parse_dt(r.get("event_time"))
                pt = parse_dt(r.get("posted_at"))
                if et is None or pt is None:
                    continue
                if et <= req_event_time and pt <= req_knowledge_time:
                    included.append(r)

            # Deduplicate by business event_id; conflicting copies require null
            groups = {}
            conflict = False
            for r in included:
                eid = r.get("event_id")
                if eid is None:
                    conflict = True
                    break
                if eid not in groups:
                    groups[eid] = r
                else:
                    if not eq_business_billing(groups[eid], r):
                        conflict = True
                        break

            if not conflict:
                total = Decimal("0")
                currency_problem = False
                for r in groups.values():
                    cur = r.get("currency")
                    if cur is None or cur != acct_currency:
                        currency_problem = True
                        break
                    amt = dec_from(r.get("amount"))
                    if amt is None:
                        currency_problem = True
                        break
                    total += amt
                if not currency_problem:
                    net_value = total
                    net_billed = dec_to_str(total)

    # ---------------- Support correctness ----------------
    support_correct = None
    support_records = [r for r in relevant if r.get("kind") == "support"]
    visible_support = []
    if req_knowledge_time is not None:
        for r in support_records:
            recorded_at = parse_dt(r.get("recorded_at"))
            if recorded_at is not None and recorded_at <= req_knowledge_time:
                visible_support.append(r)

    if account is not None and visible_support and net_value is not None:
        # Choose latest visible support note
        visible_support.sort(key=lambda r: (parse_dt(r.get("recorded_at")) or datetime.min.replace(tzinfo=timezone.utc), r.get("id") or ""))
        note = visible_support[-1]
        acct_currency = account.get("currency")
        note_currency = note.get("currency")
        if note_currency is None or acct_currency is None or note_currency != acct_currency:
            support_correct = None
        else:
            asserted = dec_from(note.get("asserted_net"))
            if asserted is None:
                support_correct = None
            else:
                support_correct = (asserted == net_value)
    else:
        support_correct = None

    # ---------------- Plan price ----------------
    plan_price = None
    if account is not None:
        plan_id = account.get("plan_id")
        line_count = account.get("lines")
        if plan_id is not None and line_count is not None:
            plan_records = [
                r for r in relevant
                if r.get("kind") == "catalog_link"
                and r.get("catalog_kind") == "plan"
                and r.get("catalog_id") == plan_id
            ]
            if plan_records:
                # If multiple matching plan records conflict, return null
                canon = {
                    "eligible_lines": tuple(plan_records[0].get("eligible_lines") or []),
                    "line_prices": tuple(plan_records[0].get("line_prices") or []),
                }
                conflict = False
                for r in plan_records[1:]:
                    cur = {
                        "eligible_lines": tuple(r.get("eligible_lines") or []),
                        "line_prices": tuple(r.get("line_prices") or []),
                    }
                    if cur != canon:
                        conflict = True
                        break
                if not conflict:
                    eligible_lines = plan_records[0].get("eligible_lines") or []
                    line_prices = plan_records[0].get("line_prices") or []
                    if len(eligible_lines) == len(line_prices):
                        try:
                            idx = eligible_lines.index(line_count)
                            plan_price = dec_to_str(line_prices[idx])
                        except ValueError:
                            plan_price = None

    return {
        "promotion_eligible": promotion_eligible,
        "net_billed": net_billed,
        "support_correct": support_correct,
        "plan_price": plan_price,
    }
