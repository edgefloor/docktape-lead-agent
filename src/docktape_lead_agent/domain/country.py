from __future__ import annotations

from typing import Any

from .profiles import JurisdictionFact
from .results import ComplianceCheck
from .submissions import ComplianceOutcome, JurisdictionRole, normalize_text


def evaluate_country_compliance(
    jurisdictions: list[JurisdictionFact],
    policy: dict[str, Any],
) -> ComplianceCheck:
    restricted = set(policy["country_codes"])
    configured_roles = policy.get("country_policy", {}).get(
        "screening_roles",
        [
            JurisdictionRole.REGISTERED_JURISDICTION.value,
            JurisdictionRole.CONTRACTING_ENTITY.value,
            JurisdictionRole.ULTIMATE_PARENT.value,
            JurisdictionRole.OPERATIONAL_HEADQUARTERS.value,
        ],
    )
    screening_roles = {JurisdictionRole(role) for role in configured_roles}
    relevant = [fact for fact in jurisdictions if fact.role in screening_roles]
    offices = [fact for fact in jurisdictions if fact.role == JurisdictionRole.OFFICE]

    countries_by_subject: dict[tuple[str, JurisdictionRole], set[str]] = {}
    for fact in relevant:
        key = (normalize_text(fact.entity_name), fact.role)
        countries_by_subject.setdefault(key, set()).add(fact.country)
    conflicts = [
        (entity, role, countries)
        for (entity, role), countries in countries_by_subject.items()
        if len(countries - {"unknown"}) > 1
    ]
    listed_relevant = [fact for fact in relevant if fact.country in restricted]
    if listed_relevant:
        fact = listed_relevant[0]
        return ComplianceCheck(
            kind="country",
            policy_entry="exercise_country_set",
            outcome=ComplianceOutcome.FLAGGED,
            reason=(
                f"Verified {fact.role.value} for {fact.entity_name} is in policy-listed country {fact.country}."
            ),
            evidence_id=fact.evidence_ids[0] if fact.evidence_ids else None,
        )

    if conflicts:
        entity, role, countries = conflicts[0]
        return ComplianceCheck(
            kind="country",
            policy_entry="exercise_country_set",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason=(
                f"Conflicting {role.value} countries for {entity}: {', '.join(sorted(countries))}."
            ),
        )

    listed_offices = [fact for fact in offices if fact.country in restricted]
    if listed_offices:
        fact = listed_offices[0]
        return ComplianceCheck(
            kind="country",
            policy_entry="exercise_country_set",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason=(
                f"A verified office exists in policy-listed country {fact.country}, but an office alone does not "
                "establish legal domicile or the contracting entity."
            ),
            evidence_id=fact.evidence_ids[0] if fact.evidence_ids else None,
        )

    unresolved_relevant = [fact for fact in relevant if fact.country == "unknown"]
    if unresolved_relevant:
        fact = unresolved_relevant[0]
        return ComplianceCheck(
            kind="country",
            policy_entry="exercise_country_set",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason=f"The {fact.role.value} country for {fact.entity_name} remains unresolved.",
            evidence_id=fact.evidence_ids[0] if fact.evidence_ids else None,
        )

    known_relevant = [fact for fact in relevant if fact.country != "unknown"]
    if not known_relevant:
        return ComplianceCheck(
            kind="country",
            policy_entry="exercise_country_set",
            outcome=ComplianceOutcome.REVIEW_REQUIRED,
            reason="No verified legal, contracting, parent, or operational-headquarters jurisdiction was established.",
        )

    countries = ", ".join(sorted({fact.country for fact in known_relevant}))
    office_context = " Multiple international offices are context only." if len(offices) > 1 else ""
    return ComplianceCheck(
        kind="country",
        policy_entry="exercise_country_set",
        outcome=ComplianceOutcome.CLEAR,
        reason=f"Verified relevant jurisdictions ({countries}) are outside the policy-listed country set.{office_context}",
        evidence_id=known_relevant[0].evidence_ids[0] if known_relevant[0].evidence_ids else None,
    )
