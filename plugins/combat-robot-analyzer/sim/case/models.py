"""Shared types: the vocabulary every gate, handler and rule module speaks.

Enums are ``StrEnum`` so members drop straight into JSON bodies, DynamoDB items
and HTML form values without ``.value`` noise, and so a raw form string round
trips through ``WeightClass("12lb")``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

__all__ = [
    "Aim",
    "Bonus",
    "CaseChoice",
    "EnergyLevel",
    "GateResult",
    "ImpactorSpec",
    "Job",
    "MaterialSource",
    "OpponentArchetype",
    "PartAssignment",
    "PartRole",
    "WeightClass",
]


class WeightClass(StrEnum):
    """NHRL Open Rules 2026 classes, plus 1 lb by internal convention.

    1 lb is not an NHRL class. It is supported by request and reuses the NHRL
    bonus *ratios*, which are uniform across the three real classes. The report
    states this.
    """

    ONE_LB = "1lb"
    THREE_LB = "3lb"
    TWELVE_LB = "12lb"
    THIRTY_LB = "30lb"


class Bonus(StrEnum):
    """Weight bonus claimed at submit. Bonuses cannot be combined."""

    NONE = "none"
    NTL = "ntl"  # non-traditional locomotion
    TRUE_WALKER = "true_walker"
    MULTIBOT = "multibot"


class MaterialSource(StrEnum):
    """Where a part's material came from. PLAN.md decision 42.

    "Defaults are fine -- but the chosen/guessed/defaulted distinction must
    survive into the report." A material feeds the solve directly, so one
    nobody looked at produces a confident report about a bot made of something
    it is not.

    The value is only half the answer and is never read alone: the row carries
    ``material`` beside it, which matters because the two producers default to
    different things. The submit form defaults to ``tpu_shore95a``
    (``web/build.py``) and the library to ``steel_1018``
    (``materials.DEFAULT_MATERIAL``), so ``DEFAULT`` on its own does not say
    what physics was run.

    ``GUESSED`` has no producer in the tree. It came from
    ``tools/partviewer``, which inferred a material from a part name through
    :func:`cra.materials.guess_material` ("Hardox Bit" -> hardox); that tool
    was deleted on 2026-09-23. The member stays because the value is *in the
    data*: ``corpus-roles.json`` carries 37 guessed rows and
    :func:`cra.calibrate.roles.load_roles` refuses a source it does not know,
    so removing it would make the committed table unreadable. The submit form
    never had the inference and emits only ``CHOSEN`` and ``DEFAULT``. One
    vocabulary rather than two, because the report says the same sentence
    whichever produced the row.
    """

    #: A human picked it.
    CHOSEN = "chosen"
    #: Nobody touched it; it is whatever the producer's default is.
    DEFAULT = "default"
    #: Inferred from the part's name. Better than a default, not a decision.
    GUESSED = "guessed"


class CaseChoice(StrEnum):
    """Which impacts a builder asked for. PLAN.md decision 63.

    Both is the product; one is a deliberate narrowing, not a discount. The
    price does not move -- the report is what is sold, and a skipped case has
    always cost the same, which is what the "at the same price" warning on a
    geometry-forced skip already tells builders.

    Choosing one does buy something real, though: ``Settings.per_case_budget``
    splits the element-cycle cap across the cases that run, so a single case
    gets the whole 2.028e10 rather than half of it. A bot that is refused on
    cost with both cases may fit with one.
    """

    BOTH = "both"
    LC1_ONLY = "lc1"
    LC2_ONLY = "lc2"

    @property
    def keys(self) -> tuple[str, ...]:
        """The case keys this choice runs, in report order."""
        return ("lc1", "lc2") if self is CaseChoice.BOTH else (self.value,)

    def runs(self, case: str) -> bool:
        return case in self.keys


class PartRole(StrEnum):
    """What a part is for, assigned by the builder in the B3.5 table.

    Nothing in the pipeline can infer this reliably from geometry, which is the
    whole reason B3.5 exists.
    """

    WEAPON = "weapon"
    ARMOUR = "armour"
    CHASSIS = "chassis"
    OTHER = "other"


class EnergyLevel(StrEnum):
    """Opponent spin preset: picks the weapon's RPM (impactor.RPM_PRESETS)."""

    TYPICAL = "typical"
    HIGH = "high"


class OpponentArchetype(StrEnum):
    """Opponent weapon type: sets the default weapon shape (bar or disc) and, in
    v2, the weapon's spin axis (up for a horizontal spinner, horizontal across the attack for a
    drum; see case/weapon.py)."""

    HORIZONTAL = "horizontal"
    VERTICAL_DRUM = "vertical_drum"


@dataclass(frozen=True)
class GateResult:
    """The uniform return shape of every gate, B0 through B7.

    Because every gate has this signature, the runner is a loop rather than a
    special case per gate, and the test suite exercises all nine without AWS.
    """

    gate: str
    passed: bool
    hard_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, gate: str, *, warnings: list[str] | None = None, **data: Any) -> GateResult:
        return cls(gate=gate, passed=True, warnings=list(warnings or []), data=data)

    @classmethod
    def fail(
        cls, gate: str, *errors: str, warnings: list[str] | None = None, **data: Any
    ) -> GateResult:
        return cls(
            gate=gate,
            passed=False,
            hard_errors=list(errors),
            warnings=list(warnings or []),
            data=data,
        )


@dataclass(frozen=True)
class PartAssignment:
    """One row of the submit form: what a part is, and what it is made of.

    Deliberately not a field on :class:`Job`. Job is the DynamoDB metadata
    row, and PLAN.md §9 keeps per-part data out of it; the table is written to
    the job's working directory for B5 instead.
    """

    part_id: str  # the per-occurrence id from B3, e.g. "p003"
    role: PartRole
    material: str  # a key in materials.yaml
    #: Where ``material`` came from. Defaults to ``DEFAULT`` on purpose: a
    #: producer that says nothing has not reviewed anything, and the honest
    #: reading of silence is "nobody looked". The failure mode of the other
    #: default is a report that claims a material was confirmed when it was
    #: not, which is exactly what decision 42 exists to prevent.
    material_source: MaterialSource = MaterialSource.DEFAULT


@dataclass(frozen=True)
class ImpactorSpec:
    """The opponent's spinning weapon and the tooth it strikes with.

    Resolved by ``impactor.impactor_spec`` from the class presets and any user
    overrides (named in ``overrides``). Keyed to the *nominal* weight class — a
    walker's opponent is still a normal bot of that class.
    """

    weight_class: WeightClass
    energy_level: EnergyLevel
    archetype: OpponentArchetype
    ke_j: float
    v_tip_ms: float
    m_eff_kg: float
    r_arc_mm: float
    material: str
    assumptions: tuple[str, ...] = ()
    rpm: float = 0.0
    weapon_shape: str = "bar"
    weapon_mass_kg: float = 0.0
    plate_thickness_mm: float = 0.0
    weapon_od_mm: float = 0.0
    bar_width_mm: float | None = None
    #: the tooth head: across the strike (along the spin axis), radial depth
    #: out of the rim, and along the direction of travel
    tooth_width_mm: float = 0.0
    tooth_depth_mm: float = 0.0
    tooth_length_mm: float = 0.0
    #: what the weapon's hub carries: the whole opponent robot
    opponent_mass_kg: float = 0.0
    overrides: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Aim:
    """Where the user put the strike, and whether they put it there.

    The four fields ``tools/partviewer``'s "Export approach" wrote; that
    tool is gone as of 2026-09-23 but the shape is the wire format the
    submit form posts, so it is fixed by the pages, not by the tool.
    ``direction`` runs from the target *outwards*, towards where the tooth
    starts, so the tooth travels along ``-direction`` — the same convention
    :class:`~cra.placement.Approach` uses, because this overwrites three of
    its fields rather than replacing it.

    ``human_placed`` is not decoration. Automatic placement aims at the
    support point of the ellipsoid inscribed in a bounding box, and across the
    corpus that lands off the material 46 times in 58, so a strike nobody
    confirmed is a strike that is probably wrong. The report has to be able to
    say which it was.
    """

    point: tuple[float, float, float]
    direction: tuple[float, float, float]
    standoff: float
    human_placed: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> Aim | None:
        """Parse one aim off the submit form, or ``None`` if absent."""
        if not raw:
            return None
        try:
            px, py, pz = (float(v) for v in raw["point"])
            dx, dy, dz = (float(v) for v in raw["direction"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"unusable aim {raw!r}: {exc}") from exc
        norm = math.sqrt(dx * dx + dy * dy + dz * dz)
        if norm < 1e-9:
            raise ValueError(f"unusable aim {raw!r}: the direction has no length")
        return cls(
            point=(px, py, pz),
            direction=(dx / norm, dy / norm, dz / norm),
            standoff=float(raw.get("standoff") or 0.0),
            human_placed=bool(raw.get("human_placed")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "point": list(self.point),
            "direction": list(self.direction),
            "standoff": self.standoff,
            "human_placed": self.human_placed,
        }


@dataclass
class Job:
    """One submission, from upload to captured payment.

    Mirrors the PLAN.md section 9 DynamoDB metadata row — no geometry, no part
    names, no report content — with ``workdir`` as the one non-persisted field.
    The accounting attributes stay ``None`` until the Batch task fills them in.
    """

    job_id: str
    created_at: str

    # Set at submit, from the form.
    email: str = ""
    weight_class: WeightClass | None = None
    bonus: Bonus = Bonus.NONE
    energy_level: EnergyLevel = EnergyLevel.TYPICAL
    archetype: OpponentArchetype = OpponentArchetype.HORIZONTAL

    #: Which impacts the builder asked for. Both by default, because both is
    #: the product and an older form that does not send the field means both.
    cases: CaseChoice = CaseChoice.BOTH

    # Which part each load case strikes. The form pre-fills both with the most
    # exposed candidate carrying the matching role, so they are only ``None``
    # before B3.5 has run — or, for ``lc1_target``, on a multibot segment with
    # no weapon, where LC1 does not run at all.
    lc1_target: str | None = None
    lc2_target: str | None = None

    # Where on that part each case is struck. Both are required for a paid
    # job and both must be confirmed by a human against real surfaces — see
    # :class:`Aim`. ``None`` means the form was never completed, which B3.5
    # refuses rather than falling back to the prefill.
    lc1_aim: Aim | None = None
    lc2_aim: Aim | None = None

    # Payment. v0 holds a mock intent id and never moves money.
    pi_id: str | None = None
    captured_at: str | None = None

    #: When the delivery mail was accepted by SES, ISO-8601 in UTC.
    #:
    #: Written by ``handlers/notify.py`` after the send and checked before it,
    #: so the second delivery of an at-least-once EventBridge event, or a
    #: re-driven Batch job, does not mail the builder twice. ``None`` means
    #: nobody has been told anything about this job.
    notified_at: str | None = None

    #: When DynamoDB may delete this row, as **Unix epoch seconds**.
    #:
    #: An int, not an ISO string, and that is not a style choice: DynamoDB's TTL
    #: only acts on a Number attribute and silently ignores every other type. A
    #: string here would leave the table configured, the attribute present, and
    #: nothing ever deleted -- the failure shape where everything looks right.
    #:
    #: Two tiers, from ``Settings``. ``handlers/upload.py`` sets the short one
    #: at row creation; ``handlers/submit.py`` extends it to the paid one in the
    #: same update that records ``pi_id``, because a row describing a
    #: transaction is the only kind PLAN.md §9's "accounting, disputes" is
    #: about. Nothing in the Batch task reads or writes it.
    expires_at: int | None = None

    # Filled in by B4-B7 in the Batch task.
    element_count: int | None = None
    element_cycles: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    fargate_seconds: float | None = None
    actual_cost: float | None = None
    outcome: str | None = None
    report_sha256: str | None = None

    # Ephemeral: the Fargate scratch directory, never persisted.
    workdir: str | None = None

    # --- crossing into the Batch task ---------------------------------------
    #: Fields the approved-job document deliberately leaves out, and why. Held
    #: here rather than in the test so the reasons live beside the fields.
    #:
    #: Everything else must be in the document. A field that exists on one side
    #: of a process boundary and silently does not cross is this project's most
    #: repeated defect -- ``solid_index`` dropped from B3's projection, then
    #: ``part_id`` and ``solid_index`` dropped from the mesh worker's
    #: ``Approach`` rebuild -- and it is invisible every time, because the
    #: fallback works.
    NOT_HANDED_OVER: ClassVar[frozenset[str]] = frozenset(
        {
            # The Batch task's own scratch directory; meaningless anywhere else.
            "workdir",
            # Written by the Batch task, not read by it.
            "captured_at",
            "element_count",
            "element_cycles",
            "tokens_in",
            "tokens_out",
            "fargate_seconds",
            "actual_cost",
            "outcome",
            "report_sha256",
            # Housekeeping for the metadata row, set at upload and extended at
            # submit. The Batch task neither reads nor extends it: a row's
            # retention is decided by whether it describes a transaction, and
            # that is settled before the job is ever enqueued.
            "expires_at",
            # Written by the notify Lambda after the Batch task has exited, and
            # about the task rather than by it. The task cannot mail at all --
            # no route to SES from a private subnet -- so this never crosses
            # the handover in either direction. See handlers/notify.py.
            "notified_at",
        }
    )

    def approved_document(self) -> dict[str, Any]:
        """What B3.5 approved, in the form the Batch task reconstructs it from.

        **The aims are the point of this.** They live only in the submit
        Lambda's memory otherwise, and B4 reads ``job.lc1_aim`` directly, so
        without them a paid job falls back to the automatic prefill -- which
        decisions 45 and 46 exist to forbid, and which the corpus refuses in 44
        of 58 cases. The user would be charged for a strike they confirmed and
        given one nobody did.

        The same argument B5 makes about B4: a deck built from anything other
        than what was costed is a model the budget gate never saw. A case
        meshed from anything other than what B3.5 graded is a strike nobody
        approved.
        """
        return {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "email": self.email,
            "weight_class": self.weight_class.value if self.weight_class else None,
            "bonus": self.bonus.value,
            "energy_level": self.energy_level.value,
            "archetype": self.archetype.value,
            "cases": self.cases.value,
            "lc1_target": self.lc1_target,
            "lc2_target": self.lc2_target,
            "lc1_aim": self.lc1_aim.to_dict() if self.lc1_aim else None,
            "lc2_aim": self.lc2_aim.to_dict() if self.lc2_aim else None,
            "pi_id": self.pi_id,
        }

    @classmethod
    def from_approved(cls, raw: Mapping[str, Any]) -> Job:
        """Rebuild the job the Batch task is to run.

        Strict about the enums and the aims: a job that cannot be rebuilt
        exactly must fail loudly here, before anything is meshed, rather than
        quietly become a different job with defaults in the gaps.
        """
        weight = raw.get("weight_class")
        return cls(
            job_id=str(raw["job_id"]),
            created_at=str(raw.get("created_at") or ""),
            email=str(raw.get("email") or ""),
            weight_class=WeightClass(weight) if weight else None,
            bonus=Bonus(raw.get("bonus") or Bonus.NONE.value),
            energy_level=EnergyLevel(raw.get("energy_level") or EnergyLevel.TYPICAL.value),
            archetype=OpponentArchetype(raw.get("archetype") or OpponentArchetype.HORIZONTAL.value),
            cases=CaseChoice(raw.get("cases") or CaseChoice.BOTH.value),
            lc1_target=raw.get("lc1_target"),
            lc2_target=raw.get("lc2_target"),
            lc1_aim=Aim.from_dict(raw.get("lc1_aim")),
            lc2_aim=Aim.from_dict(raw.get("lc2_aim")),
            pi_id=raw.get("pi_id"),
        )
