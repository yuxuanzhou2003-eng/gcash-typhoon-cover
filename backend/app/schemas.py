"""Pydantic contracts for the agent workflow API.

The frontend owns the authoritative TypeScript contract.  These models use the
same camelCase field names so a snapshot can be frozen and round-tripped
without a translation layer that might change its meaning.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictInt


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=False,
    )


class Merchant(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=400)
    region: str = Field(min_length=1, max_length=200)
    lat: FiniteFloat
    lng: FiniteFloat
    sumInsured: StrictInt = Field(gt=0)
    coverageStart: str = Field(min_length=1, max_length=80)
    coverageEnd: str = Field(min_length=1, max_length=80)


class Portfolio(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=400)
    version: StrictInt = Field(gt=0)
    source: Literal["demo", "import"]
    createdAt: str = Field(min_length=1, max_length=80)
    merchants: list[Merchant] = Field(max_length=1000)


class Storm(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=400)


class TrackPoint(ContractModel):
    lat: FiniteFloat = Field(ge=-90, le=90)
    lng: FiniteFloat = Field(ge=-180, le=180)


class Bulletin(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    issuedAt: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=400)
    wind: FiniteFloat = Field(ge=0)
    center: TrackPoint
    track: list[TrackPoint] = Field(max_length=2000)
    phase: Literal["forecast", "settlement"]
    source: str = Field(min_length=1, max_length=400)


class RuleTier(ContractModel):
    wind: FiniteFloat = Field(ge=0)
    fraction: FiniteFloat = Field(ge=0, le=1)


class Rule(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=400)
    version: str = Field(min_length=1, max_length=128)
    tiers: list[RuleTier] = Field(min_length=1, max_length=100)
    expenseRate: FiniteFloat = Field(ge=0, le=1)
    annualEventRate: FiniteFloat = Field(ge=0)
    currency: Literal["PHP"]
    source: str = Field(min_length=1, max_length=400)


class RiskItem(ContractModel):
    merchantId: str = Field(min_length=1, max_length=128)
    wind: FiniteFloat | None = Field(default=None, ge=0)
    payout: StrictInt = Field(ge=0)
    eligible: bool
    reason: str = Field(max_length=1000)


class ModelRun(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    portfolioId: str = Field(min_length=1, max_length=128)
    portfolioVersion: StrictInt = Field(gt=0)
    stormId: str = Field(min_length=1, max_length=128)
    bulletinId: str = Field(min_length=1, max_length=128)
    ruleId: str = Field(min_length=1, max_length=128)
    asOf: str = Field(min_length=1, max_length=80)
    createdAt: str = Field(min_length=1, max_length=80)
    status: Literal["queued", "running", "completed", "partial", "failed"]
    modelVersion: str = Field(min_length=1, max_length=200)
    simulated: bool
    inputHash: str = Field(min_length=1, max_length=256)
    items: list[RiskItem] = Field(max_length=1000)
    totalPayout: StrictInt = Field(ge=0)
    affectedCount: StrictInt = Field(ge=0)
    error: str | None = Field(default=None, max_length=1000)


class Action(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    portfolioId: str = Field(min_length=1, max_length=128)
    stormId: str = Field(min_length=1, max_length=128)
    runId: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=400)
    detail: str = Field(max_length=4000)
    type: Literal["review", "funding", "settlement"]
    status: Literal["open", "done"]


class PayoutItem(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    merchantId: str = Field(min_length=1, max_length=128)
    amount: StrictInt = Field(ge=0)
    status: Literal["pending", "succeeded", "failed", "unknown"]
    paymentKey: str = Field(min_length=1, max_length=256)
    reference: str | None = Field(default=None, max_length=256)
    attempts: StrictInt = Field(ge=0)


class Batch(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    portfolioId: str = Field(min_length=1, max_length=128)
    stormId: str = Field(min_length=1, max_length=128)
    runId: str = Field(min_length=1, max_length=128)
    version: StrictInt = Field(gt=0)
    status: Literal[
        "draft",
        "pending_approval",
        "approved",
        "executing",
        "completed",
        "partial_failure",
        "reconciliation_required",
        "rejected",
    ]
    createdAt: str = Field(min_length=1, max_length=80)
    items: list[PayoutItem] = Field(max_length=1000)
    approvedBy: str | None = Field(default=None, max_length=200)
    approvedAt: str | None = Field(default=None, max_length=80)


class AgentSnapshot(ContractModel):
    portfolio: Portfolio
    storm: Storm
    bulletin: Bulletin
    rule: Rule
    modelRun: ModelRun
    actions: list[Action] = Field(max_length=1000)
    batches: list[Batch] = Field(max_length=1000)


class AgentFact(ContractModel):
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=400)
    value: str = Field(max_length=4000)
    source: str = Field(min_length=1, max_length=400)


class AgentFinding(ContractModel):
    text: str = Field(min_length=1, max_length=4000)
    evidenceIds: list[str] = Field(max_length=100)


class AgentStep(ContractModel):
    key: Literal["intelligence", "operations", "review"]
    status: Literal["waiting", "running", "completed", "failed"]
    summary: str = Field(max_length=4000)
    findings: list[AgentFinding] = Field(max_length=200)
    startedAt: str | None = Field(default=None, max_length=80)
    completedAt: str | None = Field(default=None, max_length=80)
    error: str | None = Field(default=None, max_length=1000)


class AgentProposal(ContractModel):
    id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=400)
    detail: str = Field(max_length=4000)
    kind: Literal["review", "communication", "funding", "settlement"]
    merchantIds: list[str] = Field(max_length=1000)
    evidenceIds: list[str] = Field(max_length=100)
    status: Literal["proposed", "accepted", "dismissed", "done"]


class AgentReview(ContractModel):
    verdict: Literal["ready", "needs_review"]
    summary: str = Field(max_length=4000)
    issues: list[str] = Field(max_length=200)


class AgentRun(ContractModel):
    id: str = Field(min_length=1, max_length=256)
    version: StrictInt = Field(ge=1)
    portfolioId: str = Field(min_length=1, max_length=128)
    portfolioVersion: StrictInt = Field(gt=0)
    stormId: str = Field(min_length=1, max_length=128)
    modelRunId: str = Field(min_length=1, max_length=128)
    bulletinId: str = Field(min_length=1, max_length=128)
    ruleId: str = Field(min_length=1, max_length=128)
    inputHash: str = Field(min_length=1, max_length=256)
    createdAt: str = Field(min_length=1, max_length=80)
    updatedAt: str = Field(min_length=1, max_length=80)
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    mode: Literal["live", "simulated"]
    model: str = Field(min_length=1, max_length=200)
    steps: list[AgentStep] = Field(max_length=3)
    facts: list[AgentFact] = Field(max_length=5000)
    proposals: list[AgentProposal] = Field(max_length=1000)
    review: AgentReview | None = None
    error: str | None = Field(default=None, max_length=1000)
    # The TypeScript contract does not require this field, but retaining it in
    # the response makes partial-input warnings visible while remaining
    # additive for clients that use the exact contract.
    warnings: list[str] = Field(default_factory=list, max_length=200)


class RunCreateRequest(ContractModel):
    requestId: str = Field(min_length=1, max_length=256)
    snapshot: AgentSnapshot


class RetryRequest(ContractModel):
    requestId: str = Field(min_length=1, max_length=256)
    expectedVersion: StrictInt = Field(ge=1)


class ProposalDecisionRequest(ContractModel):
    requestId: str = Field(min_length=1, max_length=256)
    expectedVersion: StrictInt = Field(ge=1)
    decision: Literal["accept", "dismiss", "complete"]
    inputHash: str = Field(min_length=1, max_length=256)


class AgentHealth(ContractModel):
    configured: bool
    provider: str = Field(max_length=200)
    model: str = Field(max_length=200)
    mode: Literal["live", "simulated"]
    message: str | None = Field(default=None, max_length=1000)


class IntelligenceOutput(ContractModel):
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[AgentFinding] = Field(max_length=200)


class AgentProposalDraft(ContractModel):
    title: str = Field(min_length=1, max_length=400)
    detail: str = Field(max_length=4000)
    kind: Literal["review", "communication", "funding", "settlement"]
    merchantIds: list[str] = Field(max_length=1000)
    evidenceIds: list[str] = Field(max_length=100)


class OperationsOutput(ContractModel):
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[AgentFinding] = Field(max_length=200)
    proposals: list[AgentProposalDraft] = Field(max_length=200)


class ReviewOutput(ContractModel):
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[AgentFinding] = Field(max_length=200)
    verdict: Literal["ready", "needs_review"]
    issues: list[str] = Field(max_length=200)
