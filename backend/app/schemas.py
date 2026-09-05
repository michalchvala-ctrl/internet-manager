from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    is_admin: bool = False


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=4, max_length=128)
    is_admin: bool | None = None
    is_active: bool | None = None


class DeviceBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mac: str = Field(min_length=11, max_length=17)
    address_list: str = Field(min_length=1, max_length=128)
    category: Literal["child", "pc", "tv", "other"] = "other"
    sort_order: int = 0
    notes: str | None = None
    owner_id: int | None = None


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    mac: str | None = Field(default=None, min_length=11, max_length=17)
    address_list: str | None = Field(default=None, min_length=1, max_length=128)
    category: Literal["child", "pc", "tv", "other"] | None = None
    sort_order: int | None = None
    notes: str | None = None
    owner_id: int | None = None


class DeviceOut(BaseModel):
    id: int
    name: str
    mac: str
    address_list: str
    category: str
    sort_order: int
    notes: str | None
    owner_id: int | None
    internet_blocked: bool
    social_blocked: bool
    social_slow: bool = False
    internet_blocked_since: datetime | None
    social_blocked_since: datetime | None
    mikrotik_filter_id: str | None = None
    traffic_upload_bytes: int | None = None
    traffic_download_bytes: int | None = None
    traffic_today_upload_bytes: int | None = None
    traffic_today_download_bytes: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TrafficDayOut(BaseModel):
    day: str
    upload_bytes: int
    download_bytes: int


class TrafficHourOut(BaseModel):
    hour: int
    upload_bytes: int
    download_bytes: int


class DeviceTrafficHistoryOut(BaseModel):
    device_id: int
    days: list[TrafficDayOut]
    hours: list[TrafficHourOut] = []
    timezone: str = "Europe/Bratislava"


class ToggleRequest(BaseModel):
    blocked: bool


class SocialModeRequest(BaseModel):
    mode: Literal["on", "slow", "off"]


class ScheduleRuleCreate(BaseModel):
    days: str = Field(default="0,1,2,3,4,5,6", max_length=32)
    time: str = Field(min_length=4, max_length=5, pattern=r"^\d{2}:\d{2}$")
    action: Literal[
        "internet_on",
        "internet_off",
        "social_on",
        "social_slow",
        "social_off",
    ]
    enabled: bool = True


class ScheduleRuleUpdate(BaseModel):
    days: str | None = Field(default=None, max_length=32)
    time: str | None = Field(default=None, min_length=4, max_length=5, pattern=r"^\d{2}:\d{2}$")
    action: (
        Literal[
            "internet_on",
            "internet_off",
            "social_on",
            "social_slow",
            "social_off",
        ]
        | None
    ) = None
    enabled: bool | None = None


class ScheduleRuleOut(BaseModel):
    id: int
    device_id: int
    enabled: bool
    days: str
    time: str
    action: str
    last_fired: str | None = None

    model_config = {"from_attributes": True}


class SocialDomainOut(BaseModel):
    id: int
    domain: str
    enabled: bool

    model_config = {"from_attributes": True}


class SocialDomainCreate(BaseModel):
    domain: str = Field(min_length=3, max_length=255)


class UserDeviceAccessUpdate(BaseModel):
    device_ids: list[int] = Field(default_factory=list)


class UserDeviceAccessOut(BaseModel):
    user_id: int
    device_ids: list[int]


class StatusOut(BaseModel):
    mikrotik_configured: bool
    mikrotik_ok: bool | None
    mikrotik_error: str | None
    adguard_configured: bool
    adguard_ok: bool | None
    adguard_error: str | None
    social_slow_limit_kbps: int | None = None
    timezone: str | None = None
