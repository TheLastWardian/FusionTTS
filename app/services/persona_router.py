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


# Contexto para el router: solo los turns recientes. Usar el max_context_turns
# del chat (hasta 500) haria la llamada thinking lenta y sesgaria hacia el
# ultimo que hablo; para decidir "quien habla ahora" basta lo reciente.
ROUTER_CONTEXT_TURNS = 10

# Token explicito de "nadie responde"; se acepta en varias grafias comunes.
NOBODY_TOKENS = {"nadie", "none", "no one", "n/a", "-"}


def build_router_prompt(
    user_message: str,
    eligible: list[str],
    personas: PersonaStore,
    config: ConfigStore,
    history: list[dict],
) -> list[dict]:
    active = [p for p in personas.list() if p["name"] in eligible]
    hints = "\n".join(
        f"- {p['name']}: {', '.join(p.get('router_hints') or [])}" for p in active
    )
    max_context = config.get("max_context_turns")
    recent = history[-ROUTER_CONTEXT_TURNS:] if max_context else []
    lines = []
    for msg in recent:
        if msg.get("role") == "user":
            lines.append(f"User: {msg.get('text', '')}")
        else:
            lines.append(f"{msg.get('sender')}: {msg.get('text', '')}")
    context = "\n".join(lines)
    max_replies = config.get("max_persona_replies")
    system = (
        "You are a conversation router. Decide which of the available personas "
        "should respond to the user's latest message, based on who is relevant "
        "to it.\n"
        "Rules:\n"
        "- One persona name per line, in the order they should speak.\n"
        f"- At most {max_replies} names.\n"
        "- If no persona should respond, reply exactly: NADIE\n"
        "- Output only names, nothing else.\n\n"
        f"Available personas:\n{hints}\n\n"
        f"Recent conversation:\n{context}\n\n"
        f"User's latest message: {user_message}\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "Who should respond?"},
    ]


def parse_router_response(raw: str, eligible: list[str], max_count: int) -> list[str] | None:
    # Devuelve [nombres] en orden de habla (dedup, capped), [] si el router
    # dijo NADIE, o None si no se pudo parsear (el caller hace fallback).
    by_lower = {name.lower(): name for name in eligible}
    chosen: list[str] = []
    for line in raw.splitlines():
        token = line.strip().strip("\"'.,! \t")
        if not token:
            continue
        if token.lower() in NOBODY_TOKENS:
            return []
        name = by_lower.get(token.lower())
        if name is None:
            continue  # linea no valida (prosa, nombre ajeno): se ignora
        if name not in chosen:
            chosen.append(name)
        if len(chosen) >= max_count:
            break
    return chosen if chosen else None


async def pick_personas(
    who_answers: str,
    user_message: str,
    eligible: list[str],
    personas: PersonaStore,
    llm: LLMClient,
    config: ConfigStore,
    history: list[dict],
) -> list[str]:
    """Quien responde (y en que orden): lista de 0..max nombres de la room.

    - "random": muestra aleatoria hasta max_persona_replies (comportamiento
      antiguo de caos explicito).
    - "router": mención unica -> solo esa; si no, una llamada LLM (budget
      1024: el modelo thinking puede pensar antes de nombrar) decide quien,
      cuantos y el orden; NADIE -> []; inparseable/fallo -> 1 aleatoria.
    - nombre inexistente: fallback aleatorio (comportamiento antiguo).
    """
    if not eligible:
        raise ValueError("no eligible personas for this room")

    max_count = min(config.get("max_persona_replies"), len(eligible))

    if who_answers == "random":
        return random.sample(eligible, max_count)

    if who_answers != "router":
        logger.info("unrecognized who_answers=%r, falling back to random", who_answers)
        return random.sample(eligible, max_count)

    if config.get("persona_name_mentions"):
        mentioned = detect_mention(user_message, eligible)
        if mentioned is not None:
            return [mentioned]

    try:
        result = await llm.chat(
            build_router_prompt(user_message, eligible, personas, config, history),
            max_tokens=4096,
        )
        chosen = parse_router_response(result, eligible, max_count)
        if chosen is not None:
            logger.info("router picked %s for %r", chosen or "nobody", user_message[:40])
            return chosen
        logger.info("router response unparseable (%r), falling back to random", result[:80])
    except Exception as exc:
        logger.warning("router call failed (%s), falling back to random", exc)
    return [random.choice(eligible)]
