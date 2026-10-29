"""The benchmark suite: realistic validators (dates, phones, IDs, emails, policies) with two prompts each.

Prompt 0 is bare (the model has no hint of the format); prompt 1 shows examples of the format
("few-shot"), which is the best a user can do WITHOUT a constraint. Reporting both keeps the comparison honest.
Few-shot prompts end at an ITEM BOUNDARY ("..., "), never mid-item ("EMP-"), so the model is asked to start a fresh
item rather than finish a half-written one; otherwise the baseline would fail because of the prompt, not the model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    """One validator to test: its category, how to compile it, and the prompts to try."""

    id: str
    category: str
    kind: str  # "regex" | "expression"
    text: str
    prompts: tuple[str, ...]


def _c(id_: str, category: str, text: str, bare: str, shots: str, kind: str = "regex") -> Case:
    return Case(id_, category, kind, text, (bare, shots))


CASES: tuple[Case, ...] = (
    _c(
        "iso_date",
        "dates",
        "[0-9]{4}-[0-9]{2}-[0-9]{2}",
        "Date: ",
        "Today is 2024-05-17. Tomorrow is 2024-05-18. Date: ",
    ),
    _c(
        "dmy_date",
        "dates",
        "(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/[0-9]{4}",
        "Date: ",
        "Born on 04/11/1987. Hired on 12/03/2015. Left on ",
    ),
    _c("time24", "dates", "([01][0-9]|2[0-3]):[0-5][0-9]", "Time: ", "Lunch at 12:30. Dinner at 19:45. Meeting at "),
    _c(
        "us_phone", "phones", "\\([0-9]{3}\\) [0-9]{3}-[0-9]{4}", "Phone: ", "Call (555) 123-4567 or (555) 987-6543 or "
    ),
    _c(
        "intl_phone",
        "phones",
        "\\+[0-9]{1,3} [0-9]{4,10}",
        "Phone: ",
        "Reach us on +44 2071234567 or +1 5551234567 or ",
    ),
    _c("employee_id", "ids", "EMP-[0-9]{3,5}", "Employee ID: ", "Staff: EMP-0042, EMP-1187, EMP-20931, "),
    _c("ssn_like", "ids", "[0-9]{3}-[0-9]{2}-[0-9]{4}", "ID number: ", "IDs on file: 123-45-6789, 987-65-4321, "),
    _c("passport", "ids", "[A-Z]{2}[0-9]{6}", "Passport: ", "Passports: AB123456, XY987654, "),
    _c("zip_us", "ids", "[0-9]{5}(-[0-9]{4})?", "ZIP: ", "ZIP codes: 90210, 10001-1234, 60614, "),
    _c(
        "semver",
        "ids",
        "(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)",
        "Version: ",
        "Releases: 1.0.0, 1.2.14, 2.0.1, ",
    ),
    _c(
        "email_simple",
        "emails",
        "[a-z]+@[a-z]+\\.(com|org|edu)",
        "Email: ",
        "Contacts: ann@example.com, bob@school.edu, ",
    ),
    _c("username", "emails", "[a-z][a-z0-9_]{2,15}", "Username: ", "Users: jsmith, anna_k, dev42, "),
    _c("decimal", "numbers", "-?(0|[1-9][0-9]*)(\\.[0-9]+)?", "Price: ", "Prices: 19.99, 5, 120.5, "),
    _c("bool_word", "numbers", "(true|false|null)", "Value: ", "Flags: true, false, null, "),
    _c(
        "email_policy",
        "policies",
        "email & !contains(/spam/) & length(8, 30)",
        "Contact: ",
        "Staff contacts: ann.lee@corp.com, raj@corp.com, ",
        kind="expression",
    ),
    _c(
        "time_not_midnight",
        "policies",
        "time24 - /00:00/",
        "Meeting at ",
        "Standups at 09:30, 10:00, 14:15, ",
        kind="expression",
    ),
    _c(
        "username_policy",
        "policies",
        "username & !/admin|root|test/",
        "Username: ",
        "Users: jsmith, anna_k, dev42, ",
        kind="expression",
    ),
    _c(
        "date_in_2024",
        "policies",
        "iso_date & starts(/2024-/)",
        "Date: ",
        "Dates: 2024-01-15, 2024-06-30, ",
        kind="expression",
    ),
)

# Quick subset used by `--quick` and by the slow test.
QUICK_IDS = ("iso_date", "employee_id", "bool_word")
