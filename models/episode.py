from datetime import datetime

from pydantic import BaseModel, HttpUrl


class Episode(BaseModel, frozen=True):
    guid: str
    feed_title: str
    title: str
    audio_url: HttpUrl
    published: datetime
    duration_seconds: int | None = None
