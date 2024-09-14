from cachetools import TTLCache
from enum import IntEnum
from sqlalchemy.ext.asyncio import AsyncSession
from typing import ClassVar, NamedTuple
from urllib.parse import urlencode

from barricade import schemas
from barricade.constants import REPORT_FORM_URL
from barricade.crud.reports import create_token
from barricade.enums import Platform, ReportReasonFlag

class FormEntryID(IntEnum):
    token_value = 1804901355

    player1_name = 109881829
    player1_id = 1558117360
    player1_bm_url = 1276541133

    reason = 98529259
    desc = 353415031

    include_player2 = 142203523
    player2_name = 451836681
    player2_id = 1611385956
    player2_bm_url = 2036234904

    include_player3 = 775443842
    player3_name = 707193353
    player3_id = 744131474
    player3_bm_url = 1862618227

    include_player4 = 1640115085
    player4_name = 1052926280
    player4_id = 1781869698
    player4_bm_url = 1757916181

    include_player5 = 1787635110
    player5_name = 1281697596
    player5_id = 1042826548
    player5_bm_url = 1072815851

    is_edit = 1041440882

    def _key(self):
        return f"entry.{self}"

    def encode_str(self, params: dict, value: str):
        params[self._key()] = value
    
    def encode_flag(self, params: dict, flag: ReportReasonFlag, custom: str | None):
        values = []

        if flag & ReportReasonFlag.CUSTOM:
            if not custom:
                raise ValueError("Missing custom value")
            values.extend((flag ^ ReportReasonFlag.CUSTOM).to_list(None))
            values.append("__other_option__")
            params[f"{self._key()}.other_option_response"] = custom
        
        else:
            values.extend(flag.to_list(None))
        
        params[self._key()] = values

    def encode_bool(self, params: dict, value: str = "I want to include another player in the report"):
        params[self._key()] = value

def get_report_edit_url(report: schemas.ReportWithToken):
    params = {}
    FormEntryID.token_value.encode_str(params, report.token.value)

    FormEntryID.desc.encode_str(params, report.body)
    FormEntryID.reason.encode_flag(params, report.reasons_bitflag, report.reasons_custom)

    # Beautiful. I love this. This is fun.

    if len(report.players) >= 1:
        player = report.players[0]
        FormEntryID.player1_name.encode_str(params, player.player_name)
        FormEntryID.player1_id.encode_str(params, player.player_id)
        if player.player.bm_rcon_url:
            FormEntryID.player1_bm_url.encode_str(params, player.player.bm_rcon_url)
    
    if len(report.players) >= 2:
        player = report.players[1]
        FormEntryID.include_player2.encode_bool(params)
        FormEntryID.player2_name.encode_str(params, player.player_name)
        FormEntryID.player2_id.encode_str(params, player.player_id)
        if player.player.bm_rcon_url:
            FormEntryID.player2_bm_url.encode_str(params, player.player.bm_rcon_url)
    
    if len(report.players) >= 3:
        player = report.players[2]
        FormEntryID.include_player3.encode_bool(params)
        FormEntryID.player3_name.encode_str(params, player.player_name)
        FormEntryID.player3_id.encode_str(params, player.player_id)
        if player.player.bm_rcon_url:
            FormEntryID.player3_bm_url.encode_str(params, player.player.bm_rcon_url)
    
    if len(report.players) >= 4:
        player = report.players[3]
        FormEntryID.include_player4.encode_bool(params)
        FormEntryID.player4_name.encode_str(params, player.player_name)
        FormEntryID.player4_id.encode_str(params, player.player_id)
        if player.player.bm_rcon_url:
            FormEntryID.player4_bm_url.encode_str(params, player.player.bm_rcon_url)
    
    if len(report.players) >= 5:
        player = report.players[4]
        FormEntryID.include_player5.encode_bool(params)
        FormEntryID.player5_name.encode_str(params, player.player_name)
        FormEntryID.player5_id.encode_str(params, player.player_id)
        if player.player.bm_rcon_url:
            FormEntryID.player5_bm_url.encode_str(params, player.player.bm_rcon_url)

    FormEntryID.is_edit.encode_bool(params, value="1")
    
    return REPORT_FORM_URL + urlencode(params, doseq=True)

class URLFactory:
    class Key(NamedTuple):
        admin_id: int
        community_id: int
        platform: Platform

        @classmethod
        def from_token(cls, token: schemas._ReportTokenBase):
            return cls(token.admin_id, token.community_id, token.platform)

    _cache: ClassVar[TTLCache[Key, str]] = TTLCache(maxsize=999, ttl=60*60)

    @staticmethod
    async def get(db: AsyncSession, params: schemas.ReportTokenCreateParams, by: str | None = None):
        key = URLFactory.Key.from_token(params)
        if url := URLFactory._cache.get(key):
            return url
        
        db_token = await create_token(db, params, by=by)

        url_params = {}
        FormEntryID.token_value.encode_str(url_params, db_token.value)
        url = REPORT_FORM_URL + urlencode(url_params)
        URLFactory._cache[key] = url

        return url

    @staticmethod
    def remove(token: schemas._ReportTokenBase) -> bool:
        key = URLFactory.Key.from_token(token)
        return URLFactory._cache.pop(key, None) is not None
