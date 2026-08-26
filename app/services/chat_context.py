def build_llm_messages(
    system_prompt: str,
    responding_persona: str,
    history: list[dict],
    max_context_turns: int,
) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    recent = history[-max_context_turns:] if max_context_turns else []
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
