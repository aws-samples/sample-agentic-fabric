"""
Deterministic governance engine. No LLM. No external calls.

Implements the four arbitration patterns from the Arbitration Patterns companion:
1. Scope-based: tighter scope wins
2. Priority: composition contract precedence ordering
3. Conjunction: both must permit (default for sovereign domains)
4. State-aware: monotonic reduction under uncertainty

The engine is a pure Python class with no I/O. All data is passed in,
all decisions are returned. I/O (DynamoDB, SQS) happens in the Arbiter.
"""

from typing import List, Optional
from .models import (
    AuthorityUnit, CompositionContract, DispatchRequest,
    GovernanceFinding, ArbitrationDecision, CaseLawEntry,
    ConstitutionalLayer, ConflictResolution,
)


class GovernanceEngine:

    def __init__(
        self,
        authority_units: List[AuthorityUnit],
        composition_contracts: List[CompositionContract],
        case_law: List[CaseLawEntry],
        constitutional_layers: List[ConstitutionalLayer] = None,
    ):
        self.authority_units = {u.unit_id: u for u in authority_units}

        # Index units by agent_id for fast lookup
        self.agent_units: dict[str, List[AuthorityUnit]] = {}
        for u in authority_units:
            self.agent_units.setdefault(u.agent_id, []).append(u)

        # Index contracts by both party directions
        self.contracts: dict[tuple[str, str], CompositionContract] = {}
        for c in composition_contracts:
            self.contracts[(c.party_a, c.party_b)] = c
            self.contracts[(c.party_b, c.party_a)] = c

        self.case_law = sorted(case_law, key=lambda e: -e.precedence)
        self.constitutional_layers = constitutional_layers or []

    def evaluate(self, request: DispatchRequest) -> GovernanceFinding:
        """
        Main evaluation entry point. Called by the Arbiter before every dispatch.

        Evaluation order:
        1. Case law (encoded resolutions from prior escalations)
        2. Constitutional review of case law permits (case law cannot bypass the constitution)
        3. Find covering authority units
        4. Residual authority denial if none found
        5. Scope-based arbitration (Pattern 1)
        6. Composition contract evaluation (Patterns 2, 3, 4)
        7. Single-domain permit
        8. Constitutional review of all permits (mandatory, never bypassed)
        """
        # Step 1: Check case law first
        case_match = self._check_case_law(request)
        if case_match:
            finding = GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=case_match.resolution,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason=f"case_law:{case_match.case_id}",
            )
            # Step 2: Case law permits must survive constitutional review
            if finding.decision == ArbitrationDecision.PERMIT:
                constitutional_check = self._constitutional_review(request, finding)
                if constitutional_check:
                    return constitutional_check
            return finding

        # Step 3: Find authority units that cover this request
        covering_units = self._find_covering_units(request)

        # Step 4: If no unit covers the action, residual authority denies
        if not covering_units:
            return GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=ArbitrationDecision.DENY,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason="residual_authority_denial:no_scope_covers_action",
                residual_authority_denial=True,
            )

        # Step 5: Scope-based arbitration (Pattern 1) — pick the tightest scope
        best_unit = self._select_tightest_scope(covering_units)

        # Step 6: Check if this crosses a domain boundary
        contract = self._find_contract(request)
        if contract:
            finding = self._evaluate_composition(request, best_unit, contract)
        else:
            # Step 7: Single-domain permit
            finding = GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=ArbitrationDecision.PERMIT,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason=f"scope_match:{best_unit.unit_id}",
                scope_evaluated=best_unit.unit_id,
            )

        # Step 8: Constitutional review — mandatory for all permits
        # Deny at any tier is final. Permit must survive constitutional review.
        if finding.decision == ArbitrationDecision.PERMIT:
            constitutional_check = self._constitutional_review(request, finding)
            if constitutional_check:
                return constitutional_check

        return finding

    def _find_covering_units(self, request: DispatchRequest) -> List[AuthorityUnit]:
        """Return all valid, in-force authority units that cover this request."""
        units = self.agent_units.get(request.requesting_agent_id, [])
        units = units + self.agent_units.get("*", [])  # wildcard units
        return [u for u in units if u.is_valid() and u.scope.covers(request)]

    def _select_tightest_scope(self, units: List[AuthorityUnit]) -> AuthorityUnit:
        """Pattern 1: Scope-based arbitration. Most specific scope governs."""
        return max(units, key=lambda u: u.scope.specificity)

    def _find_contract(self, request: DispatchRequest) -> Optional[CompositionContract]:
        """
        Find a composition contract governing this cross-domain interaction.
        Falls through from agent-pair to domain-pair lookup, aligning with the
        architecture's claim that contracts govern domain boundaries, not agent pairs.
        """
        # Agent-pair lookup (most specific)
        contract = self.contracts.get(
            (request.requesting_agent_id, request.target_agent_id)
        )
        if contract:
            return contract

        # Domain-pair fallback: derive domains from covering authority units
        requester_domain = self._get_agent_domain(request.requesting_agent_id, request)
        target_domain = self._get_agent_domain(request.target_agent_id, request)
        if requester_domain and target_domain and requester_domain != target_domain:
            return self.contracts.get((requester_domain, target_domain))

        return None

    def _get_agent_domain(self, agent_id: str, request: DispatchRequest = None) -> Optional[str]:
        """
        Derive an agent's domain from its most specific authority unit.
        If a request is provided, filters to units covering that request
        so domain derivation is request-aware.
        """
        units = self.agent_units.get(agent_id, [])
        if request:
            valid = [u for u in units if u.is_valid() and u.scope.domain != "*" and u.scope.covers(request)]
        else:
            valid = [u for u in units if u.is_valid() and u.scope.domain != "*"]
        if valid:
            return max(valid, key=lambda u: u.scope.specificity).scope.domain
        return None

    def _evaluate_composition(
        self,
        request: DispatchRequest,
        requester_unit: AuthorityUnit,
        contract: CompositionContract,
    ) -> GovernanceFinding:
        """
        Evaluate cross-domain interaction against its composition contract.
        Implements Patterns 2 (priority), 3 (conjunction), 4 (state-aware).
        """
        # Pattern 4: State-aware — monotonic reduction under uncertainty
        # Only fires if unconfirmed keys are relevant to the contract's scope
        # or the covering authority unit's conditions
        if not self._is_state_confirmed(request, requester_unit, contract):
            return GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=ArbitrationDecision.HALT,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason="state_aware:monotonic_reduction_unconfirmed_state",
                contract_evaluated=contract.contract_id,
            )

        # Find authority units on the target side
        target_units = [
            u for u in self.agent_units.get(request.target_agent_id, [])
            if u.is_valid() and u.scope.covers(request)
        ]

        requester_permits = len(self._find_covering_units(request)) > 0
        target_permits = len(target_units) > 0

        # Pattern 2: Priority arbitration using contract precedence
        if contract.authority_precedence == request.requesting_agent_id:
            decision = ArbitrationDecision.PERMIT if requester_permits else ArbitrationDecision.DENY
            return GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=decision,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason=f"precedence:{contract.authority_precedence}",
                scope_evaluated=requester_unit.unit_id,
                contract_evaluated=contract.contract_id,
            )
        elif contract.authority_precedence == request.target_agent_id:
            decision = ArbitrationDecision.PERMIT if target_permits else ArbitrationDecision.DENY
            return GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=decision,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason=f"precedence:{contract.authority_precedence}",
                scope_evaluated=target_units[0].unit_id if target_units else None,
                contract_evaluated=contract.contract_id,
            )

        # Pattern 3: Conjunction — both must permit (default for sovereign domains)
        if requester_permits and target_permits:
            return GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=ArbitrationDecision.PERMIT,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason=f"conjunction:both_permit",
                scope_evaluated=requester_unit.unit_id,
                contract_evaluated=contract.contract_id,
            )

        # Conjunction failed — resolve based on contract
        if contract.conflict_resolution == ConflictResolution.HALT_AND_ESCALATE:
            return GovernanceFinding.create(
                workflow_id=request.workflow_id,
                decision=ArbitrationDecision.ESCALATE,
                requesting_agent=request.requesting_agent_id,
                target_agent=request.target_agent_id,
                reason="conjunction:conflict:halt_and_escalate",
                contract_evaluated=contract.contract_id,
                escalation_target=contract.escalation_path,
            )

        # Default deny
        return GovernanceFinding.create(
            workflow_id=request.workflow_id,
            decision=ArbitrationDecision.DENY,
            requesting_agent=request.requesting_agent_id,
            target_agent=request.target_agent_id,
            reason="conjunction:conflict:default_deny",
            contract_evaluated=contract.contract_id,
        )

    def _is_state_confirmed(
        self,
        request: DispatchRequest,
        authority_unit: Optional[AuthorityUnit] = None,
        contract: Optional[CompositionContract] = None,
    ) -> bool:
        """
        Check if runtime state required by the scope is confirmed.
        Only fires if unconfirmed keys are relevant to the authority unit's
        conditions/limits or the composition contract's scope conditions.
        Unrelated unconfirmed keys do not trigger monotonic reduction.
        """
        unconfirmed_keys = {
            k for k in request.context.keys() if k.startswith("unconfirmed_")
        }
        if not unconfirmed_keys:
            return True

        # Collect the keys that governance actually evaluates
        relevant_keys = set()
        if authority_unit:
            relevant_keys.update(authority_unit.scope.conditions.keys())
            relevant_keys.update(authority_unit.scope.limits.keys())
        if contract and contract.scope:
            relevant_keys.update(contract.scope.conditions.keys())
            relevant_keys.update(contract.scope.limits.keys())

        # Check if any unconfirmed key (sans prefix) is governance-relevant
        unconfirmed_base_keys = {k[len("unconfirmed_"):] for k in unconfirmed_keys}
        return not bool(unconfirmed_base_keys & relevant_keys)

    def _check_case_law(self, request: DispatchRequest) -> Optional[CaseLawEntry]:
        """Check encoded case law for a matching prior resolution."""
        for entry in self.case_law:
            if self._matches_pattern(request, entry.pattern):
                return entry
        return None

    def _matches_pattern(self, request: DispatchRequest, pattern: dict) -> bool:
        """Deterministic pattern matching against case law entry."""
        for key, expected in pattern.items():
            actual = getattr(request, key, request.context.get(key))
            if actual != expected:
                return False
        return True

    def _constitutional_review(
        self,
        request: DispatchRequest,
        permit_finding: GovernanceFinding,
    ) -> Optional[GovernanceFinding]:
        """
        Mandatory constitutional review for all permits. The constitution is
        always evaluated and never bypassed by more specific contracts.

        Deny at any tier is final. Permit must survive constitutional review.
        Returns a DENY finding if any constitutional invariant is violated,
        or None if all invariants pass and the permit is confirmed.

        Rules are evaluated individually. Each rule that is violated produces
        an immediate DENY. This means constitutional invariants are conjunctive:
        all must hold. There is no mechanism for disjunctive constitutional
        rules ("either A or B must be true"). This is by design: the
        constitution is the set of things that must always be true.
        """
        if not self.constitutional_layers:
            return None

        for layer in self.constitutional_layers:
            for rule in layer.rules:
                # Each rule is a dict with 'field', 'operator', 'value'
                # evaluated against the request context
                field_val = request.context.get(rule.get("field"))
                operator = rule.get("operator", "eq")
                expected = rule.get("value")

                violated = False
                if operator == "eq" and field_val != expected:
                    violated = True
                elif operator == "neq" and field_val == expected:
                    violated = True
                elif operator == "exists" and field_val is None:
                    violated = True
                elif operator == "not_exists" and field_val is not None:
                    violated = True
                elif operator == "gt" and (field_val is None or field_val <= expected):
                    violated = True
                elif operator == "lt" and (field_val is None or field_val >= expected):
                    violated = True

                if violated:
                    return GovernanceFinding.create(
                        workflow_id=request.workflow_id,
                        decision=ArbitrationDecision.DENY,
                        requesting_agent=request.requesting_agent_id,
                        target_agent=request.target_agent_id,
                        reason=f"constitutional_review:{layer.layer_id}:invariant_violated:{rule.get('field')}",
                        scope_evaluated=permit_finding.scope_evaluated,
                        contract_evaluated=permit_finding.contract_evaluated,
                    )

        return None
