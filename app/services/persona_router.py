import logging
import random
import re

from app.config import ConfigStore
from app.personas import FOR_INSTRUCT_NAME, PersonaStore
from app.rooms import RoomConfigStore
from app.services.llm import LLMClient

logger = logging.getLogger(__name__)


def resolve_room_personas(
    rooms: RoomConfigStore, personas: PersonaStore, chat_room: str, config: ConfigStore
) -> list[str]:
    hidden = (
        {FOR_INSTRUCT_NAME} if not config.get("show_for_instruct") else set()
    )
    all_names = [p["name"] for p in personas.list() if p["name"] not in hidden]
    if chat_room.lower() == "default":
        return all_names
    room = next(
        (
            r
            for r in rooms.list()
            if str(r.get("name", "")).lower() == chat_room.lower()
        ),
        None,
    )
    if room is not None:
        return [n for n in room.get("persona_names", []) if n in all_names]
    return all_names


def detect_mention(user_message: str, eligible: list[str]) -> str | None:
    found = [
        name
        for name in eligible
        if re.search(r"\b" + re.escape(name) + r"\b", user_message, flags=re.IGNORECASE)
    ]
    return found[0] if len(found) == 1 else None


def build_router_prompt(
    user_message: str,
    eligible: list[str],
    personas: PersonaStore,
    config: ConfigStore,
    history: list[dict],
) -> list[dict]:
    active = [p for p in personas.list() if p["name"] in eligible]
    choices = ", ".join(p["name"] for p in active)
    hints = "\n".join(
        f"- {p['name']}: {', '.join(p.get('router_hints') or [])}" for p in active
    )
    max_context = config.get("max_context_turns")
    recent = history[-max_context:] if max_context else []
    lines = []
    for msg in recent:
        if msg.get("role") == "user":
            lines.append(f"User: {msg.get('text', '')}")
        else:
            lines.append(f"{msg.get('sender')}: {msg.get('text', '')}")
    context = "\n".join(lines)
    system = (
        "You are a conversation router. Your ONLY job is to pick the best "
        "persona to respond to the user's latest message.\n\n"
        f"Available personas:\n{hints}\n\n"
        f"Recent conversation:\n{context}\n\n"
        f"User's latest message: {user_message}\n\n"
        "Respond with ONLY the name of the best persona. Choose from: "
        f"{choices}. Do not add any explanation."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "Pick one persona."},
    ]


async def pick_persona(
    who_answers: str,
    user_message: str,
    eligible: list[str],
    personas: PersonaStore,
    llm: LLMClient,
    config: ConfigStore,
    history: list[dict],
) -> str:
    if not eligible:
        raise ValueError("no eligible personas for this room")

    if who_answers == "random":
        return random.choice(eligible)

    if who_answers == "router":
        if config.get("persona_name_mentions"):
            mentioned = detect_mention(user_message, eligible)
            if mentioned is not None:
                return mentioned
        try:
            result = await llm.chat(
                build_router_prompt(user_message, eligible, personas, config, history),
                max_tokens=16,
            )
            chosen = result.strip().strip("\"'")
            if chosen in eligible:
                return chosen
            logger.info("router returned unknown name %r, falling back to random", chosen)
        except Exception as exc:
            logger.warning("router call failed (%s), falling back to random", exc)
        return random.choice(eligible)

    if who_answers in eligible:
        return who_answers

    logger.info("unrecognized who_answers=%r, falling back to random", who_answers)
    return random.choice(eligible)
