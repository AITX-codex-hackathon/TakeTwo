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

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        return cls(
            id=d["id"],
            start_frame=d["start_frame"],
            end_frame=d["end_frame"],
            fps=d["fps"],
            quality_score=d["quality_score"],
            anchor_frame_path=d["anchor_frame_path"],
            issues=d.get("issues", []),
        )


@dataclass
class SceneContext:
    description: str
    issues_detail: str
    mood: str
    replacement_prompts: List[str] = field(default_factory=list)
    recommendation: Literal["replace", "cut"] = "replace"
    negative_prompt: str = ""
    motion_directive: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SceneContext":
        return cls(
            description=d["description"],
            issues_detail=d["issues_detail"],
            mood=d["mood"],
            replacement_prompts=d.get("replacement_prompts", []),
            recommendation=d.get("recommendation", "replace"),
            negative_prompt=d.get("negative_prompt", ""),
            motion_directive=d.get("motion_directive", ""),
        )


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

    @classmethod
    def from_dict(cls, d: dict) -> "Insert":
        return cls(
            id=d["id"],
            slot_id=d["slot_id"],
            clip_path=d["clip_path"],
            prompt=d["prompt"],
            label=d["label"],
            critic_pass=d.get("critic_pass", False),
            critic_notes=d.get("critic_notes", ""),
            status=d.get("status", "pending"),
        )


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
    logs: list = field(default_factory=list)
    video_meta: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        job = cls(
            id=d["id"],
            source_path=d["source_path"],
            status=d.get("status", "queued"),
            output_path=d.get("output_path"),
            error=d.get("error"),
            logs=d.get("logs", []),
            video_meta=d.get("video_meta", {}),
        )
        job.slots = [Slot.from_dict(s) for s in d.get("slots", [])]
        job.inserts = [Insert.from_dict(i) for i in d.get("inserts", [])]
        return job


def new_id() -> str:
    return uuid.uuid4().hex[:12]
