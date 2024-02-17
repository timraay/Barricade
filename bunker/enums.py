from enum import Enum, IntFlag, auto
import logging

class PlayerIDType(str, Enum):
    STEAM_64_ID = "steamID"
    UUID = "hllWindowsID"

class ReportRejectReason(str, Enum):
    INSUFFICIENT = "Insufficient"
    INCONCLUSIVE = "Inconclusive"

class IntegrationType(str, Enum):
    BATTLEMETRICS = "battlemetrics"
    COMMUNITY_RCON = "crcon"

class ReportReasonNames(str, Enum):
    HACKING = "Hacking"
    TEAMKILLING_GRIEFING = "Teamkilling / Griefing"
    TOXICITY_HARASSMENT = "Toxicity / Harassment"
    RACISM_ANTISEMITISM = "Racism / Anti-semitism"
    STREAMSNIPING_GHOSTING = "Stream sniping / Ghosting"
    BAN_EVASION = "Ban evasion"
    
class ReportReasonFlag(IntFlag):
    HACKING = auto()
    TEAMKILLING_GRIEFING = auto()
    TOXICITY_HARASSMENT = auto()
    RACISM_ANTISEMITISM = auto()
    STREAMSNIPING_GHOSTING = auto()
    BAN_EVASION = auto()
    CUSTOM = auto()

    @classmethod
    def from_list(cls, reasons: list[str]):
        self = cls(0)
        custom_msg = None
        for reason in reasons:
            match reason:
                case "Hacking":
                    self |= ReportReasonFlag.HACKING
                case "Teamkilling / Griefing":
                    self |= ReportReasonFlag.TEAMKILLING_GRIEFING
                case "Toxicity / Harassment":
                    self |= ReportReasonFlag.TOXICITY_HARASSMENT
                case "Racism / Anti-semitism":
                    self |= ReportReasonFlag.RACISM_ANTISEMITISM
                case "Stream sniping / Ghosting":
                    self |= ReportReasonFlag.STREAMSNIPING_GHOSTING
                case "Ban evasion":
                    self |= ReportReasonFlag.BAN_EVASION
                case _:
                    if self & ReportReasonFlag.CUSTOM:
                        logging.warn("Multiple custom reasons submitted: %s", ", ".join(reasons))
                    self |= ReportReasonFlag.CUSTOM
                    custom_msg = reason
        return self, custom_msg
    
    def to_list(self, custom_msg: str | None):
        reasons = []
        for flag in self:
            if flag == ReportReasonFlag.CUSTOM:
                if not custom_msg:
                    raise TypeError("custom_msg must be a str if CUSTOM is flagged")
                reasons.append(custom_msg)
            else:
                reason = ReportReasonNames[flag.name]
                reasons.append(reason.value)
        return reasons
