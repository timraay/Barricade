from enum import Enum, StrEnum, IntFlag, auto
import logging
from typing import NamedTuple

class PlayerIDType(str, Enum):
    STEAM_64_ID = "steamID"
    UUID = "hllWindowsID"

class ReportRejectReason(StrEnum):
    INSUFFICIENT = "Insufficient"
    INCONCLUSIVE = "Inconclusive"

class IntegrationType(StrEnum):
    BATTLEMETRICS = "battlemetrics"
    COMMUNITY_RCON = "crcon"

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
        reasons = []
        for flag in self:
            if flag == ReportReasonFlag.CUSTOM:
                if not custom_msg:
                    raise TypeError("custom_msg must be a str if CUSTOM is flagged")
                if with_emoji:
                    reasons.append("🎲 " + custom_msg)
                else:
                    reasons.append(custom_msg)
            else:
                reason = ReportReasonDetails[flag.name]
                if with_emoji:
                    reasons.append(f"{reason.value.emoji} {reason.value.pretty_name}")
                else:
                    reasons.append(reason.value.pretty_name)
        return reasons

class Emojis(StrEnum):
    STEAM = "<:steam:1246502628297539625>"
    XBOX = "<:xbox:1246502635218141275>"
    TICK_YES = "<:tick_yes:1246502633351680111>"
    TICK_MAYBE = "<:tick_maybe:1246503269115756735>"
    TICK_NO = "<:tick_no:1246502631904645291>"
    OWNER = "<:owner:1246838964141297715>"
    CONTACT = "<:contact:1246838962329354251>"
