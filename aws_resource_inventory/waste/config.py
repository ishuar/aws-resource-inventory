"""The knobs a waste evaluation runs with — one frozen value for the run."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WasteConfig:
    """Evaluation inputs shared by every rule.

    ``now`` is the run's single reference clock, stamped by the CLI —
    rules never read the wall clock themselves, so age thresholds are
    reproducible and testable with fixture times.
    """

    now: datetime
    # Days an instance must have been stopped before ec2-long-stopped fires.
    stopped_days: int = 90
    # Minimum image age in days before ami-unused fires.
    unused_image_days: int = 90
    # Tag-drift inputs. The tag is the user's own convention (--managed-tag,
    # never a default); the tagged set is fetched by the orchestration via
    # the existing Tagging API path — providers make no AWS calls.
    managed_tag_key: str | None = None
    managed_tag_value: str | None = None
    trust_tags: bool = False
    tagged_arns: frozenset[str] = frozenset()
