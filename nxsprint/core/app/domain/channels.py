"""Which transport carries a message. The bot is used only where it can actually reach the person."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ProjectCfg
from app.models import TeamsConversation

WEBHOOK_CHANNELS = frozenset({"teams_dm", "owner_alert", "teams_team"})  # n8n posts these
BOT_CHANNEL = "teams_bot"  # core posts these through the Bot Framework connector
BOT_FOOTER = "Reply ack when you have seen this."


def dm_channel(session: Session, member_id: int, use_bot: bool) -> str:
    """Bot DM if the bot is on and the member has talked to it, otherwise the webhook DM."""
    if use_bot and session.scalar(
        select(TeamsConversation.id).where(TeamsConversation.member_id == member_id)
    ):
        return BOT_CHANNEL
    return "teams_dm"


def webhook_env_for(cfg: ProjectCfg, channel: str) -> str:
    return cfg.channels.team_webhook_env if channel == "teams_team" else cfg.channels.dm_webhook_env
