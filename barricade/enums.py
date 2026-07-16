import logging
from enum import Enum, IntFlag, StrEnum, auto
from typing import NamedTuple, assert_never


class PlayerIDType(StrEnum):
    STEAM_64_ID = "steamID"
    UUID = "hllWindowsID"


class Platform(StrEnum):
    # Careful when renaming, used by PSQL
    PC = "PC"
    CONSOLE = "Console"

    def to_flag(self) -> "PlatformFlag":
        match self:
            case Platform.PC:
                return PlatformFlag.PC
            case Platform.CONSOLE:
                return PlatformFlag.CONSOLE
            case _:
                assert_never(self)
                raise ValueError(f"Unknown platform: {self}")


class PlatformFlag(IntFlag):
    PC = auto()
    CONSOLE = auto()
    # Update to_platforms when adding new platforms

    @classmethod
    def all(cls):
        self = cls(0)
        for platform in cls:
            self |= platform
        return self

    def to_platforms(self) -> list[Platform]:
        platforms: list[Platform] = []
        if self & PlatformFlag.PC:
            platforms.append(Platform.PC)
        if self & PlatformFlag.CONSOLE:
            platforms.append(Platform.CONSOLE)
        return platforms


class PlayerPlatform(StrEnum):
    # Names must exist in Emojis enum as well
    STEAM = "Steam"
    EPIC = "Epic Games"
    XBOX = "Xbox"
    PLAYSTATION = "PlayStation"

    def is_pc(self):
        return self in (PlayerPlatform.STEAM, PlayerPlatform.EPIC, PlayerPlatform.XBOX)

    def is_console(self):
        return self in (PlayerPlatform.XBOX, PlayerPlatform.PLAYSTATION)

    def is_valid_for_platform(self, platform: Platform):
        match platform:
            case Platform.PC:
                return self.is_pc()
            case Platform.CONSOLE:
                return self.is_console()
            case _:
                assert_never(platform)

    def is_valid_for_platform_flag(self, platform_bitflag: PlatformFlag):
        return any(
            self.is_valid_for_platform(platform)
            for platform in platform_bitflag.to_platforms()
        )


class Game(StrEnum):
    # Careful when renaming, used by PSQL & Integration API
    HLL = "HLL"
    HLLV = "HLLV"

    def to_flag(self) -> "GameFlag":
        return GameFlag[self.name]


class GameFlag(IntFlag):
    HLL = auto()
    HLLV = auto()

    @classmethod
    def all(cls):
        self = cls(0)
        for game in cls:
            self |= game
        return self


class PlayerAlertType(StrEnum):
    WATCHLISTED = "Watchlisted"
    UNREVIEWED = "Unreviewed"


class ReportRejectReason(StrEnum):
    # Careful when renaming, used by PSQL
    INSUFFICIENT = "Lacks evidence"
    INCONCLUSIVE = "Not severe enough"


class IntegrationType(StrEnum):
    BATTLEMETRICS = "Battlemetrics"
    COMMUNITY_RCON = "CRCON"
    BIFROST = "Bifrost"
    CUSTOM = "Custom"


class ReportReasonDetailsType(NamedTuple):
    pretty_name: str
    emoji: str


class ReportReasonDetails(Enum):
    HACKING = ReportReasonDetailsType(pretty_name="Hacking", emoji="🪄")
    TEAMKILLING_GRIEFING = ReportReasonDetailsType(
        pretty_name="Teamkilling / Griefing", emoji="🧨"
    )
    TOXICITY_HARASSMENT = ReportReasonDetailsType(
        pretty_name="Toxicity / Harassment", emoji="🤬"
    )
    RACISM_ANTISEMITISM = ReportReasonDetailsType(
        pretty_name="Racism / Anti-semitism", emoji="🎭"
    )
    STREAMSNIPING_GHOSTING = ReportReasonDetailsType(
        pretty_name="Stream sniping / Ghosting", emoji="📺"
    )
    BAN_EVASION = ReportReasonDetailsType(pretty_name="Ban evasion", emoji="🕵️‍♂️")

    def to_flag(self) -> "ReportReasonFlag":
        return ReportReasonFlag[self.name]


class ReportReasonFlag(IntFlag):
    HACKING = auto()
    TEAMKILLING_GRIEFING = auto()
    TOXICITY_HARASSMENT = auto()
    RACISM_ANTISEMITISM = auto()
    STREAMSNIPING_GHOSTING = auto()
    BAN_EVASION = auto()

    # Hopefully we won't ever need more than 15 reasons :)
    CUSTOM = 1 << 15

    @classmethod
    def all(cls):
        self = cls(0)
        for reason in cls:
            self |= reason
        return self

    @classmethod
    def from_list(cls, reasons: list[str]):
        self = cls(0)
        custom_msg = None
        for reason_name in reasons:
            reason = None
            for reason_key, details in ReportReasonDetails.__members__.items():
                if reason_name == details.value.pretty_name:
                    reason = cls[reason_key]
                    self |= reason
            if not reason:
                if self & ReportReasonFlag.CUSTOM:
                    logging.warning(
                        "Multiple custom reasons submitted: %s", ", ".join(reasons)
                    )
                self |= ReportReasonFlag.CUSTOM
                custom_msg = reason_name
        return self, custom_msg

    def to_list(self, custom_msg: str | None, with_emoji: bool = False):
        reasons: list[str] = []
        for flag in self:
            if flag == ReportReasonFlag.CUSTOM:
                if not custom_msg:
                    raise TypeError("custom_msg must be a str if CUSTOM is flagged")
                if with_emoji:
                    reasons.append("🎲 " + custom_msg)
                else:
                    reasons.append(custom_msg)
            else:
                reason = ReportReasonDetails[flag.name]  # type: ignore
                if with_emoji:
                    reasons.append(f"{reason.value.emoji} {reason.value.pretty_name}")
                else:
                    reasons.append(reason.value.pretty_name)
        return reasons


class Emojis(StrEnum):
    STEAM = "<:steam:1275098550182740101>"
    XBOX = "<:xbox:1275098583590240256>"
    EPIC = "<:epic:1525803070813110393>"
    PLAYSTATION = "<:playstation:1525802885164830720>"
    EPIC_XBOX = "<:epic_xbox:1357314108415807528>"
    XBOX_PLAYSTATION = "<:xbox_playstation:1525802887987593256>"
    EPIC_XBOX_PLAYSTATION = "<:epic_xbox_playstation:1525802886393495643>"
    TICK_YES = "<:tick_yes:1275098575356952689>"
    TICK_MAYBE = "<:tick_maybe:1275098558567022633>"
    TICK_NO = "<:tick_no:1275098566515363911>"
    OWNER = "<:owner:1275098484264927328>"
    CONTACT = "<:contact:1275098526556356638>"
    CRCON = "<:crcon:1275098538581430346>"
    BATTLEMETRICS = "<:battlemetrics:1275098517345669140>"
    BIFROST = "<:bifrost:1527373867176296529>"
    HIGHLIGHT_RED = "<:highlight_red:1280312505176031293>"
    HIGHLIGHT_GREEN = "<:highlight_green:1280312497072504886>"
    HIGHLIGHT_BLURPLE = "<:highlight_blurple:1280312487677268030>"
    BANNED = "<:banned:1283018335566561343>"
    UNBANNED = "<:unbanned:1283018344051511316>"
    SILHOUETTE = "<:silhouette:1283389193724366960>"
    REFRESH = "<:refresh:1283790096461594655>"
    ARROW_DOWN_RIGHT = "<:arrow_down_right:1357406683801849996>"
    EASY_ANTI_CHEAT = "<:easy_anti_cheat:1470734064892772394>"
    PILL_HLL_1 = "<:pill_hll1:1509524428998971462>"
    PILL_HLL_2 = "<:pill_hll2:1509524430190022766>"
    PILL_HLL_3 = "<:pill_hll3:1509524431553298502>"
    PILL_HLL_4 = "<:pill_hll4:1509524432643817532>"
    PILL_HLL_5 = "<:pill_hll5:1509524433612837055>"
    PILL_HLLV_1 = "<:pill_hllv1:1509524437257551942>"
    PILL_HLLV_2 = "<:pill_hllv2:1509524438360522802>"
    PILL_HLLV_3 = "<:pill_hllv3:1509524443909722183>"
    PILL_HLLV_4 = "<:pill_hllv4:1509524445847486495>"
    PILL_HLLV_5 = "<:pill_hllv5:1509524447227412540>"


class ReportMessageType(Enum):
    # Careful when renaming, used by PSQL
    PUBLIC = auto()
    MANAGE = auto()
    REVIEW = auto()
    T17_SUPPORT = auto()
