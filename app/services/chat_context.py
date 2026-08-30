def build_system_prompt(config, persona: dict) -> str:
    """Prompt de sistema que se envia para una persona: global + su prompt."""
    global_prompt = str(config.get("global_system_prompt") or "").strip()
    persona_prompt = (persona.get("system_prompt") or "").strip()
    return "\n\n".join(part for part in (global_prompt, persona_prompt) if part)


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
        messages.append(
            {"role": "user", "content": "[Contexto previo resumido]\n\n" + summary}
        )
    recent = [m for m in history if not m.get("compacted")]
    recent = recent[-max_context_turns:] if max_context_turns else []
    for msg in recent:
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
