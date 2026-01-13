import os
import io

import discord
from discord.ext import commands

from db import BANLIST_TIERS
from team_generator import generate_random_team_for_pokemon, generate_random_team_for_tier, load_available_tiers


def _normalize_tier(tier: str) -> str:
    return tier.strip().replace(" ", "").upper()


def _resolve_tier(user_tier: str, available_tiers: list[str]) -> str | None:
    if not user_tier:
        return None

    normalized = _normalize_tier(user_tier)

    normalized_map: dict[str, str] = {}
    for t in available_tiers:
        normalized_map[_normalize_tier(t)] = t

    return normalized_map.get(normalized)


def _format_team_message(tier: str, team_text: str, pokepaste_url: str) -> str:
    return (
        f"**Random {tier} team**\n"
        f"Pokepaste: {pokepaste_url}\n\n"
        f"```\n{team_text}\n```"
    )


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    if bot.user is None:
        return
    print(f"Logged in as {bot.user} (id={bot.user.id})")


@bot.command(name="gen")
async def gen(ctx: commands.Context, *, arg: str | None = None) -> None:
    tiers = load_available_tiers()

    user_arg = (arg or "").strip()

    if _normalize_tier(user_arg) in BANLIST_TIERS:
        await ctx.send(f"Banlist tiers are not selectable formats: `{user_arg}`")
        return

    resolved_tier = _resolve_tier(user_arg, tiers)

    await ctx.typing()
    try:
        if resolved_tier is not None:
            team_text, paste_url = generate_random_team_for_tier(
                resolved_tier, include_lower_tiers=False
            )
            title_tier = resolved_tier
            content = _format_team_message(title_tier, team_text, paste_url)
        else:
            if not user_arg:
                tier_list = ", ".join(tiers)
                await ctx.send(
                    "Usage: `!gen <tier>` or `!gen <pokemon_name>`\n"
                    f"Available tiers: {tier_list}"
                )
                return

            resolved_name, native_tier, team_text, paste_url = generate_random_team_for_pokemon(
                user_arg, include_lower_tiers=False
            )
            content = (
                f"**Random {native_tier} team featuring {resolved_name}**\n"
                f"Pokepaste: {paste_url}\n\n"
                f"```\n{team_text}\n```"
            )
    except Exception as exc:
        await ctx.send(f"Failed to generate a team: `{exc}`")
        return

    if len(content) <= 1900:
        await ctx.send(content)
        return

    file = discord.File(
        fp=io.BytesIO(team_text.encode("utf-8")),
        filename="random_team.txt",
    )
    await ctx.send(
        content=(
            f"Pokepaste: {paste_url}\n"
            "(Team attached as a file due to Discord message length limits.)"
        ),
        file=file,
    )


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")

    bot.run(token)


if __name__ == "__main__":
    main()
