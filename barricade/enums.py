from enum import Enum, StrEnum, IntFlag, auto
import logging
from typing import NamedTuple

class PlayerIDType(str, Enum):
    STEAM_64_ID = "steamID"
    UUID = "hllWindowsID"

class Platform(str, Enum):
    PC = "pc"
    CONSOLE = "console"

class ReportRejectReason(StrEnum):
    INSUFFICIENT = "Insufficient"
    INCONCLUSIVE = "Inconclusive"

class IntegrationType(StrEnum):
    BATTLEMETRICS = "battlemetrics"
    COMMUNITY_RCON = "crcon"
    CUSTOM = "custom"

class ReportReasonDetailsType(NamedTuple):
    pretty_name: str
    emoji: str

class ReportReasonDetails(Enum):
    HACKING = ReportReasonDetailsType(
        pretty_name="Hacking",
        emoji="👾"
    )
    TEAMKILLING_GRIEFING = ReportReasonDetailsType(
        pretty_name="Teamkilling / Griefing",
        emoji="🧨"
    )
    TOXICITY_HARASSMENT = ReportReasonDetailsType(
        pretty_name="Toxicity / Harassment",
        emoji="🤬"
    )
    RACISM_ANTISEMITISM = ReportReasonDetailsType(
        pretty_name="Racism / Anti-semitism",
        emoji="🎭"
    )
    STREAMSNIPING_GHOSTING = ReportReasonDetailsType(
        pretty_name="Stream sniping / Ghosting",
        emoji="📺"
    )
    BAN_EVASION = ReportReasonDetailsType(
        pretty_name="Ban evasion",
        emoji="🕵️‍♂️"
    )
    
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
                    logging.warn("Multiple custom reasons submitted: %s", ", ".join(reasons))
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
                reason = ReportReasonDetails[flag.name] # type: ignore
                if with_emoji:
                    reasons.append(f"{reason.value.emoji} {reason.value.pretty_name}")
                else:
                    reasons.append(reason.value.pretty_name)
        return reasons

class Emojis(StrEnum):
    STEAM = "<:steam:1275098550182740101>"
    XBOX = "<:xbox:1275098583590240256>"
    TICK_YES = "<:tick_yes:1275098575356952689>"
    TICK_MAYBE = "<:tick_maybe:1275098558567022633>"
    TICK_NO = "<:tick_no:1275098566515363911>"
    OWNER = "<:owner:1275098484264927328>"
    CONTACT = "<:contact:1275098526556356638>"
    CRCON = "<:crcon:1275098538581430346>"
    BATTLEMETRICS = "<:battlemetrics:1275098517345669140>"
    HIGHLIGHT_RED = "<:highlight_red:1280312505176031293>"
    HIGHLIGHT_GREEN = "<:highlight_green:1280312497072504886>"
    HIGHLIGHT_BLURPLE = "<:highlight_blurple:1280312487677268030>"
    BANNED = "<:banned:1283018335566561343>"
    UNBANNED = "<:unbanned:1283018344051511316>"
    SILHOUETTE = "<:silhouette:1283389193724366960>"
    REFRESH = "<:refresh:1283775099165737111>"
