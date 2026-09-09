from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DraftChoiceInput(BaseModel):
    offering_id: int
    kind: Literal["primary", "alternate"]
    alternate_priority: int | None = Field(default=None, ge=1, le=2)

    @model_validator(mode="after")
    def validate_priority(self):
        if self.kind == "alternate" and self.alternate_priority is None:
            raise ValueError("备选课程必须提供优先级")
        if self.kind == "primary" and self.alternate_priority is not None:
            raise ValueError("主选课程不能提供备选优先级")
        return self


class SaveDraftRequest(BaseModel):
    expected_version: int = Field(ge=1)
    choices: list[DraftChoiceInput] = Field(max_length=6)


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class OperationResult(BaseModel):
    schedule_id: int
    version: int
    message: str


class CloseResult(BaseModel):
    semester_id: int
    cancelled_offering_ids: list[int]
    billing_jobs_created: int
    message: str


class StoredChoice(BaseModel):
    offering_id: int
    kind: Literal["primary", "alternate", "enrolled"]
    priority: int | None = None


class ScheduleView(BaseModel):
    schedule_id: int | None
    semester_id: int
    version: int
    has_submitted: bool
    is_deleted: bool
    draft: list[StoredChoice]
    enrolled: list[StoredChoice]
    formal_alternates: list[StoredChoice]
