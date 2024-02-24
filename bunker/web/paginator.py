from fastapi import Request, Query, Depends
from pydantic import BaseModel, AnyHttpUrl
from typing import Annotated, Generic, TypeVar, Optional

class PaginatedResponseLinks(BaseModel):
    prev: Optional[AnyHttpUrl] = None
    next: Optional[AnyHttpUrl] = None

M = TypeVar('M')
class PaginatedResponse(BaseModel, Generic[M]):
    limit: int
    items: list[M]
    links: PaginatedResponseLinks

class PaginatorParams:
    def __init__(self,
            req: Request,
            limit: Annotated[int, Query(gt=0, le=1000)] = 100,
            offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.req = req
        self.offset = offset
        self.limit = limit

    def _get_prev_url(self, items: list[M]):
        if self.offset == 0:
            return None

        offset = max(self.offset - self.limit, 0)
        limit = min(self.offset - offset, self.limit)
        return str(self.req.url.include_query_params(
            offset=offset,
            limit=limit
        ))

    def _get_next_url(self, items: list[M]):
        if len(items) < self.limit:
            return None
        else:
            return str(self.req.url.include_query_params(
                offset=self.offset + self.limit,
                limit=self.limit
            ))

    def paginate(self, items: list[M]):
        return PaginatedResponse(
            limit=self.limit,
            items=items,
            links=PaginatedResponseLinks(
                prev=self._get_prev_url(items),
                next=self._get_next_url(items),
            )
        )

PaginatorDep = Annotated[PaginatorParams, Depends(PaginatorParams)]
