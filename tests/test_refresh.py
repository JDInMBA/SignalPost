"""Refresh must be idempotent, preserve prior values and type its changes."""
from __future__ import annotations

import copy

from signalpost.envelope import Availability, Claim, CompanyEnvelope, RunMeta, utcnow
from signalpost.refresh import canonical, carry_forward, diff


def env(claims) -> CompanyEnvelope:
    e = CompanyEnvelope(organisation_number="810034882",
                        run=RunMeta(run_id="r", agent_version="a", started_at=utcnow()))
    e.claims = claims
    return e


def base():
    return [
        Claim(field="leadership", family="leadership", availability=Availability.AVAILABLE,
              value=[{"name": "A", "role": "Daglig leder"}, {"name": "B", "role": "Styreleder"}]),
        Claim(field="official_website", family="website", availability=Availability.AVAILABLE,
              value={"url": "https://x.no", "domain": "x.no"}),
    ]


def test_same_snapshot_produces_no_changes():
    prev = env(base())
    assert diff(prev, env(copy.deepcopy(base()))) == []


def test_reordering_a_list_is_not_a_change():
    prev = env(base())
    shuffled = copy.deepcopy(base())
    shuffled[0].value = list(reversed(shuffled[0].value))
    assert diff(prev, env(shuffled)) == []


def test_volatile_fields_do_not_create_false_changes():
    prev = env(base())
    noisy = copy.deepcopy(base())
    noisy[1].value = {"url": "https://x.no", "domain": "x.no",
                      "verification": {"confidence": 0.8}, "retrieved_at": "later"}
    assert diff(prev, env(noisy)) == []


def test_role_replacement_emits_typed_changes():
    prev = env(base())
    changed = copy.deepcopy(base())
    changed[0].value = [{"name": "A", "role": "Daglig leder"}, {"name": "C", "role": "Styreleder"}]
    kinds = sorted(c.change_type for c in diff(prev, env(changed)))
    assert kinds == ["new_role", "removed_role"]


def test_blocked_source_keeps_the_last_supported_value():
    prev = env(base())
    blocked = copy.deepcopy(base())
    blocked[1].availability = Availability.BLOCKED
    blocked[1].value = None
    current = env(blocked)
    carry_forward(prev, current)
    assert current.claims[1].value == {"url": "https://x.no", "domain": "x.no"}
    assert current.claims[1].confidence <= 0.6
    assert "last supported value retained" in (current.claims[1].note or "")


def test_canonical_drops_volatile_keys():
    assert canonical({"a": 1, "retrieved_at": "x"}) == {"a": 1}


def test_carry_forward_brings_the_evidence_it_references():
    """A restored value must arrive with the evidence record behind it.

    Copying only the ids leaves every carried-forward claim pointing at evidence
    that is not in this envelope, which validate_integrity rejects and which makes
    the claim unverifiable for a reader.
    """
    from signalpost.envelope import Evidence, SourceClass

    prev = env(base())
    prev.evidence.append(Evidence(id="ev-web-home", source_url="https://x.no",
                                  source_class=SourceClass.COMPANY_OWNED,
                                  content_sha256="a" * 64))
    prev.claims[1].evidence_ids = ["ev-web-home"]
    # Keep only the website claim so the assertion is about the carried value.
    prev.claims = [prev.claims[1]]

    blocked = copy.deepcopy(base())[1:]
    blocked[0].availability = Availability.BLOCKED
    blocked[0].value = None
    current = env(blocked)

    carry_forward(prev, current)
    assert current.claims[0].value is not None
    assert [e.id for e in current.evidence] == ["ev-web-home"]
    assert current.validate_integrity() == []
