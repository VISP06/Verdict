"""Deterministic conversational terminal flow; deliberately has no Streamlit dependency."""

import datetime as dt
import calendar
import html
import math
import os
import re

import failproofai_sdk as fp

from assistant import Assistant
from forecast import FORECAST_CONFIG
from main import format_payment_plan
from money import money
from plan_dates import installment_completion_date


_CURRENCIES = {"INR": "INR", "USD": "USD", "EUR": "EUR", "ZAR": "ZAR", "IDR": "IDR"}
_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "R": "ZAR"}
_PROSE_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "ZAR": "R", "IDR": "Rp"}
_AMOUNT_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?|"
    r"\d{1,3}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d{1,3}(?: \d{3})+|\d+(?:\.\d+)?)(?![\d,])", re.I
)
BANNER = ("Glacier checks whether a payment fits your next 90 days: your balance, income, bills and pending items, "
          "and tells you whether to pay now, split it, or wait.")


def parse_amount(text):
    """Return the first positive amount and explicit currency, if supplied."""
    value = str(text or "")
    currency = None
    for code in _CURRENCIES:
        if re.search(rf"\b{code}\b", value, re.I):
            currency = code
            break
    for symbol, code in _SYMBOLS.items():
        found = (symbol in value if symbol != "R" else bool(re.search(r"(?<!\w)R(?=\s*\d)", value, re.I)))
        if found:
            currency = code
            break
    if re.search(r"(?<!\w)rs\.?(?!\w)|\brupees?\b", value, re.I):
        currency = "INR"
    amount_text = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b", " ", value)
    amount_text = re.sub(
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"(?:\s+20\d{2})?\b", " ", amount_text, flags=re.I,
    )
    amount_text = re.sub(r"\bwithin\s+\d+\s+days\b", " ", amount_text, flags=re.I)
    matches = [m for m in _AMOUNT_RE.finditer(amount_text)
               if not re.match(r"\s*%", amount_text[m.end():])
               and not re.search(r"-\s*$", amount_text[:m.start()])]
    # Prefer explicit monetary notation and grouped/decimal amounts over stray
    # dates, installment counts, or other plain integers in a question.
    def priority(match):
        raw = match.group(0)
        prefix = amount_text[max(0, match.start() - 8):match.start()]
        suffix = amount_text[match.end():match.end() + 8]
        explicit = (bool(re.search(r"(?:INR|USD|EUR|ZAR|IDR|Rs\.?|₹|\$|€|R)\s*$", prefix, re.I))
                    or bool(re.match(r"\s*(?:INR|USD|EUR|ZAR|IDR)\b", suffix, re.I)))
        marked = bool(re.search(r"[, ]|\.", raw)) or bool(re.match(r"\s*(?:k|m|mn|mm|b|bn|million|lakh|lakhs|lac|lacs|cr|crore|crores)\b", suffix, re.I))
        return (0 if explicit else 1 if marked else 2, match.start())
    for match in sorted(matches, key=priority):
        raw = match.group(0).replace(",", "").replace(" ", "")
        try:
            amount = float(raw)
        except ValueError:
            continue
        suffix = amount_text[match.end():]
        prefix = amount_text[:match.start()]
        unit = re.match(r"\s*(k|m|mn|mm|b|bn|million|lakh|lakhs|lac|lacs|cr|crore|crores)(?![a-z])", suffix, re.I)
        if unit:
            u = unit.group(1).lower()
            amount *= (1_000 if u == "k" else 1_000_000 if u in {"m", "mn", "mm", "million"}
                       else 1_000_000_000 if u in {"b", "bn"}
                       else 100_000 if u in {"lakh", "lakhs", "lac", "lacs"} else 10_000_000)
        elif re.search(r"(?:\d)\s*$", prefix) and " " in prefix[-2:]:
            continue
        if amount > 0:
            return amount, currency
    return None, currency


_FOLLOWUP_COUNT_PATTERNS = (
    re.compile(r"\b(\d+)\s*installments?\b"),
    re.compile(r"\binstallments?\b[^.?!]*?\b(?:to|be|into)\s+(\d+)\b"),
    re.compile(r"\buse\s+(\d+)\s+instead\b"),
    re.compile(r"\bsplit\s+(?:it\s+)?into\s+(\d+)\b"),
)
_PURCHASE_CUE_RE = re.compile(r"\b(?:pay|paying|afford|cost|costs|buy|buying|purchase)\b")
_CURRENCY_RE = re.compile(r"(?:₹|\$|€|\b(?:INR|USD|EUR|ZAR|IDR|rs\.?)\b|(?<!\w)R(?=\s*\d))", re.I)
_FINANCING_RE = re.compile(
    r"\bhow\s+(?:can|could|should|do)\s+(?:i|we)\s+(?:finance|afford|pay)\b"
    r"|\bhow\s+to\s+(?:finance|afford|pay)\b", re.I)


def _followup_count(low):
    """Installment count stated by an amendment such as 'make it 3 installments'."""
    for pattern in _FOLLOWUP_COUNT_PATTERNS:
        match = pattern.search(low)
        if match:
            return int(match.group(1))
    return None


def _is_installment_count_followup(low):
    """True when the sentence only amends the installment count of the prior request."""
    return (_followup_count(low) is not None
            and not _PURCHASE_CUE_RE.search(low)
            and not _CURRENCY_RE.search(low))


def _amount_candidates(text):
    """Extract distinct amounts so ambiguous requests can be clarified."""
    value = str(text or "")
    result = []
    for match in _AMOUNT_RE.finditer(value):
        if re.match(r"\s*%", value[match.end():]):
            continue
        prefix = value[max(0, match.start() - 8):match.start()]
        suffix = value[match.end():match.end() + 12]
        explicit = bool(re.search(r"(?:INR|USD|EUR|ZAR|IDR|Rs\.?|₹|\$|€|R)\s*$", prefix, re.I))
        unit = bool(re.match(r"\s*(?:k|m|mn|mm|b|bn|lakh|lakhs|lac|lacs|cr|crore|crores)\b", suffix, re.I))
        grouped = "," in match.group(0) or " " in match.group(0) or "." in match.group(0)
        if not (explicit or unit or grouped):
            continue
        amount, _ = parse_amount(value[match.start():match.end() + 12])
        if amount is not None and amount not in result:
            result.append(amount)
    return result


def _indian_digits(value, decimals=2):
    """Indian digit grouping for INR amounts: 1,80,00,000 (last 3 digits, then pairs)."""
    value = float(value)
    sign = "-" if value < 0 else ""
    text = f"{abs(value):,.{decimals}f}"
    whole, _dot, frac = text.partition(".")
    whole = whole.replace(",", "")
    if len(whole) <= 3:
        grouped = whole
    else:
        suffix = whole[-3:]
        prefix = whole[:-3]
        groups = []
        while prefix:
            groups.insert(0, prefix[-2:])
            prefix = prefix[:-2]
        grouped = ",".join(groups + [suffix])
    return f"{sign}{grouped}{('.' + frac) if frac else ''}"


def fmt_money(value, currency):
    """money() with Indian digit grouping and the ₹ symbol for INR accounts."""
    if str(currency).upper() != "INR":
        return money(value, currency)
    value = float(value)
    return f"{'-' if value < 0 else ''}₹{_indian_digits(abs(value))}"


def inr_ack_words(value):
    """Compact Indian words for the acknowledgement: ₹1.8 crore, ₹12 lakh.

    Returns None below 1 lakh, where the full grouped amount is clearer.
    """
    value = float(value)
    if value >= 10_000_000:
        return f"₹{round(value / 10_000_000, 2):g} crore"
    if value >= 100_000:
        return f"₹{round(value / 100_000, 2):g} lakh"
    return None


class TerminalSession:
    def __init__(self, user_id="user_07", as_of_date=None, dataset_dir=None, assistant=None):
        self.dataset_dir = dataset_dir or os.path.join(os.path.dirname(__file__), "..", "dataset")
        self.assistant = assistant
        if assistant is None:
            assistant = Assistant(user_id, as_of_date or self._latest_date(user_id),
                                  dataset_dir=self.dataset_dir, no_llm=True)
        self.assistant = assistant
        self.user_id = str(user_id)
        self.as_of_date = str(as_of_date or assistant.as_of_date)[:10]
        self.as_of_defaulted = as_of_date is None
        if self.user_id not in self.assistant.engine.profiles.index.astype(str):
            raise ValueError(f"Unknown user id: {self.user_id}")
        self.run_id = assistant.run_id
        self.session_id = f"chat-{self.run_id}"
        self.phase = "ready"
        self.request_text = ""
        self.form = {}
        self.questions = []
        self.question_index = 0
        self.invalid_attempts = 0
        self.report = None
        self.latest_result = None
        self.latest_wording = "plain text"
        self.last_blocks = []
        self._installment_count_given = False
        self._no_change_reason_cached = None
        self._partial_safest_cache = None
        self._split_classes_cache = None
        self._fee_invalid = False
        self._conversion_note = None
        self._snapshot = None

    @property
    def as_of_note(self):
        """Line shown when the as-of date was defaulted rather than overridden."""
        if getattr(self, "as_of_defaulted", False):
            return f"Using {self.as_of_date}, the day after your latest bank record."
        return None

    def _earliest_installment_completion(self):
        return installment_completion_date(self.as_of_date, min(self.installment_counts))

    @property
    def installment_counts(self):
        cap = self.assistant._max_installment_months()
        counts = [count for count in (3, 6, 12) if cap is None or count <= cap]
        if not counts:
            counts = [cap]
        return tuple(counts)

    def _eligible_installment_counts(self):
        deadline = self.form.get("pay_by_date")
        if not deadline:
            return list(self.installment_counts)
        # With a deadline every count from 2 up to the profile cap whose last
        # payment (30-day spacing, first today) lands on or before the deadline
        # is offered — not just the usual 3/6/12 menu.
        cap = self.assistant._max_installment_months()
        cap = 12 if cap is None else min(12, int(cap))
        return [count for count in range(2, cap + 1)
                if installment_completion_date(self.as_of_date, count) <= dt.date.fromisoformat(str(deadline)[:10])]

    def _installment_options_text(self):
        counts = self._eligible_installment_counts() or list(self.installment_counts)
        return "1 {} months (default)".format(counts[0]) + "".join(f", {idx + 2} {count} months" for idx, count in enumerate(counts[1:]))

    def _installment_deadline_message(self, deadline):
        return f"No monthly installment plan finishes by {deadline}."

    def _swap_installments_for_split_offer(self, index):
        """No monthly plan finishes by the deadline: replace the count question.

        Profiles that accept partial payment get the split offer; otherwise the
        count question is dropped and the report explains the deadline miss.
        """
        self._block("text", self._installment_deadline_message(self.form["pay_by_date"]))
        if self._profile_allows_partial():
            self.questions[index] = "split_offer"
        else:
            self.questions.pop(index)

    def _split_window_days(self):
        """Split window: the deadline, otherwise 30 days, capped at 90."""
        deadline = self.form.get("pay_by_date")
        if deadline:
            window = (dt.date.fromisoformat(str(deadline)[:10]) - dt.date.fromisoformat(self.as_of_date)).days
        else:
            window = 30
        return max(0, min(90, window))

    @staticmethod
    def _zero_decimal(currency):
        return str(currency).upper() in {"IDR", "JPY", "KRW"}

    def _split_amounts(self, count, window_days):
        """Equal payments: per-payment rounds half up at currency granularity,
        the last payment absorbs the rounding."""
        fee = float(self.form.get("split_fee_pct") or 0)
        total = round(float(self.form["amount"]) * (1.0 + fee / 100.0), 2)
        factor = 1 if self._zero_decimal(self.form.get("currency")) else 100
        per = math.floor(total / count * factor + 0.5) / factor
        amounts = [per] * (count - 1) + [round(total - per * (count - 1), 2)]
        return total, amounts

    def _split_dates(self, count, window_days):
        """Evenly spaced dates: first payment today, last on the deadline."""
        as_of = dt.date.fromisoformat(self.as_of_date)
        if count <= 1:
            return [as_of.isoformat()]
        return [(as_of + dt.timedelta(days=round(i * window_days / (count - 1)))).isoformat()
                for i in range(count)]

    def _split_income_classes(self):
        """Income classes for split simulation, classified once per session."""
        if self._split_classes_cache is None:
            import pandas as pd
            from income_classifier import IncomeClassifier
            frame = pd.DataFrame([{"request_id": f"split_{self.run_id}", "user_id": self.user_id,
                                   "request_date": self.as_of_date}])
            self._split_classes_cache = IncomeClassifier(self.dataset_dir).classify_requests(
                frame, environment="calibration", sleep_between_users=0)
        return self._split_classes_cache

    def _split_row(self, count, window_days, classes):
        """One candidate split schedule, evaluated through the engine."""
        total, amounts = self._split_amounts(count, window_days)
        dates = self._split_dates(count, window_days)
        schedule = dict(zip(dates, amounts))
        balances, minimum = self.assistant.engine.simulate_90_days(
            self.user_id, self.as_of_date, additional_payments=schedule,
            income_classes=classes, **FORECAST_CONFIG)
        low = min(balances.values())
        low_date = min(balances, key=balances.get)
        return {"type": "split", "count": count, "dates": dict(sorted(schedule.items())),
                "amounts": dict(sorted(schedule.items())), "per_payment": amounts[0],
                "total_cost": round(total, 2),
                "lowest_projected_balance": round(low, 2), "lowest_projected_date": low_date,
                "meets_deadline": (max(schedule) <= self.form["pay_by_date"]
                                   if self.form.get("pay_by_date") else None),
                "safe": low >= minimum - 1e-9, "minimum_balance": minimum,
                "label": f"split {count} × {fmt_money(amounts[0], self.form.get('currency', 'INR'))}"}

    def _evaluate_split(self):
        """Evaluate evenly spaced equal-payment schedules through the engine.

        Suggests the fewest payments whose schedule keeps the balance at or
        above the minimum (equal counts as safe) and neighbours it.
        """
        window_days = self._split_window_days()
        deadline = self.form.get("pay_by_date")
        fee = float(self.form.get("split_fee_pct") or 0)
        n_max = min(12, window_days // 7 + 1) if window_days >= 7 else 0
        note = None
        if not deadline:
            note = "No deadline was given, so the split assumes a 30-day window."
        elif window_days < 7:
            note = (f"The window until {deadline} is only {window_days} day{'s' if window_days != 1 else ''}; "
                    f"split payments must be at least 7 days apart.")
        rows, safe_row = [], None
        classes = None
        forced = self.form.get("split_count")
        if forced:
            # A follow-up pinned the count ("make it 5 payments"): show it with
            # its neighbours instead of re-suggesting the fewest.
            centre = int(forced)
            for count in (centre - 1, centre, centre + 1):
                if not (2 <= count <= n_max):
                    continue
                if classes is None:
                    classes = self._split_income_classes()
                row = self._split_row(count, window_days, classes)
                rows.append(row)
                if count == centre:
                    safe_row = row
        else:
            for count in range(2, n_max + 1):
                if safe_row is not None and count > safe_row["count"] + 1:
                    break
                if classes is None:
                    classes = self._split_income_classes()
                row = self._split_row(count, window_days, classes)
                rows.append(row)
                if safe_row is None and row["safe"]:
                    safe_row = row
        return {"window_days": window_days, "deadline": deadline, "fee_pct": fee,
                "n_max": n_max, "rows": rows, "suggested": safe_row, "window_note": note}

    def _apply_split(self, result, split_eval):
        """Fold the split evaluation into the advise result the report renders."""
        result["split_evaluation"] = split_eval
        row = result.setdefault("decision", {})
        options = result.setdefault("ranked_options", [])
        existing = {(item.get("type"), item.get("count"))
                    for item in options if item.get("type") == "split"}
        for item in split_eval.get("rows", []):
            if (item.get("type"), item.get("count")) not in existing:
                options.append(item)
        suggested = split_eval.get("suggested")
        if suggested is None or not suggested.get("safe"):
            # No safe split: the honest outcome is NOT RECOMMENDED, whatever
            # the engine ranked for the other methods.
            row["affordability_status"] = "not_affordable"
            result["recommended_option"] = None
            return
        result["recommended_option"] = suggested
        row["affordability_status"] = "affordable_with_plan"
        row["recommended_payment_method"] = "partial_payment"
        row["payment_plan"] = format_payment_plan(suggested["dates"])

    def _history_outflow_average(self):
        """Average monthly debit outflows over the last 3 months of bank history."""
        events = self.assistant.engine.events
        user = events[events.user_id.astype(str).eq(self.user_id)]
        try:
            recurring = self.assistant.engine.forecast.recurring_series(
                user, dt.date.fromisoformat(self.as_of_date), min_months=3,
                amount_mode="mean")
            recurring_total = sum(float(item.get("amount", 0)) for item in recurring
                                  if str(item.get("key", ("", ""))[1]).lower() in {"debit", "out"})
            if recurring:
                return recurring_total
        except Exception:
            # Keep the snapshot deterministic for lightweight fake engines too.
            pass
        end = dt.date.fromisoformat(self.as_of_date)
        start = end - dt.timedelta(days=91)
        total = 0.0
        for row in user.itertuples():
            day = str(getattr(row, "event_date", ""))[:10]
            if (start.isoformat() <= day < end.isoformat()
                    and str(getattr(row, "direction", "")).lower() == "debit"
                    and str(getattr(row, "status", "")).lower() == "settled"):
                try:
                    amount = float(getattr(row, "amount"))
                except (TypeError, ValueError):
                    continue
                if not math.isnan(amount):
                    total += amount
        return total / 3.0

    def _largest_full_payment(self, situation):
        """Engine-bisected maximum for the full-payment method."""
        payment = self._floor_cents(self._bisect_safe_payment(
            lambda amount: {self.as_of_date: amount},
            self._unsafe_payment_bound(situation)))
        return payment

    def _bisect_safe_payment(self, schedule_of_payment, hi):
        """Largest payment p whose dated schedule keeps every projected balance safe."""
        lo = 0.0
        classes = self._split_income_classes()
        for _ in range(40):
            if hi - lo <= 0.01:
                break
            mid = (lo + hi) / 2.0
            balances, minimum = self.assistant.engine.simulate_90_days(
                self.user_id, self.as_of_date, additional_payments=schedule_of_payment(mid),
                income_classes=classes, **FORECAST_CONFIG)
            if min(balances.values()) >= minimum - 1e-9:
                lo = mid
            else:
                hi = mid
        return lo

    def _unsafe_payment_bound(self, situation):
        """A payment that certainly breaks the minimum, used as the bisect upper bound."""
        return float(situation["current_balance"]) + float(situation["minimum_balance"]) + 1000.0

    def _largest_part_now_plan(self, situation):
        """Largest purchase that stays safe paying half now and half at the strongest later day."""
        balances = situation.get("balances") or {}
        best_day = max((day for day in balances if day > self.as_of_date),
                       key=lambda day: balances[day], default=None)
        if not best_day:
            return None
        payment = self._floor_cents(self._bisect_safe_payment(
            lambda per: {self.as_of_date: per, best_day: per},
            self._unsafe_payment_bound(situation)))
        if payment <= 0:
            return None
        return 2 * payment, best_day

    def _largest_installment_plan(self, situation):
        """Largest purchase fitting the profile-capped installment count.

        Every payment inside the 90-day window must keep the balance safe in the
        forecast, and the monthly payment must stay within the estimated monthly
        room (counted income minus the 3-month outflow average).
        """
        cap = self.assistant._max_installment_months() or 12
        cap = min(12, int(cap))
        as_of = dt.date.fromisoformat(self.as_of_date)
        dates = [(as_of + dt.timedelta(days=30 * i)).isoformat()
                 for i in range(min(cap, 90 // 30 + 1))]
        per = self._floor_cents(self._bisect_safe_payment(
            lambda payment: {day: payment for day in dates},
            self._unsafe_payment_bound(situation)))
        room = float(situation.get("monthly_income_total") or 0) - self._history_outflow_average()
        total = max(0.0, min(per * cap, self._floor_cents(max(0.0, room) * cap)))
        return total, round(total / cap, 2), cap

    def _build_financing_snapshot(self):
        """FINANCING SNAPSHOT for 'how can I finance/afford/pay' questions.

        Every figure comes from the engine (forecast and capacity) or the user's
        own history; nothing is invented.
        """
        with fp.session(self.session_id):
            with fp.tool_call("financing_snapshot", input={"user_id": self.user_id}) as tool:
                snapshot = self._financing_snapshot_lines()
                skip = float(self.form["amount"]) > snapshot["largest"]
                tool.output = {"largest_feasible": snapshot["largest"],
                               "skip_questionnaire": skip}
        return snapshot, skip

    def _financing_snapshot_lines(self):
        home = self.form.get("currency", "INR")
        situation = self.assistant.situation()
        minimum = float(situation["minimum_balance"])
        capacity = self._floor_cents(max(0.0, float(situation["lowest_projected_balance"]) - minimum))
        amount = float(self.form["amount"])
        amount_text = self._prose(amount)
        item = str(self.form.get("item") or "")
        subject = f"The {item}" if item and item != "payment" and "?" not in item else "The request"
        allowed = self.assistant._profile_allowed_methods()
        lines = []
        if capacity > 0:
            ratio = amount / capacity
            if ratio >= 1.95:
                times = f"about {round(ratio)} times larger"
            elif ratio >= 1.05:
                times = f"about {ratio:.1f} times larger"
            else:
                times = f"about {round(ratio * 100)}% of what is safe today"
            lines.append(f"Safe to pay today: {self._money(capacity, home)}. {subject} costs "
                         f"{amount_text} — {times}.")
        else:
            lines.append(f"Safe to pay today: {self._money(0.0, home)}. {subject} costs {amount_text}, "
                         f"and no payment today keeps your balance above the minimum.")
        largest = 0.0
        fit = "nothing with your current balance and history"
        if "full_payment" in allowed:
            full_limit = self._largest_full_payment(situation)
            lines.append(f"Pay in full: up to {self._money(full_limit, home)} today, in one payment.")
            if full_limit > largest:
                largest, fit = full_limit, f"up to {self._money(full_limit, home)} paid in full today"
        if "partial_payment" in allowed:
            part = self._largest_part_now_plan(situation)
            if part:
                part_amount, part_day = part
                lines.append(f"Part now, rest later: up to {self._money(part_amount, home)} if you pay "
                             f"half now and half on {self._short(part_day)}.")
                if part_amount > largest:
                    largest, fit = part_amount, (f"up to {self._money(part_amount, home)} paying half now "
                                                 f"and half on {self._short(part_day)}")
        if "installments" in allowed:
            plan = self._largest_installment_plan(situation)
            if plan:
                inst_amount, per, count = plan
                lines.append(f"Installments: a purchase of up to {self._money(inst_amount, home)} over "
                             f"{count} months — about {self._money(per, home)} a month.")
                income = float(situation.get("monthly_income_total") or 0)
                outflow = self._history_outflow_average()
                room = income - outflow
                lines.append(f"That monthly payment stays within the estimated monthly room of "
                             f"{self._prose(max(0.0, room))} — counted income {self._prose(income)} minus "
                             f"average outflows {self._prose(outflow)} over your last 3 months of history. "
                             f"Payments in the next 90 days keep your balance above the minimum; "
                             f"beyond 90 days it is an estimate.")
                if inst_amount > largest:
                    largest, fit = inst_amount, (f"a purchase of up to {self._money(inst_amount, home)} "
                                                 f"over {count} months")
        lines.append(f"What would fit: {fit}.")
        lines.append("Based on: your profile, your bank history, your last 3 months of outflows "
                     "and the engine's 90-day forecast.")
        return {"text": "FINANCING SNAPSHOT\n" + "\n".join(lines), "largest": largest, "fit": fit}

    def _has_installment_conflict(self, deadline):
        count = self.form.get("installment_count") if (self.form.get("allow_installments") and getattr(self, "_installment_count_given", False)) else None
        return bool(count and deadline and installment_completion_date(self.as_of_date, count) > dt.date.fromisoformat(str(deadline)[:10]))

    def _latest_date(self, user_id):
        import pandas as pd
        engine = Assistant(user_id, dt.date.today().isoformat(), dataset_dir=self.dataset_dir, no_llm=True).engine
        dates = pd.to_datetime(engine.events[engine.events.user_id.astype(str).eq(str(user_id))].event_date, errors="coerce").dropna()
        if dates.empty:
            return dt.date.today().isoformat()
        return (dates.max().date() + dt.timedelta(days=1)).isoformat()

    def _event_range(self):
        """First and last bank-event dates for the current user, date strings."""
        events = self.assistant.engine.events
        dates = sorted(events[events.user_id.astype(str).eq(self.user_id)].event_date.dropna().astype(str))
        if not dates:
            return None, None
        return dates[0][:10], dates[-1][:10]

    def _history_guard(self):
        """Block advice when the as-of date precedes the user's first bank event."""
        first, last = self._event_range()
        if first is None:
            return self._block("error", f"No bank history for {self.user_id} before {self.as_of_date}. "
                                        f"Switch accounts with /user <id>.")
        if self.as_of_date < first:
            following = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
            return self._block("error", f"No bank history for {self.user_id} before {self.as_of_date}; "
                                        f"their history runs {first} to {last}. Use /date {following}.")
        return None

    def _block(self, kind, text, data=None):
        block = {"kind": kind, "text": str(text), "plain_text": str(text), "data": data or {}}
        self.last_blocks.append(block)
        return block

    def _prompt(self):
        question = self.questions[self.question_index]
        if question == "deadline_conflict":
            earliest = installment_completion_date(self.as_of_date, self.form["installment_count"])
            low = earliest if isinstance(earliest, str) else earliest.isoformat()
            high = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
            options = [{"key": "1", "label": "Remove the deadline", "default": True}]
            if "full_payment" in self.assistant._profile_allowed_methods():
                options.append({"key": "2", "label": "Pay in full today instead", "default": False})
            # Deadlines are capped at 90 days, so a plan that ends beyond the
            # horizon can only be resolved by removing the deadline or paying
            # in full — no date picker in that case.
            picker = None if low > high else {"label": "Or choose a later date", "min": low, "max": high}
            text = (f"The selected installment plan ends {earliest}, after that deadline. Choose 1 none, or type a date on or after {earliest}."
                    if picker else
                    f"The selected installment plan ends {earliest}, after that deadline and beyond the 90-day forecast. Choose 1 none, or 2 pay in full today.")
            return self._block("prompt", text, {"question": f"That installment plan would finish on {low}, after your deadline. What would you like to do?",
                                                "options": options,
                                                "picker": picker})
        if question == "date":
            text = "Type a date in YYYY-MM-DD format."
            low = self.as_of_date
            high = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
            return self._block("prompt", text, {"question": "Which date do you need to pay by?", "options": [], "picker": {"label": "Choose a date", "min": low, "max": high}})
        allowed = self.assistant._profile_allowed_methods()
        if question == "method":
            entries = [("1", "Let Verdict decide", True)]
            if "full_payment" in allowed:
                entries.append(("2", "Pay in full today", False))
            if "partial_payment" in allowed:
                entries.append(("3", "Pay part now and the rest later", False))
            if "installments" in allowed:
                entries.append(("4", "Pay in installments", False))
            if "partial_payment" in allowed:
                entries.append(("5", "Split into equal payments", False))
            text = ("How would you like to pay? ["
                    + ", ".join(f"{key} {label}{' (default)' if default else ''}" for key, label, default in entries) + "]")
            return self._block("prompt", text, {"question": "How would you like to pay?",
                                                "options": [{"key": key, "label": label, "default": default} for key, label, default in entries]})
        if question == "partial_amount":
            safest = self._safest_partial_amount()
            half = round(float(self.form["amount"]) * 0.5, 2)
            text = (f"How much do you want to pay now? [1 {self._fmt(safest)} now (safest, default), "
                    f"2 {self._fmt(half)} now (half), or type an amount]")
            choices = [("1", f"Pay {self._fmt(safest)} now (safest)", True), ("2", f"Pay {self._fmt(half)} now (half)", False)]
            return self._block("prompt", text, {"question": "How much do you want to pay now?",
                                                "options": [{"key": key, "label": label, "default": default} for key, label, default in choices]})
        if question == "split_offer":
            text = "Would you like to split it into equal payments instead? [1 yes (default), 2 no]"
            return self._block("prompt", text, {"question": "Would you like to split it into equal payments instead?",
                                                "options": [{"key": "1", "label": "Yes, split it", "default": True},
                                                            {"key": "2", "label": "No", "default": False}]})
        if question == "split_fee":
            text = "Optional split fee percentage? [Enter for 0% (default), or type your own up to 5%]"
            choices = [("0", "No fee", True), ("1", "1% fee", False), ("2", "2% fee", False), ("5", "5% fee", False)]
            return self._block("prompt", text, {"question": "Is there a fee for the split?",
                                                "options": [{"key": key, "label": label, "default": default} for key, label, default in choices]})
        options = {
            "deadline": "When do you need to pay? [1 none (default), 2 within 14 days, 3 end of this month, 4 type YYYY-MM-DD]",
            "installments": f"How many installments? [{self._installment_options_text()}]",
            "fee": "Optional installment fee percentage? [Enter for 0%]",
        }
        text = options[question]
        clean_questions = {"deadline": "When do you need to pay?",
                           "installments": "How many installments?",
                           "fee": "Is there a fee for installments?"}
        if question == "deadline": choices = [("1", "No deadline", True), ("2", "Within 14 days", False), ("3", "By the end of this month", False), ("4", "Pick a date…", False)]
        elif question == "installments":
            counts = self._eligible_installment_counts() or list(self.installment_counts)
            choices = [(str(idx + 1), f"{count} monthly payments", idx == 0) for idx, count in enumerate(counts)]
        else: choices = [(str(value), "No fee" if value == 0 else f"{value}% fee", value == 0) for value in (0, 1, 2, 5)]
        return self._block("prompt", text, {"question": clean_questions.get(question, text), "options": [{"key": k, "label": label, "default": default} for k, label, default in choices]})

    def _profile_allows_partial(self):
        return "partial_payment" in self.assistant._profile_allowed_methods()

    def _profile_allows_installments(self):
        return "installments" in self.assistant._profile_allowed_methods()

    def _pay_methods(self):
        """Payment structures the profile allows, in intake-question order."""
        allowed = self.assistant._profile_allowed_methods()
        return [method for method in ("full_payment", "partial_payment", "installments") if method in allowed]

    def _only_method_words(self):
        words = {"full_payment": "paying in full", "partial_payment": "partial payments",
                 "installments": "installments"}
        return words.get(self._pay_methods()[0], "this payment method")

    def _safest_partial_amount(self):
        """Largest amount that can leave the account today without breaking the minimum."""
        if self._partial_safest_cache is not None:
            return self._partial_safest_cache
        amount = float(self.form.get("amount") or 0)
        try:
            situation = self.assistant.situation()
            capacity = max(0.0, float(situation["lowest_projected_balance"]) - float(situation["minimum_balance"]))
        except Exception:
            capacity = 0.0
        # Floor to cents: a rounded-up "safest" amount would leave the balance a
        # fraction below the minimum and contradict the safe claim.
        value = self._floor_cents(min(amount, capacity))
        self._partial_safest_cache = value
        return value

    def _start(self, line):
        guard = self._history_guard()
        if guard:
            return [guard]
        amount, currency = parse_amount(line)
        candidates = _amount_candidates(line)
        if len(candidates) > 1 and not hasattr(self, "_forced_amount"):
            self.phase = "amount_clarification"
            self._ambiguous_line = line
            self._amount_choices = candidates
            options = " or ".join(fmt_money(item, currency or self.assistant.engine.get_user_home_currency(self.user_id)) for item in candidates)
            prompt = f"Which amount did you mean: {options}?"
            return [self._block("prompt", prompt, {"question": prompt, "options": []})]
        if hasattr(self, "_forced_amount"):
            amount = self._forced_amount
            del self._forced_amount
        if amount is None:
            if re.search(r"(?<!\w)-\s*\d", line):
                return [self._block("error", "Amounts must be positive; please enter an amount above zero.")]
            text = "What amount are you considering?"
            return [self._block("prompt", text, {"question": text, "options": []})]
        self.request_text = line
        home = str(self.assistant.engine.get_user_home_currency(self.user_id))
        self.form = {"amount": amount, "currency": currency or home,
                     "item": self._request_item(line),
                     "allow_partial": False, "allow_installments": False,
                     "installment_count": 3, "installment_fee_pct": 0.0}
        self._partial_safest_cache = None
        self._conversion_note = None
        # A request in another currency is converted once into the account
        # currency; every figure afterwards is shown in the account currency.
        self._normalize_currency()
        lowered = line.lower()
        self._snapshot = None
        if _FINANCING_RE.search(lowered):
            # Financing questions open with an engine-backed snapshot, before
            # any intake question; an impossible amount skips the questionnaire.
            snapshot, skip = self._build_financing_snapshot()
            self._snapshot = snapshot
            self._block("text", snapshot["text"])
            if skip:
                self._snapshot["skip"] = True
                self.questions = []
                self.phase = "intake"
                self.question_index = 0
                return self._make_report()
        self.questions = []
        self._installment_count_given = False
        if not self._parse_deadline(line):
            self.questions.append("deadline")
        # One method question replaces the old partial/installments yes/no pair.
        count = re.search(r"\b(\d+)\s+installments?\b", lowered)
        declined_inst = re.search(r"\b(?:no installments?|without (?:any )?installments?)\b", lowered)
        inst_named = bool(count) or bool(not declined_inst and re.search(r"\b(?:installments?|instalments?)\b|\bin\s+parts\b", lowered))
        partial = re.search(r"pay half now|partial payment|part now", lowered)
        full_named = re.search(r"\b(?:no partial|pay in full|full payment only|in full)\b", lowered)
        fee_match = re.search(r"(\d+(?:\.\d+)?)\s*%", lowered)
        fee_invalid = bool(fee_match and not (0 <= float(fee_match.group(1)) <= 30))
        self._fee_invalid = fee_invalid
        if fee_match and not fee_invalid:
            self.form["installment_fee_pct"] = float(fee_match.group(1))
        just_advise = bool(re.search(r"\bjust advise\b", lowered))
        statement = None
        if (count or inst_named) and not self._profile_allows_installments():
            # The user named a method the profile excludes: say so, keep the
            # fallback method, and never echo the count or offer the plan.
            if self._pay_methods()[0] == "partial_payment":
                self.form["allow_partial"] = True
            else:
                self.form["partial_declined"] = True
            self.form["allow_installments"] = False
            self.form["installments_declined"] = True
            statement = (f"Your profile doesn't include installments, "
                         f"so I'll use {self._only_method_words()} instead.")
        elif count:
            self.form.update(allow_installments=True, installment_count=int(count.group(1)),
                             allow_partial=False)
            self._installment_count_given = True
            cap = self.assistant._max_installment_months()
            if cap and self.form["installment_count"] > cap:
                self.form["installment_count"] = cap
                self._block("text", f"Your profile allows at most {cap} installments; using {cap}.")
            if fee_invalid:
                self.questions.append("fee")
        elif inst_named:
            self.form.update(allow_installments=True, allow_partial=False)
            self.questions.append("installments")
            if fee_invalid:
                self.questions.append("fee")
        elif partial:
            self.form["allow_partial"] = True
            self.form["allow_installments"] = False
            if "pay half now" in lowered:
                self.form["partial_amount"] = self.form["amount"] * 0.5
            else:
                self.questions.append("partial_amount")
        elif full_named or declined_inst:
            # The sentence already picks the method, so no follow-up is needed.
            if declined_inst:
                self.form["installments_declined"] = True
            if full_named:
                self.form["partial_declined"] = True
        elif not just_advise:
            pay_methods = self._pay_methods()
            if len(pay_methods) > 1:
                self.questions.append("method")
            elif pay_methods:
                statement = f"Your profile only allows {self._only_method_words()}."
                if pay_methods[0] == "installments":
                    self.form.update(allow_installments=True, allow_partial=False)
                    self.questions.append("installments")
                    if fee_invalid:
                        self.questions.append("fee")
                elif pay_methods[0] == "partial_payment":
                    self.form["allow_partial"] = True
                    self.questions.append("partial_amount")
        self._acknowledge(lowered)
        if statement:
            self._block("text", statement)
        if self.form.get("allow_installments") and self.form.get("pay_by_date") and self._has_installment_conflict(self.form["pay_by_date"]):
            deadline = self.form["pay_by_date"]
            self.questions = [q for q in self.questions if q != "deadline"]
            self.questions.insert(0, "deadline_conflict")
            self._block("text", f"The selected {self.form['installment_count']}-installment plan conflicts with the deadline {deadline}.")
        self.phase = "intake"
        self.question_index = 0
        if just_advise:
            self.questions = []
        if (self.form.get("pay_by_date") and not self._eligible_installment_counts()
                and "installments" in self.questions):
            # The count question is only queued once installments are chosen,
            # so a deadline that no plan can meet replaces it immediately.
            index = self.questions.index("installments")
            self._swap_installments_for_split_offer(index)
        if amount > 1_000_000_000_000:
            self._block("text", "Warning: that amount is above 1,000,000,000,000; please check it carefully.")
        if self.questions:
            prompt_block = self._prompt()
            return self.last_blocks
        return self._make_report()

    @staticmethod
    def _request_item(text):
        """Extract the purchase name from common amount-and-deadline phrasing."""
        value = str(text or "").strip()
        match = re.search(
            r"\b(?:buy|purchase)\s+(?:a|an|the)?\s*(.+?)\s+"
            r"(?:worth|costing|costs?|priced at)\s+(?:₹|\$|€|R\s*)?"
            r"[\d,.]+\s*(?:k|thousand|lakh|lakhs|lac|lacs|crore|crores)?",
            value, re.I,
        )
        if match:
            item = match.group(1)
        else:
            item = re.sub(r"\b(?:can i|can we|should i|should we)\b", "", value, flags=re.I)
            item = re.sub(r"\b(?:buy|purchase|afford|finance|financing|pay for|how|do)\b", "", item, flags=re.I)
            item = re.split(r"\b(?:worth|costing|costs?|by|i will|i'll|without|no installments?|that)\b", item, maxsplit=1, flags=re.I)[0]
            item = re.sub(r"(?:₹|\$|€|\b(?:INR|USD|EUR|ZAR|IDR|Rs\.?)\b)\s*", "", item, flags=re.I)
            item = re.sub(r"\b\d[\d,.]*(?:\s*(?:k|thousand|lakh|lakhs|lac|lacs|crore|crores))?\b", "", item, flags=re.I)
            item = re.sub(r"\b(?:no deadline|no due date|just advise|in full|pay in full|pay half now|today)\b", "", item, flags=re.I)
        item = re.sub(r"\b(?:a|an|the)\b", "", item, flags=re.I)
        return re.sub(r"\s+", " ", item).strip(" ?.!,$") .lower() or "payment"

    def _parse_deadline(self, text):
        if re.search(r"\b(no deadline|no due date|just advise)\b", text, re.I):
            self.form["pay_by_date"] = None
            return True
        if re.search(r"\btoday\b", text, re.I):
            self.form["pay_by_date"] = self.as_of_date
            return True
        iso = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
        if iso:
            try:
                parsed = dt.date.fromisoformat(iso.group(1))
                today = dt.date.fromisoformat(self.as_of_date)
                if parsed >= today:
                    self.form["pay_by_date"] = parsed.isoformat()
                    return True
            except ValueError:
                pass
        relative = re.search(r"\bin\s+(\d+)\s+(days?|weeks?)\b", text, re.I)
        if relative:
            n = int(relative.group(1)); unit = relative.group(2).lower()
            self.form["pay_by_date"] = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=n * (7 if unit.startswith("week") else 1))).isoformat()
            return True
        if re.search(r"\bwithin 14 days\b", text, re.I):
            self.form["pay_by_date"] = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=14)).isoformat()
            return True
        if re.search(r"\bend of this month\b", text, re.I):
            day = dt.date.fromisoformat(self.as_of_date)
            next_month = day.replace(day=28) + dt.timedelta(days=4)
            self.form["pay_by_date"] = (next_month.replace(day=1) - dt.timedelta(days=1)).isoformat()
            return True
        if re.search(r"\b(?:end of next month|by the end of next month)\b", text, re.I):
            day = dt.date.fromisoformat(self.as_of_date)
            first_next = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            first_following = (first_next.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            self.form["pay_by_date"] = (first_following - dt.timedelta(days=1)).isoformat()
            return True
        return False

    def _acknowledge(self, lowered):
        """One short acknowledgement per new request, built only from parsed fields."""
        if str(self.form["currency"]).upper() == "INR":
            ack_amount = inr_ack_words(self.form["amount"]) or self._money(self.form["amount"])
        else:
            ack_amount = self._money(self.form["amount"], self.form["currency"])
        parts = [f"Got it: {ack_amount}"]
        item = str(self.form.get("item") or "")
        if item and item != "payment" and "installments" not in item:
            parts.append(html.escape(item, quote=True))
        if self.form.get("allow_installments") and self._installment_count_given:
            parts.append(f"{int(self.form['installment_count'])} installments")
            if self.form.get("installment_fee_pct"):
                parts.append(f"{self.form['installment_fee_pct']:g}% fee")
        elif self.form.get("allow_installments"):
            parts.append("installments")
        elif self.form.get("allow_partial"):
            parts.append("half now" if self.form.get("partial_amount") else "partial payment")
        if self.form.get("pay_by_date"):
            parts.append(f"pay by {self.form['pay_by_date']}")
        elif re.search(r"\b(?:no deadline|no due date|just advise)\b", lowered):
            parts.append("no deadline")
        else:
            parts.append("no deadline given yet")
        text = " · ".join(parts) + "."
        note = getattr(self, "_conversion_note", None)
        if note:
            # A request in another currency is stated once, in both currencies.
            text = f"{text} {note}."
        self._block("text", text)

    def _accept_answer(self, line):
        key = self.questions[self.question_index]
        value = line.strip()
        dynamic_counts = self._eligible_installment_counts() or list(self.installment_counts)
        installment_choices = {str(idx + 1): count for idx, count in enumerate(dynamic_counts)}
        allowed = self.assistant._profile_allowed_methods()
        method_choices = {"1": "decide"}
        if "full_payment" in allowed:
            method_choices["2"] = "full"
        if "partial_payment" in allowed:
            method_choices["3"] = "partial"
        if "installments" in allowed:
            method_choices["4"] = "installments"
        if "partial_payment" in allowed:
            method_choices["5"] = "split"
        conflict_choices = {"1": None}
        if "full_payment" in allowed:
            conflict_choices["2"] = "full"
        choices = {"deadline": {"1": None, "2": "14", "3": "month", "4": "date"},
                   "deadline_conflict": conflict_choices, "method": method_choices,
                   "installments": installment_choices,
                   "split_offer": {"1": "yes", "2": "no"}}
        is_date = key == "date" or (key == "deadline_conflict" and value not in {"", "1"}
                                    and not (value == "2" and "2" in conflict_choices))
        fee_number = re.fullmatch(r"\d+(?:\.\d+)?", value)
        valid = value == "" or (key in choices and value in choices[key]) \
            or (key == "fee" and bool(fee_number) and 0 <= float(value) <= 30) \
            or (key == "split_fee" and bool(fee_number) and 0 <= float(value) <= 5)
        partial_now = None
        if key == "partial_amount":
            if value in {"", "1"}:
                partial_now = self._safest_partial_amount()
            elif value == "2":
                partial_now = round(float(self.form["amount"]) * 0.5, 2)
            else:
                partial_now = parse_amount(value)[0]
            valid = partial_now is not None and 0 < float(partial_now) <= float(self.form["amount"])
        relative_value = re.fullmatch(r"in\s+(\d+)\s+(days?|weeks?)", value, re.I)
        if relative_value and key in {"date", "deadline_conflict"}:
            n = int(relative_value.group(1)); unit = relative_value.group(2).lower()
            parsed_relative = dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=n * (7 if unit.startswith("week") else 1))
            value = parsed_relative.isoformat()
            is_date = True
        if is_date and value:
            try:
                parsed_date = dt.date.fromisoformat(value)
                as_of = dt.date.fromisoformat(self.as_of_date)
                valid = parsed_date >= as_of
                if key == "deadline_conflict":
                    # Kept deadlines are capped at 90 days like every other one.
                    valid = (parsed_date >= installment_completion_date(self.as_of_date, self.form["installment_count"])
                             and parsed_date <= as_of + dt.timedelta(days=90))
            except ValueError:
                valid = False
        if not valid:
            self.invalid_attempts += 1
            if self.invalid_attempts == 1:
                if key in {"date", "deadline_conflict"}:
                    self._block("text", f"Date must be on or after {self.as_of_date} and within the 90-day forecast.")
                elif key == "partial_amount":
                    self._block("text", f"Enter an amount up to {self._fmt(self.form['amount'])}, or press Enter for the safest amount.")
                elif key == "split_fee":
                    self._block("text", "Maximum fee is 5%. Enter a percentage between 0 and 5, or press Enter for 0%.")
                return [self._prompt()]
            value = ""
            if key == "partial_amount":
                partial_now = self._safest_partial_amount()
        self.invalid_attempts = 0
        if key in {"deadline", "deadline_conflict"}:
            if key == "deadline_conflict":
                if value in {"", "1"}:
                    self.form["pay_by_date"] = None
                elif value == "2":
                    # The user takes the option that meets the deadline: pay in
                    # full today. The kept deadline stays untouched.
                    self.form["allow_installments"] = False
                    self.form["installments_declined"] = True
                else:
                    self.form["pay_by_date"] = dt.date.fromisoformat(value).isoformat()
            elif value == "2":
                self.form["pay_by_date"] = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=14)).isoformat()
            elif value == "3":
                d = dt.date.fromisoformat(self.as_of_date); n = d.replace(day=28) + dt.timedelta(days=4)
                self.form["pay_by_date"] = (n.replace(day=1) - dt.timedelta(days=1)).isoformat()
            elif value == "4":
                self.questions[self.question_index] = "date"
                return [self._prompt()]
            else:
                self.form["pay_by_date"] = None
        elif key == "date":
            parsed = dt.date.fromisoformat(value) if value else None
            horizon = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
            if parsed and parsed.isoformat() > horizon:
                # Never silently drop a too-late deadline: say the cap and re-ask.
                self._block("text", f"Deadlines can be at most 90 days ahead (until {horizon}). "
                                    f"Pick a date up to then, or choose no deadline for longer installment plans.")
                return [self._prompt()]
            self.form["pay_by_date"] = parsed.isoformat() if parsed else None
        elif key == "method":
            pick = method_choices.get(value, "decide")
            if pick == "full":
                self.form["allow_partial"] = False
                self.form["partial_declined"] = True
                self.form["allow_installments"] = False
                self.form["installments_declined"] = True
            elif pick == "partial":
                self.form["allow_partial"] = True
                self.form["allow_installments"] = False
                self.form["installments_declined"] = True
                self.form.pop("partial_declined", None)
                self.questions.insert(self.question_index + 1, "partial_amount")
            elif pick == "installments":
                self.form["allow_installments"] = True
                self.form["allow_partial"] = False
                self.form.pop("partial_declined", None)
                self.questions.insert(self.question_index + 1, "installments")
                if getattr(self, "_fee_invalid", False):
                    self.questions.insert(self.question_index + 2, "fee")
            elif pick == "split":
                self.form["allow_split"] = True
                self.form["installments_declined"] = True
                self.form["allow_partial"] = False
                self.form["allow_installments"] = False
                self.questions.insert(self.question_index + 1, "split_fee")
            else:
                # Let Verdict decide: the engine ranks every allowed method.
                self.form["allow_partial"] = self._profile_allows_partial()
                self.form["allow_installments"] = False
                self.form.pop("partial_declined", None)
                self.form.pop("installments_declined", None)
        elif key == "installments":
            self.form["allow_installments"] = True
            self.form["allow_partial"] = False
            self.form["installment_count"] = installment_choices.get(value, installment_choices["1"])
            self._installment_count_given = True
        elif key == "partial_amount":
            applied = min(float(partial_now), float(self.form["amount"]))
            safest = self._safest_partial_amount()
            if applied > safest + 1e-9:
                applied = safest
                self._block("text", f"Only {self._fmt(safest)} is safe to pay today; using it.")
            self.form["allow_partial"] = True
            self.form["partial_amount"] = applied
        elif key == "split_offer":
            if value in {"", "1"}:
                self.form["allow_split"] = True
                self.form["installments_declined"] = True
                self.form["allow_partial"] = False
                self.form["allow_installments"] = False
                self.questions.insert(self.question_index + 1, "split_fee")
            else:
                # No split: keep the deadline miss honest in the report.
                self.form["installments_declined"] = True
        elif key == "split_fee":
            self.form["split_fee_pct"] = float(value) if value else 0.0
        elif key == "fee": self.form["installment_fee_pct"] = float(value) if value else 0.0
        self.question_index += 1
        blocks = []
        if key in {"deadline", "deadline_conflict"} and self.form.get("pay_by_date") and self.form.get("allow_installments") and self._has_installment_conflict(self.form["pay_by_date"]):
            conflict_index = max(0, self.question_index - 1)
            self.questions[conflict_index] = "deadline_conflict"
            self.question_index = conflict_index
            self._block("text", f"The selected installment plan conflicts with the deadline {self.form['pay_by_date']}.")
            self._prompt()
            return self.last_blocks
        if (key in {"deadline", "deadline_conflict", "date"} and self.form.get("pay_by_date")
                and not self._eligible_installment_counts()
                and "installments" in self.questions[self.question_index:]):
            # The count question only exists once installments are chosen, so a
            # deadline that no plan can meet replaces it immediately — whether
            # the deadline came from the sentence or a typed answer.
            index = self.questions.index("installments", self.question_index)
            self._swap_installments_for_split_offer(index)
        if key == "method" and self.form.get("allow_installments") and self.form.get("pay_by_date") \
                and not self._eligible_installment_counts() \
                and "installments" in self.questions[self.question_index:]:
            index = self.questions.index("installments", self.question_index)
            self._swap_installments_for_split_offer(index)
        if key == "installments" and self._has_installment_conflict(self.form.get("pay_by_date")):
            # The user has now actually chosen installments, so a count that
            # cannot meet the kept deadline gets the same conflict question.
            conflict_index = max(0, self.question_index - 1)
            self.questions[conflict_index] = "deadline_conflict"
            self.question_index = conflict_index
            self._block("text", f"The selected {self.form['installment_count']}-installment plan conflicts with the deadline {self.form['pay_by_date']}.")
            self._prompt()
            return self.last_blocks
        if self.question_index < len(self.questions):
            blocks.append(self._prompt())
            return blocks
        return self._make_report()

    def _make_report(self):
        self.phase = "reported"
        self.form.setdefault("pay_by_date", None)
        try:
            home = str(self.assistant.engine.get_user_home_currency(self.user_id))
            converted, _ = self.assistant._home_amount(self.form["amount"], self.form["currency"])
            if converted is None:
                return [self._block("error", f"Your account is in {home} and I have no {self.form['currency']} to {home} rate. Type the amount in {home}, or switch accounts with /user.")]
            with fp.session(self.session_id):
                with fp.agent("assistant", goal="Produce deterministic payment advice"):
                    with fp.tool_call("advise", input={"user_id": self.user_id, "amount": self.form["amount"]}) as tool:
                        result = self.assistant.advise(self.form)
                        tool.output = result
                    if self.form.get("allow_split"):
                        with fp.tool_call("evaluate_split", input={"user_id": self.user_id}) as split_tool:
                            split_eval = self._evaluate_split()
                            split_tool.output = {"candidates": [row["count"] for row in split_eval["rows"]],
                                                 "suggested": (split_eval["suggested"] or {}).get("count")}
                        self._apply_split(result, split_eval)
            self.report = result
            self.latest_result = result
            self.latest_wording = "plain text"
            text = self.render_report(result)
            return [self._block("report", text, self.report_data(result))]
        except Exception as exc:
            fp.event.error(session_id=self.session_id, agent_id="assistant", error_type=type(exc).__name__, message=str(exc)[:300])
            return [self._block("error", f"Unable to produce advice: {exc}")]

    def _unmet_deadline(self, result):
        """True when the recommended installment plan finishes after the kept deadline."""
        chosen = result.get("recommended_option") or {}
        deadline = self.form.get("pay_by_date")
        return bool(deadline and chosen.get("type") == "installments"
                    and chosen.get("meets_deadline") is False)

    def _fmt(self, value):
        return fmt_money(value, self.form["currency"])

    def _money(self, value, currency=None):
        """Table-and-data money text, Indian grouping for INR accounts."""
        return fmt_money(value, currency if currency is not None else self.form.get("currency", "INR"))

    def _prose(self, value):
        """Whole-number currency for prose text, e.g. ₹1,80,081."""
        symbol = _PROSE_SYMBOLS.get(self.form["currency"], self.form["currency"] + " ")
        amount = round(float(value))
        sign = "-" if amount < 0 else ""
        grouped = (_indian_digits(abs(amount), decimals=0) if self.form["currency"] == "INR"
                   else f"{abs(amount):,.0f}")
        return f"{sign}{symbol}{grouped}"

    @staticmethod
    def _floor_cents(value):
        """Largest two-decimal amount that does not exceed the value.

        Safety claims ("pay X now") must survive paying X to the cent: a
        rounded-up amount could leave the balance a fraction below the minimum.
        """
        return math.floor(float(value) * 100.0) / 100.0

    def _prose_currency(self, currency, value):
        """Compact symbol-and-amount text for a conversion note, e.g. $100."""
        symbol = _PROSE_SYMBOLS.get(currency, currency + " ")
        value = float(value)
        if str(currency).upper() == "INR":
            grouped = _indian_digits(abs(value), decimals=2 if abs(value - round(value)) >= 0.005 else 0)
        else:
            grouped = f"{abs(value):,.0f}" if abs(value - round(value)) < 0.005 else f"{abs(value):,.2f}"
        sign = "-" if value < 0 else ""
        return f"{sign}{symbol}{grouped}"

    def _margin_words(self, margin):
        """Above/below wording for a balance margin; the boundary is at the minimum."""
        if margin > 0.005:
            return f"{self._prose(margin)} above your minimum"
        if margin < -0.005:
            return f"{self._prose(-margin)} below your minimum"
        return "exactly at your minimum"

    def _normalize_currency(self):
        """Convert a foreign-currency request once into the account currency.

        Everything afterwards (acknowledgement, WHY, NUMBERS, OPTIONS) stays in
        the account currency; the conversion is stated once in the
        acknowledgement, e.g. "$100 = ₹8,333 (rate 83.33)".
        """
        home = str(self.assistant.engine.get_user_home_currency(self.user_id))
        requested = str(self.form.get("currency") or home)
        self.form["currency"] = requested
        amount = self.form.get("amount")
        if requested == home or not amount:
            return
        converted, _home = self.assistant._home_amount(float(amount), requested)
        if converted is None:
            return  # no dated rate; the report asks for the home-currency amount
        original = float(amount)
        rate = round(float(converted) / original, 2)
        self.form["amount"] = round(float(converted), 2)
        self.form["currency"] = home
        rate_text = f"{rate:.2f}".rstrip("0").rstrip(".")
        self._conversion_note = (f"{self._prose_currency(requested, original)} = "
                                 f"{self._prose_currency(home, self.form['amount'])} "
                                 f"(rate {rate_text})")

    def _short(self, day):
        """Short prose date, e.g. 13 Sep."""
        try:
            parsed = dt.date.fromisoformat(str(day)[:10])
        except (TypeError, ValueError):
            return str(day)
        return f"{parsed.day} {parsed.strftime('%b')}"

    def _based_on_line(self, result=None):
        """The single closing evidence line shared by WHY and 'Why not now?'."""
        result = result if result is not None else (self.report or self.latest_result)
        ref = self._employer_message_ref(result) if result else None
        line = "Based on: your profile, your bank history and your salary pattern"
        if ref:
            line += f" and your employer's update {ref}"
        return line + "."

    _CATEGORY_WORDS = {
        "rent": "rent", "debt_repayment": "loan EMI", "utilities": "electricity bill",
        "groceries": "groceries", "transport": "transport", "dining": "eating out",
        "healthcare": "healthcare", "family_support": "family support",
        "music_subscription": "music subscription", "cloud_storage": "cloud storage",
        "shopping": "shopping", "salary": "salary", "insurance": "insurance",
        "education": "education", "housing": "housing", "investment": "investment",
    }

    def _everyday_label(self, flow):
        """Plain name for a forecast flow, in everyday words."""
        category = str(flow.get("category") or "").strip().lower()
        if flow.get("source") == "variable_spending" or (not category and not flow.get("description") and not flow.get("series_key")):
            return "everyday spending"
        if category in self._CATEGORY_WORDS:
            return self._CATEGORY_WORDS[category]
        description = str(flow.get("description") or "").strip().lower()
        if description:
            return description
        return category.replace("_", " ") or "regular outflow"

    def _outflows_around(self, breakdown, day, window=5, count=3):
        """The 2-3 biggest named outflows within a few days of the dip date."""
        flows = breakdown.get("flows_by_day", {})
        try:
            target = dt.date.fromisoformat(str(day)[:10])
        except ValueError:
            return []
        items = []
        for day_key, day_flows in flows.items():
            try:
                flow_day = dt.date.fromisoformat(str(day_key)[:10])
            except ValueError:
                continue
            if abs((flow_day - target).days) > window:
                continue
            for flow in day_flows:
                amount = float(flow.get("amount", 0))
                if amount < 0:
                    items.append((-amount, self._everyday_label(flow)))
        items.sort(key=lambda item: (-item[0], item[1]))
        labels = []
        for _amount, label in items:
            if label not in labels:
                labels.append(label)
            if len(labels) >= count:
                break
        return labels

    @staticmethod
    def _ordinal(day):
        day = int(day)
        if 10 <= day % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"{day}{suffix}"

    def _income_facts(self, result):
        """Counted income per month with its projected payment dates, from engine flows."""
        breakdown = result.get("evidence", {}).get("breakdown", {})
        counted = {str(item.get("series_key"))
                   for item in (result["situation"].get("monthly_income_counted") or [])
                   if item.get("series_key")}
        groups = {}
        for day_flows in breakdown.get("flows_by_day", {}).values():
            for flow in day_flows:
                if flow.get("source") != "income" or float(flow.get("amount", 0)) <= 0:
                    continue
                key = str(flow.get("series_key") or flow.get("event_id") or "income")
                if counted and key not in counted:
                    continue
                group = groups.setdefault(key, {"total": 0.0, "dates": [], "category": str(flow.get("category") or "")})
                group["total"] += float(flow.get("amount", 0))
                if flow.get("date") and flow["date"] not in group["dates"]:
                    group["dates"].append(flow["date"])
        return [{"series_key": key, "monthly": value["total"] / 3.0,
                 "dates": sorted(value["dates"]), "category": value["category"]}
                for key, value in sorted(groups.items())]

    def _employer_message(self, result):
        """(ref, sent date, text) of the employer message retrieved for this answer."""
        ids = result.get("evidence", {}).get("retrieved_message_ids", [])
        if not ids:
            ids = result.get("evidence", {}).get("explanation_evidence", {}).get("retrieved_message_ids", [])
        messages_path = os.path.join(self.dataset_dir, "messages.csv")
        if not ids or not os.path.exists(messages_path):
            return None, None, None
        wanted = {str(item) for item in ids}
        import csv
        try:
            with open(messages_path) as fh:
                for row in csv.DictReader(fh):
                    if row.get("message_id") not in wanted:
                        continue
                    if str(row.get("source_type", "")).strip().lower() != "employer":
                        continue
                    sent = str(row.get("sent_at") or "")[:10]
                    if sent and self.as_of_date and sent > self.as_of_date:
                        continue  # only messages sent on or before the as-of date
                    text = str(row.get("message_text", ""))
                    match = re.search(r"\bEMP-\d+\b", text)
                    ref = match.group(0) if match else str(row["message_id"])
                    return ref, sent, text
        except OSError:
            return None, None, None
        return None, None, None

    def _employer_message_ref(self, result):
        """EMP-xxx payroll reference of an employer message used for this answer."""
        ref, _sent, _text = self._employer_message(result)
        return ref

    def _employer_payroll_note(self, result):
        """Honest note when the employer's payroll date differs from the forecast's.

        Display only: the forecast itself is never changed by this note.
        """
        ref, _sent, text = self._employer_message(result)
        if not ref or not text:
            return None
        match = re.search(r"\b(20\d\d-\d\d-\d\d)\b", text)
        if not match:
            return None
        try:
            said = dt.date.fromisoformat(match.group(1))
        except ValueError:
            return None
        upcoming = sorted(day for fact in self._income_facts(result)
                          for day in fact["dates"] if day >= self.as_of_date)
        if not upcoming or said.isoformat() in upcoming:
            return None  # the forecast already lands salary on the employer's date
        assumed_day = int(upcoming[0].split("-")[2])
        return (f"Your employer says salary is now expected on {self._short(said)}; "
                f"this forecast still assumes the {self._ordinal(assumed_day)}.")

    def _employer_line(self, result):
        """The employer sentence for the report's WHY section (honest about dates)."""
        ref, _sent, text = self._employer_message(result)
        if not ref:
            return None
        note = self._employer_payroll_note(result)
        if note:
            return note
        if re.search(r"\b(20\d\d-\d\d-\d\d)\b", text or ""):
            return f"Your employer's message {ref} agrees with the salary date this forecast assumes."
        return f"Your employer's message {ref} was counted as evidence for this income."

    def _income_words(self, facts):
        """Plain-words description of counted income: salary, amount, day of month."""
        pieces = []
        for fact in facts:
            category = str(fact.get("category") or "").strip().lower()
            day = int(fact["dates"][0].split("-")[2]) if fact["dates"] else 1
            word = "salary" if category == "salary" else self._CATEGORY_WORDS.get(category, "income")
            pieces.append(f"your {word} of {self._prose(fact['monthly'])} lands on the {self._ordinal(day)}")
        return " and ".join(pieces)

    def _plan_effect_low(self, result, option):
        """Lowest balance after this option's first payment actually leaves the account."""
        baseline = result.get("evidence", {}).get("balances", {})
        first = min(option.get("dates") or {}) if option.get("dates") else None
        later = [(day, balance - sum(value for pday, value in option["amounts"].items() if pday <= day))
                 for day, balance in baseline.items() if first and day >= first]
        if not later:
            return option.get("lowest_projected_balance", 0), option.get("lowest_projected_date", self.as_of_date)
        low_day, low_value = min(later, key=lambda item: item[1])
        return low_value, low_day

    def _user_plan_row(self, result):
        """The ranked row for the payment method the user chose, safe or not.

        The default WHY path already describes a safe chosen plan; this helper
        exists so that a NOT RECOMMENDED verdict can still explain itself with
        the plan the user asked about (installments, split, part-now), instead
        of describing only "pay in full".
        """
        chosen = result.get("recommended_option")
        if chosen is not None and chosen.get("safe", False):
            return None
        options = result.get("ranked_options", [])
        if self.form.get("allow_split"):
            rows = [x for x in options if x.get("type") == "split"]
            if rows:
                wanted = self.form.get("split_count")
                match = next((x for x in rows if wanted and x.get("count") == int(wanted)), None)
                return match or max(rows, key=lambda x: x.get("count", 0))
        count = self.form.get("installment_count")
        if self.form.get("allow_installments") and count:
            match = next((x for x in options if x.get("type") == "installments"
                          and len(x.get("amounts") or {}) == int(count)), None)
            if match:
                return match
        if self.form.get("allow_partial"):
            amount = float(self.form.get("partial_amount") or 0)
            match = next((x for x in options if x.get("type") == "partial_then_remainder"
                          and abs(float(next(iter(x.get("amounts", {0.0: 0.0}).values()))) - amount) < 0.01), None)
            if match:
                return match
            rows = [x for x in options if x.get("type") == "partial_then_remainder"]
            if rows:
                return rows[0]
        # No method was picked explicitly: when the profile keeps installments
        # as a considered method, the engine still evaluated one — discuss it.
        if "installments" in self.assistant._profile_allowed_methods():
            rows = [x for x in options if x.get("type") == "installments"]
            if rows:
                return min(rows, key=lambda x: len(x.get("amounts") or {}))
        return None

    def _plan_reject_lines(self, result, row):
        """Why the user's chosen plan fails: payment size against income, first dip below the minimum."""
        schedule = sorted((str(day), float(value)) for day, value in (row.get("amounts") or {}).items())
        if not schedule:
            return []
        count = row.get("count") or len(schedule)
        per = schedule[0][1]
        minimum = float(result["situation"]["minimum_balance"])
        lines = []
        facts = self._income_facts(result)
        monthly = sum(float(fact["monthly"]) for fact in facts)
        if per > 0 and monthly > 0:
            ratio = per / monthly
            if len(facts) == 1:
                category = str(facts[0].get("category") or "").strip().lower()
                word = "salary" if category == "salary" else self._CATEGORY_WORDS.get(category, "income")
                income_name = f"your monthly {word} of {self._prose(monthly)}"
            else:
                income_name = f"your counted monthly income of {self._prose(monthly)}"
            if ratio >= 1.95:
                comparison = f"about {max(2, round(ratio))} times {income_name}"
            elif ratio >= 1.05:
                comparison = f"more than {income_name}"
            elif ratio >= 0.75:
                comparison = f"about the size of {income_name}"
            else:
                comparison = f"about {round(ratio * 100)}% of {income_name}"
            lines.append(f"{count} payments of {self._prose(per)} are {comparison}.")
        baseline = result.get("evidence", {}).get("balances", {})
        first_below = row.get("lowest_projected_date")
        for day in sorted(baseline):
            paid = sum(value for pday, value in schedule if pday <= day)
            if float(baseline[day]) - paid < minimum - 1e-9:
                first_below = day
                break
        if first_below:
            paid = sum(value for pday, value in schedule if pday <= str(first_below))
            dip = float(baseline.get(str(first_below), 0.0)) - paid
            lines.append(f"With these payments the balance first falls below your minimum of "
                         f"{self._prose(minimum)} on {self._short(first_below)}, reaching about {self._prose(dip)}.")
        return lines

    def _why_section(self, result):
        situation = result["situation"]
        breakdown = result.get("evidence", {}).get("breakdown", {})
        lines = []
        # (a) Balance today and the amount to keep free.
        lines.append(f"You have {self._prose(situation['current_balance'])} available today and want to keep "
                     f"at least {self._prose(situation['minimum_balance'])} free.")
        # (b) Tightest point in the next 90 days with the biggest nearby outflows.
        #     The baseline figure never assumes this payment, and says so.
        low_date = situation.get("lowest_projected_date") or self.as_of_date
        low = float(situation.get("lowest_projected_balance") or 0)
        names = self._outflows_around(breakdown, low_date)
        if names:
            joined = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
            lines.append(f"Before this payment, the lowest balance in the next 90 days is about "
                         f"{self._prose(low)} on {self._short(low_date)}, after your {joined} go out.")
        else:
            lines.append(f"Before this payment, the lowest balance in the next 90 days is about "
                         f"{self._prose(low)} on {self._short(low_date)}.")
        # (c) Counted income in plain words, plus the honest employer payroll note.
        facts = self._income_facts(result)
        if facts:
            lines.append(f"Counted income: {self._income_words(facts)} each month.")
        else:
            lines.append("No recurring income is counted in this forecast.")
        employer_line = self._employer_line(result)
        if employer_line:
            lines.append(employer_line)
        # (d) One-sentence result: what the recommended plan does to the balance.
        #     Every figure here assumes the payment leaves the account.
        chosen = result.get("recommended_option")
        if chosen is not None and not chosen.get("safe", False):
            chosen = None
        minimum = float(situation["minimum_balance"])
        if chosen is None:
            plan_row = self._user_plan_row(result)
            if plan_row is not None:
                # The user chose a plan (or the profile keeps one): explain the
                # verdict with that plan, never with "pay in full" alone.
                lines.extend(self._plan_reject_lines(result, plan_row))
            else:
                full = next((x for x in result.get("ranked_options", []) if x["type"] == "pay_in_full_now"), None)
                if full:
                    low_value, low_day = full["lowest_projected_balance"], full["lowest_projected_date"]
                    below = minimum - low_value
                    if below > 0.005:
                        lines.append(f"Paying {self._prose(self.form['amount'])} in full now would take you to "
                                     f"{self._prose(low_value)} on {self._short(low_day)}, {self._prose(below)} below "
                                     f"your minimum, so the full payment does not fit in the next 90 days.")
                    elif below > 0:
                        lines.append(f"Paying {self._prose(self.form['amount'])} in full now would take you to "
                                     f"{self._prose(low_value)} on {self._short(low_day)}, just under "
                                     f"your minimum, so the full payment does not fit in the next 90 days.")
                    else:
                        lines.append(f"Paying {self._prose(self.form['amount'])} in full now would keep your lowest "
                                     f"balance at {self._prose(low_value)} on {self._short(low_day)}, "
                                     f"{self._margin_words(-below)}.")
        else:
            low_value, low_day = self._plan_effect_low(result, chosen)
            margin = low_value - minimum
            amount_text = self._prose(self.form["amount"])
            margin_text = self._margin_words(margin)
            if chosen["type"] == "pay_in_full_now":
                lines.append(f"Paying {amount_text} in full now would take you to {self._prose(low_value)} on "
                             f"{self._short(low_day)}, {margin_text}, "
                             f"so you can go ahead today.")
            elif chosen["type"] == "installments":
                count = self.form.get("installment_count", 3)
                lines.append(f"Paying {amount_text} in {count} installments would take you to "
                             f"{self._prose(low_value)} on {self._short(low_day)}, {margin_text}, "
                             f"so the plan stays safe.")
            elif chosen["type"] == "wait":
                wait_date = max(chosen["dates"])
                lines.append(f"Waiting until {self._short(wait_date)} to pay {amount_text} would take you to "
                             f"{self._prose(low_value)} on {self._short(low_day)}, {margin_text}, "
                             f"so waiting makes the payment safe.")
            elif chosen["type"] == "split":
                count = chosen.get("count", 2)
                entries = sorted((chosen.get("amounts") or {}).items())
                first_amount = self._prose(entries[0][1]) if entries else amount_text
                last_date = max(chosen["dates"])
                lines.append(f"Splitting {amount_text} into {count} equal payments of about {first_amount} until "
                             f"{self._short(last_date)} would take you to {self._prose(low_value)} on "
                             f"{self._short(low_day)}, {margin_text}, so the split stays safe.")
                split_eval = result.get("split_evaluation") or {}
                if split_eval.get("window_note"):
                    lines.append(split_eval["window_note"])
            else:
                entries = sorted(chosen["amounts"].items())
                first_amount = self._prose(entries[0][1])
                rest_date = max(chosen["dates"])
                rest_amount = self._prose(self.form["amount"] - entries[0][1])
                lines.append(f"Paying {first_amount} now and {rest_amount} on {self._short(rest_date)} would "
                             f"take you to {self._prose(low_value)} on {self._short(low_day)}, "
                             f"{margin_text}, so splitting it works.")
        # (e) Pending bank items in plain words.
        pending = situation.get("pending_items_considered_or_ignored") or {}
        ignored = list(pending.get("ignored") or [])
        considered = list(pending.get("considered") or [])
        if ignored:
            noun = "item is" if len(ignored) == 1 else "items are"
            lines.append(f"{len(ignored)} unsettled incoming {noun} not counted, because that money has not "
                         f"actually arrived in your account yet.")
        elif considered:
            noun = "item is" if len(considered) == 1 else "items are"
            lines.append(f"{len(considered)} unsettled bank {noun} already counted in this forecast.")
        else:
            lines.append("No unsettled bank items affect this.")
        lines.append(self._based_on_line(result))
        return "\n".join(lines)

    def _wait_methods_words(self):
        allowed = self.assistant._profile_allowed_methods()
        words = {"full_payment": "paying in full", "partial_payment": "partial payments",
                 "installments": "installments", "wait": "waiting"}
        return ", ".join(words.get(method, method) for method in sorted(allowed))

    def _no_change_reason(self, low):
        """Why a follow-up left the verdict unchanged, built from the profile and engine evidence."""
        allowed = self.assistant._profile_allowed_methods()
        if "half" in low or re.search(r"\bpay\s+\d+(?:\.\d+)?\s*%\s+now\b", low) or re.search(r"\bpay\s+[\d,.]+\s*(?:k|thousand)?\s+now\b", low):
            if "partial_payment" not in allowed:
                return "partial payment is not among the payment methods you accept"
            return "paying the full amount today is already safe, so splitting it does not change the plan"
        if self.form.get("allow_split"):
            fee = re.search(r"\bfee\s+(\d+(?:\.\d+)?)\s*%?", low)
            if fee:
                return f"the split fee is now {float(fee.group(1)):g}%, which only changes the payment size"
        count = _followup_count(low)
        if count:
            if "installments" not in allowed:
                return "installments are not among the payment methods you accept"
            return f"the plan already uses {int(self.form.get('installment_count', count))} installments"
        if re.search(r"\bwait\b|\ba month later\b", low):
            if "full_payment" not in allowed:
                if allowed == {"installments"}:
                    return "your accepted methods are installments only, so waiting does not change the plan"
                return (f"you only consider {self._wait_methods_words()}, "
                        "so waiting does not change the plan")
            chosen = (self.report or {}).get("recommended_option") or {}
            if chosen.get("type") == "wait":
                return f"the plan already waits until {max(chosen.get('dates') or ['the safe date'])}"
            if chosen.get("type") == "pay_in_full_now":
                return "paying in full today is already safe, so waiting does not change the plan"
            return "no safe date appears within the 90-day forecast, so waiting does not change the plan"
        iso = re.search(r"\bby\s+(20\d\d-\d\d-\d\d)\b", low)
        if iso:
            chosen = (self.report or {}).get("recommended_option") or {}
            dates = chosen.get("dates") or []
            if dates and max(dates) <= iso.group(1):
                return f"the plan already completes by {iso.group(1)}"
            return "the deadline does not change the recommended plan"
        if re.search(r"\b(?:actually|make it)\b", low):
            if str(((self.report or {}).get("decision") or {}).get("affordability_status")) == "not_affordable":
                return "this amount still does not fit within the next 90 days"
            return f"the same plan fits the new amount of {self._fmt(self.form['amount'])}"
        return "the recommended plan does not change"

    def quick_replies(self):
        """Quick-reply chips that apply to the profile's accepted payment methods."""
        allowed = self.assistant._profile_allowed_methods()
        chips = []
        if "partial_payment" in allowed:
            chips.append(("Pay half now", "pay half now"))
        if "full_payment" in allowed:
            chips.append(("Wait a month", "wait a month"))
        if "installments" in allowed:
            chip = self._installment_chip()
            if chip:
                chips.append(chip)
        chips.append(("Why not now?", "why not now"))
        return chips

    def _installment_chip(self):
        """A 'Use N installments' chip only when N is allowed and meets the deadline.

        Never offers the count the current verdict already uses.
        """
        chosen = (self.report or {}).get("recommended_option") or {}
        verdict_installments = bool(chosen) and chosen.get("type") == "installments" and chosen.get("safe", False)
        deadline = self.form.get("pay_by_date")
        eligible = self._eligible_installment_counts()
        counts = eligible if deadline else list(self.installment_counts)
        if verdict_installments:
            counts = [count for count in counts if count != int(self.form.get("installment_count") or 0)]
        if not counts:
            return None
        count = min(counts)
        return (f"Use {count} installments", f"{count} installments")

    def _timeline_line(self, result, count=3, window=14):
        """The next few named outflows, with amounts and short dates."""
        flows = result.get("evidence", {}).get("breakdown", {}).get("flows_by_day", {})
        items = []
        for day in sorted(flows):
            if self.as_of_date <= day <= (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=window)).isoformat():
                for flow in flows[day]:
                    amount = float(flow.get("amount", 0))
                    if amount < 0:
                        items.append((day, -amount, self._everyday_label(flow)))
        shown = sorted(sorted(items, key=lambda item: (-item[1], item[0]))[:count], key=lambda item: item[0])
        if not shown:
            return None
        parts = [f"{label} {self._prose(value)} on {self._short(day)}" for day, value, label in shown]
        return "Coming up: " + ", ".join(parts) + "."

    def _next_income_line(self, result):
        """When counted income lands next, in plain words."""
        facts = self._income_facts(result)
        if not facts:
            return "Income: no counted income lands in the next 90 days."
        upcoming = sorted(day for fact in facts for day in fact["dates"] if day >= self.as_of_date)
        pieces = []
        for fact in facts:
            category = str(fact.get("category") or "").strip().lower()
            word = "salary" if category == "salary" else self._CATEGORY_WORDS.get(category, "income")
            pieces.append(f"your {word} of {self._prose(fact['monthly'])}")
        words = " and ".join(pieces)
        if upcoming:
            return f"Income: {words}; the next payment lands on {self._short(upcoming[0])}."
        return f"Income: {words}."

    def _why_not_now_text(self):
        """Evidence-based answer to 'Why not now?', distinct from the report's WHY section."""
        result = self.report or self.latest_result
        if not result:
            return "No completed answer yet. Ask an amount first."
        options = result.get("ranked_options", [])
        chosen = result.get("recommended_option")
        if chosen is not None and not chosen.get("safe", False):
            chosen = None
        full = next((x for x in options if x["type"] == "pay_in_full_now"), None)
        allowed = self.assistant._profile_allowed_methods()
        amount_value = float(self.form.get("amount") or 0)
        amount_text = self._prose(amount_value)
        situation = result["situation"]
        minimum = float(situation["minimum_balance"])
        # The headline is NOT RECOMMENDED both when nothing is affordable and
        # when the chosen plan finishes after the kept deadline; the follow-up
        # must explain either case.
        not_recommended = chosen is None or self._unmet_deadline(result)
        lines = []
        # 1. Straight one-line answer: can the full amount leave the account today?
        full_safe = bool(full and full.get("safe") and amount_value > 0)
        if full:
            low = float(full["lowest_projected_balance"])
            low_day = self._short(full["lowest_projected_date"])
        else:
            low = float(situation.get("lowest_projected_balance") or 0) - amount_value
            low_day = self._short(situation.get("lowest_projected_date") or self.as_of_date)
        margin = low - minimum
        room = self._margin_words(margin)
        if full_safe:
            lines.append(f"Yes — you can pay the full {amount_text} today.")
        else:
            lines.append(f"No — you cannot pay the full {amount_text} today.")
        # 2. The lowest balance if you pay, always labelled as assuming the payment.
        lines.append(f"If you pay it all today, your lowest balance would be about {self._prose(low)} "
                     f"on {low_day}, {room}.")
        # 3. Coming up: the next few named outflows.
        timeline = self._timeline_line(result)
        if timeline:
            lines.append(timeline)
        # 4. Income, plus the honest employer payroll note when dates disagree.
        lines.append(self._next_income_line(result))
        note = self._employer_payroll_note(result)
        if note:
            lines.append(note)
        # 5. The real amount you could pay today and stay above your minimum,
        #    computed from the situation's room, not capped at the request.
        #    Floored to cents so the quoted amount really is safe to pay.
        try:
            # Use the no-payment engine situation here.  The advisory result's
            # evidence may describe a requested scenario, while this line is
            # specifically the independent amount-safe-today capacity.
            baseline_situation = self.assistant.situation()
            capacity = self._floor_cents(max(0.0, float(baseline_situation["lowest_projected_balance"]) -
                                             float(baseline_situation["minimum_balance"])))
        except (KeyError, TypeError, ValueError):
            capacity = 0.0
        if capacity > 0:
            lines.append(f"You could pay up to {self._prose(capacity)} today and stay above your minimum.")
        else:
            lines.append("Right now no payment amount keeps you above your minimum without new income.")
        # When nothing was affordable and the user chose a plan (installments
        # or a split), explain the failure with that plan's own payments.
        if not_recommended:
            plan_row = self._user_plan_row(result)
            if plan_row is not None:
                lines.extend(self._plan_reject_lines(result, plan_row))
        # 6. Which accepted-method or deadline rule blocks a safe full payment.
        rules = []
        if full_safe and "full_payment" not in allowed:
            rules.append(f"you told us you only consider {self._wait_methods_words()}")
        if not_recommended and self._unmet_deadline(result):
            rules.append(f"nothing you consider completes by your deadline {self.form['pay_by_date']}")
        for text in rules:
            lines.append(f"But {text}.")
        if not rules and not_recommended and full_safe:
            lines.append("But the decision engine did not select it for this request; the OPTIONS table shows it "
                         "as evidence.")
        lines.append(self._based_on_line(result))
        return "\n".join(lines)

    def render_report(self, result):
        currency = self.form["currency"]
        options = result.get("ranked_options", [])
        chosen = result.get("recommended_option")
        if chosen is not None and not chosen.get("safe", False):
            chosen = None
        not_affordable = str(result.get("decision", {}).get("affordability_status")) == "not_affordable"
        unmet_deadline = self._unmet_deadline(result)
        if unmet_deadline:
            verdict = f"NOT RECOMMENDED: no allowed plan completes by {self.form['pay_by_date']}"
        elif not_affordable or chosen is None:
            verdict = "NOT RECOMMENDED: no option keeps your balance above the minimum"
        elif chosen["type"] == "pay_in_full_now": verdict = "PAY IN FULL TODAY"
        elif chosen["type"] == "installments": verdict = f"PAY IN {self.form.get('installment_count', 3)} INSTALLMENTS"
        elif chosen["type"] == "split": verdict = f"SPLIT INTO {chosen.get('count', 2)} EQUAL PAYMENTS"
        elif chosen["type"] == "partial_then_remainder": verdict = f"PAY {self._money(next(iter(chosen['amounts'].values())), currency)} NOW AND THE REST ON {list(chosen['amounts'])[-1]}"
        elif chosen["type"] == "wait": verdict = f"WAIT UNTIL {max(chosen['dates'])}"
        else: verdict = "NOT RECOMMENDED"
        situation = result["situation"]
        why = self._why_section(result)
        balances = result.get("evidence", {}).get("balances", {})
        vals = list(balances.values())
        levels = "▁▂▃▄▅▆▇█"
        spark = "" if not vals else "".join(levels[round((v-min(vals))/(max(vals)-min(vals))*7)] if max(vals) != min(vals) else "▄" for v in vals)
        minimum = situation["minimum_balance"]
        rows = ["OPTION                 WHEN                 TOTAL COST       LOWEST BALANCE   MEETS DEADLINE   SAFE"]
        for option in options:
            safe_mark = "✓" if option.get("safe") else "✗"
            below = max(0.0, float(minimum) - float(option["lowest_projected_balance"]))
            # A shortfall that rounds to 0.00 is a hair under the minimum, not
            # "0.00 below" it; say that instead of printing a contradiction.
            shortfall = (f"{self._money(below,currency)} below the minimum" if below > 0.005
                         else "just under the minimum")
            safety = "SAFE" if option.get("safe") else (
                f"(drops to {self._money(option['lowest_projected_balance'],currency)} on "
                f"{option['lowest_projected_date']}, {shortfall})"
            )
            meets = option.get("meets_deadline")
            meets = "n/a" if meets is None else ("yes" if meets else "no")
            name = option.get("label") or option["type"].replace("_", " ")
            rows.append(f"{name:22} {', '.join(option['dates']):20} {self._money(option['total_cost'],currency):16} {self._money(option['lowest_projected_balance'],currency):16} {meets}   {safe_mark} {'SAFE' if option.get('safe') else 'UNSAFE'}")
            if not option.get("safe"):
                rows.append(f"                         Note: {safety}")
        allowed_types = {{"full_payment": "pay_in_full_now", "partial_payment": "partial_then_remainder",
                          "installments": "installments", "wait": "wait"}.get(method)
                         for method in self.assistant._profile_allowed_methods()}
        other_safe_items = [x for x in options if x.get("safe") and x is not chosen
                            and x.get("type") in allowed_types]
        other_safe_names = [x["type"].replace("_", " ") for x in other_safe_items]
        earliest = situation.get("earliest_date_for_full_payment")
        safe_today = self._floor_cents(float(result["decision"].get("amount_safe_to_pay", 0) or 0))
        spending_changes = result["decision"].get("spending_changes_needed", "none")
        shortest_plan = installment_completion_date(self.as_of_date, min(self.installment_counts))
        alts = []

        if unmet_deadline:
            alts.append(
                f"No payment method you would consider completes by {self.form['pay_by_date']}; "
                f"the shortest allowed plan ends on {shortest_plan}, after the deadline.")
            alts.append(f"Remove the deadline or pick a date on or after {shortest_plan} to see a recommended plan.")
            if other_safe_names:
                alts.append(f"Safe alternative options: {', '.join(other_safe_names)}.")
        elif verdict.startswith("PAY IN FULL TODAY"):
            if other_safe_names:
                alts.append(f"Other safe payment structures: {', '.join(other_safe_names)}.")
            else:
                alts.append("Paying in full today is your most direct and cost-effective safe option.")
            buffer = situation["lowest_projected_balance"] - minimum
            if buffer > 0.005:
                alts.append(
                    f"Projected lowest balance stays {self._money(buffer, currency)} above your required minimum "
                    f"on {situation['lowest_projected_date']}."
                )
            else:
                alts.append(
                    f"Projected lowest balance stays exactly at your required minimum "
                    f"on {situation['lowest_projected_date']}."
                )
        elif verdict.startswith("NOT RECOMMENDED"):
            snapshot = getattr(self, "_snapshot", None)
            if snapshot and snapshot.get("skip"):
                alts.append(f"What would fit: {snapshot['fit']}.")
                alts.append("Ask about a smaller amount.")
            split_eval = result.get("split_evaluation") or {}
            if split_eval and not split_eval.get("suggested"):
                horizon = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
                n_max = split_eval.get("n_max", 0)
                if n_max < 2:
                    alts.append("Splitting into equal payments is not possible: payments must be at least 7 days apart.")
                else:
                    alts.append(f"No equal-payment split up to {n_max} payments keeps your balance above the minimum.")
                alts.append(f"Pick a later deadline (up to {horizon}) or pay the safe amount today instead.")
            full_now = next((x for x in options if x.get("safe") and x["type"] == "pay_in_full_now"), None)
            if full_now and "full_payment" not in self.assistant._profile_allowed_methods():
                alts.append("Paying in full today would be safe, but it isn't among the payment methods you accept.")
            if safe_today > 0:
                alts.append(f"Amount safe to pay today: {self._money(safe_today, currency)} (downsized purchase).")
            if earliest and earliest > self.as_of_date:
                alts.append(f"Earliest safe date to wait for: {earliest}.")
            if other_safe_names:
                alts.append(f"Safe alternative options: {', '.join(other_safe_names)}.")
            if spending_changes != "none":
                alts.append(f"Spending changes that would free cash: {spending_changes}.")
            if not alts:
                alts.append("No safe alternative within the 90-day forecast. Consider reducing the amount or deferring.")
        elif "WAIT" in verdict:
            if safe_today > 0:
                alts.append(f"Amount safe to pay today: {self._money(safe_today, currency)} (partial payment).")
            if other_safe_names:
                alts.append(f"Other safe options: {', '.join(other_safe_names)}.")
            if spending_changes != "none":
                alts.append(f"Spending changes to enable paying sooner: {spending_changes}.")
            if not alts:
                alts.append(f"Waiting until {earliest} avoids dropping below your required minimum balance.")
        elif verdict.startswith("SPLIT INTO"):
            if other_safe_names:
                alts.append(f"Other safe options: {', '.join(other_safe_names)}.")
            full_now = next((x for x in options if x.get("safe") and x["type"] == "pay_in_full_now"), None)
            if full_now and "full_payment" in self.assistant._profile_allowed_methods():
                alts.append("Paying in full today is also safe and avoids any fee.")
            if not alts:
                alts.append("The split spreads the payment so your balance never dips below the minimum.")
        else:
            if ("full_payment" in self.assistant._profile_allowed_methods()
                    and earliest and earliest > self.as_of_date):
                alts.append(f"Full payment alternative: wait until {earliest} to pay in full and avoid fees.")
            if other_safe_names:
                alts.append(f"Other safe options: {', '.join(other_safe_names)}.")
            if spending_changes != "none":
                alts.append(f"Spending changes that would free cash: {spending_changes}.")
            if not alts:
                earlier_safe = [x for x in options if x.get("safe") and x is not chosen
                                and chosen and max(x["dates"]) < max(chosen["dates"])]
                full_now = next((x for x in earlier_safe if x["type"] == "pay_in_full_now"), None)
                if full_now and "full_payment" not in self.assistant._profile_allowed_methods():
                    alts.append("Paying in full today would be safe, but it isn't among the payment "
                                "methods you accept.")
                elif earlier_safe:
                    alts.append("Earlier options exist but aren't among the payment methods you accept.")
                else:
                    alts.append("The selected plan is the earliest way to proceed safely without overdraft risk.")

        if unmet_deadline:
            steps = [
                f"Do not start this plan: nothing you would consider completes by {self.form['pay_by_date']}.",
                f"Pick a deadline on or after {shortest_plan}, or remove the deadline, then ask again.",
                "This answer could change if you move the deadline or choose fewer installments.",
            ]
        elif verdict.startswith("NOT RECOMMENDED"):
            steps = ["Do not make this payment now"]
            if getattr(self, "_snapshot", None) and self._snapshot.get("skip"):
                steps.append("Ask about a smaller amount to see a plan for it.")
            if safe_today > 0:
                steps.append(f"Amount safe to pay today: {self._money(safe_today, currency)}.")
            if earliest:
                steps.append(f"Earliest safe date: {earliest}.")
            steps.append(
                "This answer could change if income resumes, you choose a smaller amount, "
                f"or spending changes ({spending_changes})."
            )
        elif verdict.startswith("PAY IN FULL TODAY"):
            steps = [
                f"Confirm the selected plan for {self._money(self.form['amount'], currency)}.",
                f"Keep the minimum balance of {self._money(minimum, currency)} available in your account.",
                "Recheck the forecast before making the payment if unexpected expenses occur.",
            ]
        elif "WAIT" in verdict:
            chosen_date = max(chosen["dates"]) if chosen and chosen.get("dates") else (earliest or "the safe date")
            steps = [
                f"Hold the payment until {chosen_date}; do not pay in full today.",
                f"Ensure minimum balance of {self._money(minimum, currency)} remains protected.",
                "Recheck your balance before the safe date to ensure expected income arrived.",
            ]
        elif verdict.startswith("SPLIT INTO"):
            count = chosen.get("count", 2)
            entries = sorted((chosen.get("dates") or {}).items())
            per = chosen.get("per_payment", 0)
            last = entries[-1][1] if entries else per
            date_text = ", ".join(day for day, _value in entries)
            steps = [
                f"Confirm the {count}-payment split for {self._money(self.form['amount'], currency)}.",
                f"Pay {self._money(per, currency)} on {date_text}; the last payment is {self._money(last, currency)}.",
                f"Total with the {float(self.form.get('split_fee_pct') or 0):g}% fee is {self._money(chosen.get('total_cost', 0), currency)}; keep {self._money(minimum, currency)} available.",
            ]
        elif "INSTALLMENTS" in verdict:
            count = self.form.get("installment_count", 3)
            steps = [
                f"Confirm the {count}-installment plan for {self._money(self.form['amount'], currency)}.",
                f"Ensure sufficient funds on each monthly due date while maintaining {self._money(minimum, currency)}.",
                "Recheck the forecast before making each scheduled payment.",
            ]
        else:
            steps = [
                f"Confirm the selected plan for {self._money(self.form['amount'], currency)}.",
                f"Keep the minimum balance of {self._money(minimum, currency)} available.",
                "Recheck the forecast before making the payment.",
            ]
        dates = list(balances)
        start_date = dates[0] if dates else self.as_of_date
        end_date = dates[-1] if dates else (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
        start_balance = vals[0] if vals else situation["current_balance"]
        end_balance = vals[-1] if vals else situation["current_balance"]
        return (f"╭─ {verdict} ─╮\nWHY\n{why}\n\nNUMBERS\n90-day balance: {spark}\nMinimum-balance level: {self._money(minimum,currency)}\n{start_date} {self._money(start_balance,currency)} → {end_date} {self._money(end_balance,currency)}\n\nOPTIONS\n" + "\n".join(rows) + "\n\nALTERNATIVES\n" + "\n".join("• "+s for s in alts) + "\n\nNEXT STEPS\n" + "\n".join("• "+s for s in steps) + "\n╰" + "─" * max(10, len(verdict) + 4) + "╯")

    def report_data(self, result):
        rendered = self.render_report(result)
        verdict = rendered.split("╭─ ", 1)[1].split(" ─╮", 1)[0]
        chosen = result.get("recommended_option")
        if self._unmet_deadline(result):
            status = "bad"
        elif not_affordable := (str(result.get("decision", {}).get("affordability_status")) == "not_affordable"):
            status = "bad"
        elif chosen is None or not chosen.get("safe", False):
            status = "bad"
        elif chosen.get("type") == "wait":
            status = "caution"
        else:
            status = "good"
        situation = result["situation"]
        balances = result.get("evidence", {}).get("balances", {})
        values = list(balances.values())
        dates = list(balances)
        options = []
        for item in result.get("ranked_options", []):
            low = item.get("lowest_projected_balance")
            options.append({"name": item.get("label") or item.get("type", "").replace("_", " "), "when": ", ".join(item.get("dates", [])),
                            "cost": self._money(item.get("total_cost", 0), self.form["currency"]),
                            "lowest": self._money(low, self.form["currency"]),
                            "meets_deadline": "n/a" if item.get("meets_deadline") is None else ("yes" if item.get("meets_deadline") else "no"),
                            "safe": bool(item.get("safe")),
                            "note": "" if item.get("safe") else f"Unsafe: drops to {self._money(low, self.form['currency'])} on {item.get('lowest_projected_date')}"})
        sections = rendered.split("\n\nALTERNATIVES\n")
        alternatives = sections[-1].split("\n\nNEXT STEPS\n")[0].splitlines() if len(sections) > 1 else []
        next_steps = rendered.split("\n\nNEXT STEPS\n", 1)[-1].split("\n╰", 1)[0].splitlines()
        return {"verdict": verdict, "status": status,
                "why": rendered.split("WHY\n", 1)[-1].split("\n\nNUMBERS", 1)[0],
                "numbers": {"sparkline_values": values, "minimum": situation["minimum_balance"],
                            "start_date": dates[0] if dates else self.as_of_date,
                            "end_date": dates[-1] if dates else self.as_of_date,
                            "start_balance": values[0] if values else situation["current_balance"],
                            "end_balance": values[-1] if values else situation["current_balance"]},
                "options": options, "alternatives": alternatives, "next_steps": next_steps}

    def _handle_account_inquiry(self, text):
        low = text.lower()
        is_balance = bool(re.search(
            r"\b(?:bank\s+)?balance\b|\bhow\s+much\s+(?:do\s+i\s+have|money\s+(?:do\s+i\s+have|in\s+(?:my\s+)?account))\b",
            low,
        ))
        is_afford = bool(re.search(
            r"\b(?:most\s+expensive(?:\s+thing)?|how\s+much\s+can\s+i\s+(?:afford|spend)|what\s+(?:is\s+the\s+most\s+expensive\s+thing\s+)?(?:i\s+can\s+afford|can\s+i\s+afford)|afford\s+right\s+now|maximum\s+(?:purchase|amount|payment))\b",
            low,
        ))
        if not (is_balance or is_afford):
            return None
        situation = self.assistant.situation()
        currency = str(self.assistant.engine.get_user_home_currency(self.user_id))
        curr_bal = situation["current_balance"]
        min_bal = situation["minimum_balance"]
        low_bal = situation["lowest_projected_balance"]
        low_date = situation["lowest_projected_date"]
        capacity = self._floor_cents(max(0.0, low_bal - min_bal))
        if is_balance and is_afford:
            resp = (
                f"Your current available balance is {fmt_money(curr_bal, currency)}. "
                f"The most expensive payment you can safely afford right now is {fmt_money(capacity, currency)}. "
                f"Over the next 90 days, your lowest projected balance is {fmt_money(low_bal, currency)} on {low_date}, "
                f"keeping you at or above your required minimum balance of {fmt_money(min_bal, currency)}."
            )
        elif is_balance:
            resp = (
                f"Your current available balance is {fmt_money(curr_bal, currency)} "
                f"(minimum balance requirement: {fmt_money(min_bal, currency)})."
            )
        else:
            resp = (
                f"The most expensive payment you can safely afford right now is {fmt_money(capacity, currency)}. "
                f"Over the next 90 days, your lowest projected balance is {fmt_money(low_bal, currency)} on {low_date}, "
                f"leaving a safe margin above your required minimum balance of {fmt_money(min_bal, currency)}."
            )
        self._block("text", resp)
        return self.last_blocks

    def _is_new_request_in_reported_phase(self, text):
        amount, _ = parse_amount(text)
        if amount is None:
            return False
        low = text.lower().strip()
        if re.search(r"\b(actually|make it)\b", low):
            return False
        if re.search(r"\bpay\s+(?:\d+(?:\.\d+)?%|[\d,.]+(?:\s*(?:k|thousand))?)\s+now\b", low):
            return False
        if re.match(r"^(?:wait\b|a month later|half now|pay half now)", low):
            return False
        if re.fullmatch(r"(?:3|6|12)\s+installments?", low):
            return False
        if re.fullmatch(r"by\s+20\d\d-\d\d-\d\d", low):
            return False
        if _is_installment_count_followup(low):
            # "change number of installments to 3" & co. amend the prior request.
            return False
        if self.form.get("allow_split"):
            # Bare split amendments route to the split follow-up, not a new request.
            if re.fullmatch(r"(?:make it\s+)?\d+\s+payments?", low):
                return False
            if re.fullmatch(r"fee\s+\d+(?:\.\d+)?\s*%?", low):
                return False
        if (self.report is not None and not _CURRENCY_RE.search(low)
                and not _PURCHASE_CUE_RE.search(low)
                and re.search(r"\b(?:to|be|into)\s+\d", low)):
            # A bare number after to/be/into with no currency and no purchase cue
            # is never a new amount for a fresh request.
            return False
        return True

    def handle(self, line):
        self.last_blocks = []
        text = str(line or "").strip()
        command = text.lower()
        if command.startswith("/help"):
            self._block("text", "Start with an amount and optional currency. Intake asks only unanswered questions; Enter accepts defaults and /skip uses defaults. Commands: /user <id>, /users [currency], /date YYYY-MM-DD, /situation, /why, /reset, /export, /help, /skip. Use /export after a report to get plain text.")
        elif command == "/why":
            self._block("text", self._why_text())
        elif command == "/users" or command.startswith("/users "):
            requested = text.split(maxsplit=1)[1].upper() if len(text.split()) > 1 else None
            profiles = self.assistant.engine.profiles
            if requested:
                profiles = profiles[profiles.home_currency.astype(str).str.upper().eq(requested)]
            lines = [f"{uid} · {row.home_currency} · {fmt_money(row.current_available_balance, row.home_currency)}"
                     for uid, row in profiles.head(8).iterrows()]
            self._block("text", "Accounts" + (f" in {requested}" if requested else "") + ":\n" + ("\n".join(lines) or "No matching accounts."))
        elif command == "/export":
            if self.report:
                self._block("export", self.render_report(self.report))
            else:
                self._block("text", "No report to export yet. Ask an amount first.")
        elif command == "/situation":
            s = self.assistant.situation(); c = self.assistant.engine.get_user_home_currency(self.user_id)
            outflows = s.get("committed_outflows_next_30_days", s.get("outflows_next_30_days", s.get("outflows_next_30", 0)))
            outflows90 = s.get("committed_outflows_next_90_days", s.get("outflows_next_90_days", s.get("outflows_next_90", 0)))
            pending = s.get("pending_items", s.get("pending_items_considered_or_ignored", {}))
            self._block("text", (f"Balance: {fmt_money(s['current_balance'],c)}; minimum: {fmt_money(s['minimum_balance'],c)}; "
                                  f"lowest projected balance: {fmt_money(s['lowest_projected_balance'],c)} on {s.get('lowest_projected_date', 'n/a')}; "
                                  f"income counted: {fmt_money(s.get('monthly_income_total', 0),c)}; "
                                  f"outflows next 30 days: {fmt_money(outflows,c)}; next 90 days: {fmt_money(outflows90,c)}; "
                                  f"pending items: {pending}; as of {self.as_of_date}."))
        elif command == "/reset":
            self.phase = "ready"; self.form = {}; self.questions = []; self.report = None
            self._conversion_note = None
            self._snapshot = None
            self._block("text", "Session reset. What are you considering?")
        elif command == "/user": self._block("text", "Usage: /user <id>")
        elif command.startswith("/user "):
            new_user = text.split(maxsplit=1)[1]
            if new_user not in self.assistant.engine.profiles.index.astype(str): self._block("error", f"Unknown user id: {new_user}")
            else:
                self.__init__(new_user, None, self.dataset_dir)
                first, last = self._event_range()
                note = self.as_of_note or ""
                self._block("text", f"User set to {new_user}. Data range: {first} through {last}. {note}")
        elif command == "/date": self._block("text", "Usage: /date YYYY-MM-DD")
        elif command.startswith("/date "):
            value = text.split(maxsplit=1)[1]
            first, last = self._event_range()
            try:
                last_day = dt.date.fromisoformat(last)
            except (TypeError, ValueError):
                last_day = dt.date.today()
            hi = (last_day + dt.timedelta(days=1)).isoformat()
            try:
                date = dt.date.fromisoformat(value)
                if not (first and first <= value <= hi): raise ValueError
                self.__init__(self.user_id, date.isoformat(), self.dataset_dir)
                self._block("text", f"As-of date set to {value}.")
            except Exception: self._block("error", f"Invalid date. Allowed range for {self.user_id}: {first} through {hi}.")
        elif self.phase == "intake" and command == "/skip":
            self.question_index = len(self.questions)
            self._make_report()
        elif self.phase == "amount_clarification":
            amount, _ = parse_amount(text)
            if amount not in getattr(self, "_amount_choices", []):
                self._block("prompt", f"Which amount did you mean: {' or '.join(fmt_money(x, self.assistant.engine.get_user_home_currency(self.user_id)) for x in self._amount_choices)}?")
            else:
                self.phase = "ready"
                self._forced_amount = amount
                self._start(self._ambiguous_line)
        elif self.phase == "intake":
            new_amount, _ = parse_amount(text)
            current = self.questions[self.question_index] if self.question_index < len(self.questions) else None
            is_answer = (text in {"", "1", "2", "3", "4", "5"}
                         or (current == "fee" and bool(re.fullmatch(r"\d+(?:\.\d+)?", text)))
                         or (current == "split_offer" and text in {"", "1", "2"})
                         or (current == "split_fee" and (text == "" or bool(re.fullmatch(r"\d+(?:\.\d+)?", text))))
                         or (current == "partial_amount" and (text == "" or parse_amount(text)[0] is not None)))
            if (self.question_index >= len(self.questions)
                    or self.questions[self.question_index] not in {"fee", "split_fee"}) and re.search(r"(?<!\w)-\s*\d", text):
                self._block("error", "Amounts must be positive; please enter an amount above zero.")
            elif new_amount is not None and not is_answer:
                self._block("text", "Starting a new request and resetting the current intake.")
                self.phase = "ready"
                self.form = {}; self.questions = []; self.question_index = 0
                self._start(text)
            else:
                self._accept_answer(text)
        elif self.phase == "reported":
            if re.search(r"\bwhy\s+not\s+now\b|\bnot\s+now\b", text, re.I) and self.report:
                self._block("text", self._why_not_now_text())
            elif re.search(r"\bwhy\b", text, re.I) and self.report:
                report_text = self.render_report(self.report)
                why = report_text.split("WHY\n", 1)[-1].split("\n\nNUMBERS", 1)[0]
                self._block("text", why)
            elif (account_inquiry := self._handle_account_inquiry(text)) is not None:
                return account_inquiry
            elif self._is_new_request_in_reported_phase(text):
                self.phase = "ready"
                self.form = {}
                self.questions = []
                self.question_index = 0
                self.invalid_attempts = 0
                self.report = None
                return self._start(text)
            else:
                previous = self._comparison(self.report) if self.report else None
                outcome = self._apply_followup(text)
                if outcome == "nochange":
                    reason = self._no_change_reason_cached
                    self._no_change_reason_cached = None
                    self._block("text", f"No change: {reason}.")
                elif outcome and self._has_installment_conflict(self.form.get("pay_by_date")):
                    # The follow-up kept the deadline but its plan no longer
                    # completes by it: resolve with the first flow's conflict
                    # question instead of recommending a late plan.
                    self.phase = "intake"
                    self.questions = ["deadline_conflict"]
                    self.question_index = 0
                    self._block("text", f"The selected {self.form['installment_count']}-installment plan conflicts with the deadline {self.form['pay_by_date']}.")
                    self._prompt()
                elif outcome:
                    blocks = self._make_report()
                    if self.report and previous:
                        low = text.lower().strip()
                        if self.report_data(self.report).get("verdict", "n/a") == self._previous_verdict:
                            comparison = self._block("text", f"No change: {self._no_change_reason(low)}.")
                        else:
                            comparison = self._block("text", "WHAT CHANGED\n" + self._comparison(self.report, previous))
                        self.last_blocks.remove(comparison)
                        self.last_blocks.insert(0, comparison)
                elif text:
                    self._block("text", "I didn't understand that. Try: 'pay half now', 'wait a month', '6 installments', 'actually 20k', or /help.")
                else:
                    self._block("text", "Type a request or a follow-up.")
        else:
            self._start(text)
        return self.last_blocks

    def _comparison(self, result, previous=None):
        result = result or {}
        current = {
            "verdict": self.report_data(result).get("verdict", "n/a"),
            "amount safe today": self._money(self._floor_cents(result.get("decision", {}).get("amount_safe_to_pay", 0) or 0)),
            "lowest balance": self._money(result.get("situation", {}).get("lowest_projected_balance", 0)),
        }
        if previous is None:
            return "\n".join(f"{key}: {value}" for key, value in current.items())
        old = {
            "verdict": self._previous_verdict,
            "amount safe today": self._previous_safe,
            "lowest balance": self._previous_low,
        }
        return "\n".join(f"{key}: {old[key]} → {value}" for key, value in current.items())

    def _split_current_count(self):
        """The count of the split plan currently on the table."""
        chosen = (self.report or {}).get("recommended_option") or {}
        if chosen.get("type") == "split" and chosen.get("count"):
            return int(chosen["count"])
        return int(self.form.get("split_count") or 2)

    def _split_amendment(self, low):
        """Follow-ups that amend an active split; None when not split-related."""
        fee = re.search(r"\bfee\s+(\d+(?:\.\d+)?)\s*%?", low)
        if fee:
            pct = float(fee.group(1))
            if not (0 <= pct <= 5):
                self._no_change_reason_cached = "the split fee must be between 0 and 5 percent"
                return "nochange"
            self.form["split_fee_pct"] = pct
            return True
        count_match = (re.search(r"\bmake it\s+(\d+)\s+payments?\b", low)
                       or re.search(r"\bsplit\s+(?:it\s+)?into\s+(\d+)\b", low)
                       or re.fullmatch(r"(\d+)\s+payments?", low))
        if count_match:
            return self._split_request_count(int(count_match.group(1)))
        if re.search(r"\bmore payments?\b", low):
            return self._split_request_count(self._split_current_count() + 1)
        if re.search(r"\bfewer payments?\b", low):
            return self._split_request_count(self._split_current_count() - 1)
        return None

    def _split_request_count(self, count):
        """Apply a requested split count; refuse unsafe or out-of-range counts."""
        count = int(count)
        if count < 2:
            self._no_change_reason_cached = "2 is the minimum; 1 would be paying in full"
            return "nochange"
        window_days = self._split_window_days()
        n_max = min(12, window_days // 7 + 1) if window_days >= 7 else 0
        if count > n_max:
            self._no_change_reason_cached = (f"payments must be at least 7 days apart; the "
                                             f"{window_days}-day window fits at most {n_max} payments")
            return "nochange"
        row = self._split_row(count, window_days, self._split_income_classes())
        if not row["safe"]:
            self._no_change_reason_cached = (
                f"{count} payments would drop the balance to "
                f"{self._money(row['lowest_projected_balance'])} on "
                f"{row['lowest_projected_date']}, below your minimum")
            return "nochange"
        self.form["split_count"] = count
        return True

    def _apply_followup(self, text):
        value = str(text or "").strip()
        if not value:
            return False
        low = value.lower()
        allowed = self.assistant._profile_allowed_methods()
        # A follow-up asking for a method the profile excludes can never change
        # the verdict (the engine only evaluates accepted methods), so the form
        # stays untouched and the answer explains that instead of recomputing.
        if re.search(r"\bwait\b|\ba month later\b", low) and "full_payment" not in allowed:
            self._no_change_reason_cached = self._no_change_reason(low)
            return "nochange"
        if ("half" in low or re.search(r"\bpay\s+\d+(?:\.\d+)?\s*%\s+now\b", low)
                or re.search(r"\bpay\s+[\d,.]+\s*(?:k|thousand)?\s+now\b", low)) and "partial_payment" not in allowed:
            self._no_change_reason_cached = self._no_change_reason(low)
            return "nochange"
        count_ahead = _followup_count(low)
        if count_ahead and "installments" not in allowed:
            self._no_change_reason_cached = self._no_change_reason(low)
            return "nochange"
        # Preserve the old report values before updating the shared form.
        self._previous_verdict = self.report_data(self.report).get("verdict", "n/a")
        self._previous_safe = self._money(self.report.get("decision", {}).get("amount_safe_to_pay", 0))
        self._previous_low = self._money(self.report.get("situation", {}).get("lowest_projected_balance", 0))
        if self.form.get("allow_split"):
            outcome = self._split_amendment(low)
            if outcome is not None:
                return outcome
        if "half" in low or re.search(r"\bpay\s+\d+(?:\.\d+)?\s*%\s+now\b", low) or re.search(r"\bpay\s+[\d,.]+\s*(?:k|thousand)?\s+now\b", low):
            self.form["allow_partial"] = True
            self.form["allow_installments"] = False
            self.form.pop("allow_split", None)
            self.form.pop("split_count", None)
            percent = re.search(r"\bpay\s+(\d+(?:\.\d+)?)\s*%\s+now\b", low)
            explicit = re.search(r"\bpay\s+([\d,.]+)\s*(k|thousand)?\s+now\b", low)
            if percent:
                self.form["partial_amount"] = self.form["amount"] * float(percent.group(1)) / 100
            elif explicit:
                raw = explicit.group(1) + (explicit.group(2) or "")
                partial, partial_currency = parse_amount(raw)
                if partial is not None:
                    home_amount, _ = self.assistant._home_amount(partial, partial_currency or self.form["currency"])
                    request_home, _ = self.assistant._home_amount(self.form["amount"], self.form["currency"])
                    self.form["partial_amount"] = (self.form["amount"] * home_amount / request_home
                                                    if home_amount is not None and request_home else partial)
            elif "half" in low:
                self.form["partial_amount"] = self.form["amount"] * 0.5
            return True
        count = _followup_count(low)
        if count:
            # Amending the installment count keeps the same amount, item and
            # deadline; it never re-asks intake questions.
            self.form.update(allow_installments=True, installment_count=count, allow_partial=False)
            self.form.pop("installments_declined", None)
            self.form.pop("allow_split", None)
            self.form.pop("split_count", None)
            self._installment_count_given = True
            self._installment_count_only = False
            cap = self.assistant._max_installment_months()
            if cap and self.form["installment_count"] > cap:
                self.form["installment_count"] = cap
                self._block("text", f"Your profile allows at most {cap} installments; using {cap}.")
            return True
        if re.search(r"\bwait\b|\ba month later\b", low):
            date = dt.date.fromisoformat(self.as_of_date)
            amount = re.search(r"\b(\d+)\s*(days?|weeks?|months?)\b", low)
            if amount:
                n, unit = int(amount.group(1)), amount.group(2)
                if unit.startswith("day"): date += dt.timedelta(days=n)
                elif unit.startswith("week"): date += dt.timedelta(weeks=n)
                else:
                    month = date.month - 1 + n
                    date = date.replace(year=date.year + month // 12, month=month % 12 + 1, day=min(date.day, calendar.monthrange(date.year + month // 12, month % 12 + 1)[1]))
            elif "month" in low: date = date.replace(year=date.year + (date.month == 12), month=date.month % 12 + 1, day=min(date.day, calendar.monthrange(date.year + (date.month == 12), date.month % 12 + 1)[1]))
            else: return False
            self.form["pay_by_date"] = date.isoformat()
            self.form["allow_partial"] = False
            self.form["allow_installments"] = False
            self.form.pop("allow_split", None)
            self.form.pop("split_count", None)
            return True
        iso = re.search(r"\bby\s+(20\d\d-\d\d-\d\d)\b", low)
        if iso:
            horizon = (dt.date.fromisoformat(self.as_of_date) + dt.timedelta(days=90)).isoformat()
            if iso.group(1) > horizon:
                self._no_change_reason_cached = (f"deadlines can be at most 90 days ahead (until {horizon}); "
                                                 f"pick a date up to then, or choose no deadline")
                return "nochange"
            self.form["pay_by_date"] = iso.group(1)
            return True
        new_amount, currency = parse_amount(value)
        if new_amount is not None and re.search(r"\b(actually|make it)\b", low):
            self.form["amount"] = new_amount
            if currency: self.form["currency"] = currency
            # An amended amount in another currency is converted once as well.
            self._conversion_note = None
            self._normalize_currency()
            return True
        return False

    def _why_text(self):
        result = self.latest_result
        if not result:
            return "No completed answer yet. Ask an amount first."
        start = dt.date.fromisoformat(self.as_of_date)
        events = self.assistant.engine.events
        user_events = events[events.user_id.astype(str).eq(self.user_id)]
        evidence = result.get("evidence", {})
        balance_dates = sorted(evidence.get("balances", {}))
        window_start = balance_dates[0] if balance_dates else start.isoformat()
        window_end = balance_dates[-1] if balance_dates else (start + dt.timedelta(days=90)).isoformat()
        breakdown = evidence.get("breakdown", {})
        decisions = result.get("income_stream_classes", [])
        if not decisions:
            decisions = breakdown.get("income_decisions", [])
        if not decisions:
            decisions = evidence.get("explanation_evidence", {}).get("income_decisions", [])
        income_lines = [f"{entry.get('series_key', 'unknown stream')}: {entry.get('decision', 'unknown')} — {entry.get('reason', 'reason unavailable')}"
                        for entry in decisions]
        messages = evidence.get("retrieved_message_ids", [])
        if not messages:
            messages = evidence.get("explanation_evidence", {}).get("retrieved_message_ids", [])
        option_names = [option.get("type", "unknown") for option in result.get("ranked_options", [])]
        return (f"Data loaded: {len(user_events)} events for {self.user_id}; as-of {self.as_of_date}; "
                f"90-day window {window_start} through {window_end}.\n"
                f"Income streams/classes: {'; '.join(income_lines) if income_lines else 'No stream-class detail in engine evidence.'}\n"
                f"Retrieved message IDs: {', '.join(map(str, messages)) if messages else 'none in engine evidence.'}\n"
                f"Options considered: {', '.join(option_names) if option_names else 'none'}.\n"
                f"Wording: {self.latest_wording}.\nFailproof session: {self.session_id}")
