from dataclasses import dataclass, field, asdict
from typing import Optional, List, Literal
import uuid


@dataclass
class Slot:
    id: str
    start_frame: int
    end_frame: int
    fps: float
    quality_score: float
    anchor_frame_path: str
    issues: List[str] = field(default_factory=list)

    @property
    def duration_sec(self) -> float:
        return (self.end_frame - self.start_frame + 1) / self.fps


@dataclass
class SceneContext:
    description: str
    issues_detail: str
    mood: str
    replacement_prompts: List[str] = field(default_factory=list)
    recommendation: Literal["replace", "cut"] = "replace"


@dataclass
class Insert:
    id: str
    slot_id: str
    clip_path: str
    prompt: str
    label: str
    critic_pass: bool = False
    critic_notes: str = ""
    status: Literal["pending", "approved", "rejected", "cut", "applied"] = "pending"


@dataclass
class Job:
    id: str
    source_path: str
    status: Literal[
        "queued", "detecting", "analyzing", "generating", "review", "applying", "done", "error"
    ] = "queued"
    slots: List[Slot] = field(default_factory=list)
    inserts: List[Insert] = field(default_factory=list)
    output_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self):
        return asdict(self)


def new_id() -> str:
    return uuid.uuid4().hex[:12]
