def build_system_prompt(config, persona: dict) -> str:
    """Prompt de sistema que se envia para una persona: global + newcomer + su prompt.

    newcomer_prompt (setting visible/editable, default no vacio): regla para
    personajes que entran a mitad de conversacion, ante las notas de presencia.
    """
    global_prompt = str(config.get("global_system_prompt") or "").strip()
    newcomer_prompt = str(config.get("newcomer_prompt") or "").strip()
    persona_prompt = (persona.get("system_prompt") or "").strip()
    return "\n\n".join(
        part for part in (global_prompt, newcomer_prompt, persona_prompt) if part
    )


_PRESENCE_NOTE = "[Note: the following messages happened while you were not in the room]"
_SUMMARY_ABSENT_NOTE = " (part of it happened while you were not in the room)"


def _absent(responding_persona: str, msg: dict) -> bool:
    # Mensajes legacy sin "present" cuentan como presentes (sin marca).
    present = msg.get("present")
    if present is None:
        return False
    return responding_persona not in present


def build_llm_messages(
    system_prompt: str,
    responding_persona: str,
    history: list[dict],
    max_context_turns: int,
    summary: str | None = None,
) -> list[dict]:
    # Los mensajes compactados (flag "compacted") ya estan representados por
    # el resumen: entran al contexto solo los que siguen sin resumir.
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if summary:
        label = "[Contexto previo resumido]"
        if any(
            m.get("compacted") and _absent(responding_persona, m) for m in history
        ):
            label += _SUMMARY_ABSENT_NOTE
        messages.append({"role": "user", "content": label + "\n\n" + summary})
    recent = [m for m in history if not m.get("compacted")]
    recent = recent[-max_context_turns:] if max_context_turns else []
    for i, msg in enumerate(recent):
        # tramo de ausencia: el mensaje actual es "perdido" y el anterior no
        # (o es el primero de la ventana) -> nota de presencia una vez por tramo
        if _absent(responding_persona, msg) and (
            i == 0 or not _absent(responding_persona, recent[i - 1])
        ):
            messages.append({"role": "user", "content": _PRESENCE_NOTE})
        text = msg.get("text", "")
        if msg.get("role") == "user":
            if msg.get("image"):
                text = f"{text}\n\n[Attached image: {msg['image']}]"
            messages.append({"role": "user", "content": text})
        elif msg.get("role") == "assistant":
            if msg.get("sender") == responding_persona:
                messages.append({"role": "assistant", "content": text})
            else:
                messages.append(
                    {"role": "user", "content": f"[{msg.get('sender')}]: {text}"}
                )
    return messages
