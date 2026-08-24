"""Password policy.

Composition-rule policy, as specified for this project:

* **6 characters minimum**
* **at least one uppercase letter**
* **at least one lowercase letter**
* **at least one special character**

A digit is *not* required. ``REQUIRE_DIGIT`` below turns that on as a one-line
change if that decision is revisited.

One rule is enforced beyond the four above, and it is deliberate. Composition
requirements are satisfied by exactly the passwords that cracking dictionaries
enumerate first - ``Password@1`` clears every check here at ten characters. So a
blocklist runs as a backstop, matching on the letters-only root of the password
(``P@ssword1`` → ``password`` → rejected). Without it the four rules above would
admit the most-guessed credentials in existence. It is isolated in
:func:`_blocklist_problems` and can be dropped by deleting that one call.

All failures are collected and raised together, so the user fixes everything in
one attempt rather than discovering rules one at a time.
"""

from __future__ import annotations

import re
import string
from typing import Final

# ---------------------------------------------------------------------------
# Policy knobs
# ---------------------------------------------------------------------------
MIN_LENGTH: Final = 6

#: Argon2 rejects inputs beyond roughly a kilobyte; 128 characters is far below
#: that and keeps the field sane for a UI.
MAX_LENGTH: Final = 128

REQUIRE_UPPERCASE: Final = True
REQUIRE_LOWERCASE: Final = True
REQUIRE_SPECIAL: Final = True
#: Not requested for this project. Flip to True to require a digit as well.
REQUIRE_DIGIT: Final = False

#: What counts as "special". ``string.punctuation`` is the printable ASCII
#: punctuation set: ``!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~``
#:
#: An explicit set rather than "anything not alphanumeric", because the latter
#: silently accepts whitespace and invisible Unicode as the special character -
#: which users cannot see, retype, or debug when login later fails.
SPECIAL_CHARACTERS: Final[frozenset[str]] = frozenset(string.punctuation)


class PasswordPolicyError(ValueError):
    """Raised with every failure at once, so the user fixes them in one pass."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


# ---------------------------------------------------------------------------
# Blocklist backstop
# ---------------------------------------------------------------------------
#: Weak roots, stored letters-only and lowercase so they match regardless of the
#: capitalisation and punctuation added to satisfy the composition rules.
#:
#: A production deployment should add the Have I Been Pwned k-anonymity range
#: check in front of this (Stage 10); this list needs no network call.
COMMON_PASSWORD_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "qwerty",
        "qwertyuiop",
        "asdfghjkl",
        "zxcvbnm",
        "letmein",
        "welcome",
        "admin",
        "administrator",
        "root",
        "login",
        "iloveyou",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "monkey",
        "dragon",
        "master",
        "shadow",
        "superman",
        "batman",
        "trustno",
        "changeme",
        "secret",
        "abc",
        "abcd",
        "test",
        "temp",
        "personalerp",
        "personal",
        "erpadmin",
        "tally",
        "accounts",
        "company",
    }
)

#: Leetspeak substitutions, reversed, so ``P@ssw0rd`` normalises to ``password``.
_LEET_TABLE: Final = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
        "!": "i",
        "+": "t",
    }
)


def _candidate_roots(password: str) -> set[str]:
    """Reduce a password to the roots worth comparing against the blocklist.

    Three candidates are produced, because no single normalisation catches every
    way a weak root gets dressed up to pass the composition rules:

    * **plain** - lowercase, non-letters stripped. Catches padding appended to
      satisfy the rules: ``Password@1`` → ``password``.
    * **un-leeted** - leetspeak reversed first. Catches substitution *inside* the
      word: ``P@ssw0rd`` → ``password``.
    * **trimmed then un-leeted** - edge padding removed *before* reversing
      leetspeak. Needed because leetspeak rewrites padding into letters and
      corrupts the root: ``Passw0rd!`` un-leets to ``passwordi`` (miss), but
      trimming the ``!`` first yields ``passw0rd`` → ``password`` (hit).

    Each catches cases the others miss, so all three are checked.
    """
    lowered = password.lower()
    plain = re.sub(r"[^a-z]", "", lowered)
    unleeted = re.sub(r"[^a-z]", "", lowered.translate(_LEET_TABLE))
    # Strip non-letters from both ends only; interior digits/symbols survive so
    # leetspeak can still be reversed within the word.
    trimmed = lowered.strip(string.digits + string.punctuation + string.whitespace)
    trimmed_unleeted = re.sub(r"[^a-z]", "", trimmed.translate(_LEET_TABLE))
    return {plain, unleeted, trimmed_unleeted}


def _blocklist_problems(password: str) -> list[str]:
    """The one rule beyond the four composition requirements.

    Delete the call to this function in :func:`validate_password` to enforce
    only the literal composition policy.
    """
    roots = {root for root in _candidate_roots(password) if len(root) >= 3}
    if roots & COMMON_PASSWORD_ROOTS:
        return ["Too close to a commonly used password"]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def validate_password(
    password: str,
    *,
    email: str | None = None,
    full_name: str | None = None,
) -> None:
    """Validate a password, raising :class:`PasswordPolicyError` on failure.

    ``email`` and ``full_name`` enable the personal-information checks; pass them
    whenever they are known (registration, reset, change).
    """
    problems: list[str] = []

    if len(password) < MIN_LENGTH:
        problems.append(f"Must be at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        problems.append(f"Must be at most {MAX_LENGTH} characters")

    if password != password.strip():
        # Leading/trailing whitespace survives a paste and then fails at login
        # in a way nobody can diagnose.
        problems.append("Cannot begin or end with a space")

    if REQUIRE_UPPERCASE and not any(char.isupper() for char in password):
        problems.append("Must contain at least one uppercase letter")

    if REQUIRE_LOWERCASE and not any(char.islower() for char in password):
        problems.append("Must contain at least one lowercase letter")

    if REQUIRE_SPECIAL and not any(char in SPECIAL_CHARACTERS for char in password):
        problems.append("Must contain at least one special character (e.g. ! @ # $ %)")

    if REQUIRE_DIGIT and not any(char.isdigit() for char in password):
        problems.append("Must contain at least one digit")

    problems.extend(_blocklist_problems(password))
    problems.extend(_personal_info_problems(password.lower(), email, full_name))

    if problems:
        raise PasswordPolicyError(problems)


def _personal_info_problems(lowered: str, email: str | None, full_name: str | None) -> list[str]:
    """Reject passwords derived from the user's own identifiers.

    Targeted guessing starts with the victim's name and email, so these are
    worth blocking even under a permissive policy.
    """
    problems: list[str] = []

    if email:
        local_part = email.split("@")[0].lower()
        # Short local parts ("jo") would false-positive constantly.
        if len(local_part) >= 4 and local_part in lowered:
            problems.append("Cannot contain your email address")

    if full_name:
        for part in full_name.lower().split():
            if len(part) >= 4 and part in lowered:
                problems.append("Cannot contain your name")
                break

    return problems


def describe_policy() -> dict[str, object]:
    """Machine-readable policy for the frontend's password field.

    Served from the API so the client's hints can never contradict what the
    server actually enforces - the rules below are derived from the same
    constants :func:`validate_password` checks against.
    """
    rules = [f"At least {MIN_LENGTH} characters"]
    if REQUIRE_UPPERCASE:
        rules.append("At least one uppercase letter")
    if REQUIRE_LOWERCASE:
        rules.append("At least one lowercase letter")
    if REQUIRE_SPECIAL:
        rules.append("At least one special character (e.g. ! @ # $ %)")
    if REQUIRE_DIGIT:
        rules.append("At least one digit")
    rules.append("Not a commonly used password")
    rules.append("Must not contain your name or email address")

    return {
        "min_length": MIN_LENGTH,
        "max_length": MAX_LENGTH,
        "requires_uppercase": REQUIRE_UPPERCASE,
        "requires_lowercase": REQUIRE_LOWERCASE,
        "requires_special": REQUIRE_SPECIAL,
        "requires_digit": REQUIRE_DIGIT,
        "special_characters": "".join(sorted(SPECIAL_CHARACTERS)),
        "rules": rules,
    }
