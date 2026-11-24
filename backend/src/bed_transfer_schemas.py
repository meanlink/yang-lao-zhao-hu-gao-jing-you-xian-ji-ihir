from typing import Annotated

from pydantic import Field, model_validator

from .schemas import Input, Note, ShortText


Ref = Annotated[str, Field(pattern=r'^[A-Z0-9][A-Z0-9-]{4,39}$')]


class RedispatchResponse(Input):
    assigned_to: int = Field(gt=0)
    coverage_id: int | None = Field(default=None, gt=0)
    instruction: Note


class TransferMove(Input):
    resident_id: int = Field(gt=0)
    from_unit: ShortText
    from_room: ShortText


class NewBedTransfer(Input):
    ref: Ref
    note: Note
    moves: list[TransferMove] = Field(min_length=2, max_length=2)

    @model_validator(mode='after')
    def two_distinct_residents(self):
        if len({move.resident_id for move in self.moves}) != 2:
            raise ValueError('请选择两位不同的住户')
        if len({(move.from_unit, move.from_room) for move in self.moves}) != 2:
            raise ValueError('两位住户的当前床位不能相同')
        return self
