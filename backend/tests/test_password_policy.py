"""Unit tests for the password policy.

Policy under test: at least 6 characters, with at least one uppercase letter,
one lowercase letter, and one special character. A digit is not required.
A blocklist backstop rejects weak roots dressed up to satisfy those rules.
"""

from __future__ import annotations

import pytest

from app.modules.auth.password_policy import (
    MAX_LENGTH,
    MIN_LENGTH,
    REQUIRE_DIGIT,
    PasswordPolicyError,
    describe_policy,
    validate_password,
)


def problems_for(password: str, **kwargs: str) -> list[str]:
    """Return the policy failures for ``password``, or ``[]`` if it is accepted."""
    try:
        validate_password(password, **kwargs)
    except PasswordPolicyError as exc:
        return exc.problems
    return []


class TestAcceptedPasswords:
    @pytest.mark.parametrize(
        "password",
        [
            "Ab@def",  # exactly the minimum
            "Xy#123",
            "MyDog$Rex",
            "Kite#Flyer",
            "Zq!wsx",
            "Tr0ub4dor&3",
            "Brij@Home7",
            "Testimony@1",  # must not trip the "test" blocklist root
            "Mumbai$99",
            "Ödipus@Ring",  # non-ASCII cased letters count as upper/lower
        ],
    )
    def test_accepts_conforming_passwords(self, password: str) -> None:
        assert problems_for(password) == []

    def test_caseless_scripts_cannot_satisfy_the_policy(self) -> None:
        """Documents a real limitation of requiring both letter cases.

        Devanagari, Arabic, Chinese, Japanese, Hebrew, and Thai are caseless, so
        ``str.isupper()``/``str.islower()`` are both False for every character.
        A password written wholly in one of these scripts therefore cannot
        satisfy an upper+lower requirement, no matter how long or strong it is -
        the user must mix in Latin characters.

        This is a consequence of the mandated composition rules, not a bug. The
        test exists so the behaviour is deliberate and visible rather than
        discovered by a locked-out user.
        """
        problems = problems_for("थोड़ालंबापासवर्ड@1")

        assert any("uppercase" in problem for problem in problems)
        assert any("lowercase" in problem for problem in problems)


class TestLength:
    def test_rejects_below_minimum(self) -> None:
        problems = problems_for("Ab@de")  # 5 characters
        assert any(str(MIN_LENGTH) in problem for problem in problems)

    def test_accepts_exactly_minimum(self) -> None:
        assert len("Ab@def") == MIN_LENGTH
        assert problems_for("Ab@def") == []

    def test_rejects_above_maximum(self) -> None:
        problems = problems_for("Ab@" + "x" * MAX_LENGTH)
        assert any(str(MAX_LENGTH) in problem for problem in problems)


class TestCompositionRules:
    """The four required rules, each verified in isolation."""

    def test_requires_uppercase(self) -> None:
        problems = problems_for("abc@def")
        assert any("uppercase" in problem for problem in problems)

    def test_requires_lowercase(self) -> None:
        problems = problems_for("ABC@DEF")
        assert any("lowercase" in problem for problem in problems)

    def test_requires_special_character(self) -> None:
        problems = problems_for("AbcDefg")
        assert any("special" in problem for problem in problems)

    def test_digit_not_required(self) -> None:
        """A digit is deliberately not part of this policy."""
        assert REQUIRE_DIGIT is False
        assert problems_for("Kite#Flyer") == []

    def test_whitespace_is_not_a_special_character(self) -> None:
        """A space the user cannot see is not a usable second factor of variety."""
        problems = problems_for("Ab cdef")
        assert any("special" in problem for problem in problems)

    def test_rejects_surrounding_whitespace(self) -> None:
        """A pasted password with stray spaces fails at login, unexplainably."""
        problems = problems_for(" Ab@def ")
        assert any("space" in problem for problem in problems)


class TestBlocklistBackstop:
    """Composition rules alone admit the most-guessed passwords in existence.

    Each case below satisfies all four required rules and must still be rejected.
    """

    @pytest.mark.parametrize(
        "password",
        [
            "Password@1",  # padding appended
            "P@ssw0rd",  # leetspeak inside the word
            "Passw0rd!",  # leetspeak inside *and* padding appended
            "Admin@123",
            "Welcome@1",
            "Qwerty@1",
            "LetMeIn@1",
            "Master@99",
            "Secret@1",
            "Changeme@1",
            "Tally@2024",  # product names are the first thing tried
            "Personal@Erp",
        ],
    )
    def test_rejects_dressed_up_common_passwords(self, password: str) -> None:
        problems = problems_for(password)
        assert any("commonly used" in problem for problem in problems), (
            f"{password!r} satisfies the composition rules and was not blocked"
        )

    def test_unrelated_password_not_falsely_blocked(self) -> None:
        assert problems_for("Ganga!Ram") == []


class TestPersonalInformation:
    def test_rejects_password_containing_email_local_part(self) -> None:
        problems = problems_for("Priyasharma@1", email="priyasharma@acme.test")
        assert any("email" in problem for problem in problems)

    def test_rejects_password_containing_name(self) -> None:
        problems = problems_for("Sharma@Ledger", full_name="Jhon Doe")
        assert any("name" in problem for problem in problems)

    def test_short_name_parts_do_not_false_positive(self) -> None:
        """Two- and three-letter fragments would match almost anything."""
        assert problems_for("Kite#Flyer", full_name="Jo Li") == []

    def test_unrelated_password_accepted(self) -> None:
        assert (
            problems_for(
                "Kite#Flyer",
                email="priyasharma@acme.test",
                full_name="Jhon Doe",
            )
            == []
        )


class TestReporting:
    def test_reports_every_problem_at_once(self) -> None:
        """One round trip should tell the user everything that is wrong.

        ``admin`` breaks three rules simultaneously - too short, no uppercase, no
        special character, and blocklisted - so a policy that stopped at the
        first failure would report only one of them.
        """
        problems = problems_for("admin")

        assert len(problems) >= 3, problems
        assert any(str(MIN_LENGTH) in problem for problem in problems)
        assert any("uppercase" in problem for problem in problems)
        assert any("special" in problem for problem in problems)

    def test_describe_policy_matches_enforcement(self) -> None:
        """The advertised policy must not drift from the enforced one."""
        policy = describe_policy()

        assert policy["min_length"] == MIN_LENGTH
        assert policy["max_length"] == MAX_LENGTH
        assert policy["requires_uppercase"] is True
        assert policy["requires_lowercase"] is True
        assert policy["requires_special"] is True
        assert policy["requires_digit"] is REQUIRE_DIGIT
        assert isinstance(policy["rules"], list) and policy["rules"]

    def test_advertised_special_characters_are_actually_accepted(self) -> None:
        """Every character the API advertises must genuinely satisfy the rule."""
        advertised = str(describe_policy()["special_characters"])
        assert advertised

        for char in advertised:
            assert problems_for(f"Abcde{char}") == [], (
                f"{char!r} is advertised as special but was not accepted"
            )
