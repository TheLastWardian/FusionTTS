from app.persistence import new_message
from app.services.chat_context import build_llm_messages, build_system_prompt


def _history(n, compact_from=None):
    msgs = []
    for i in range(n):
        if i % 2 == 0:
            m = new_message("user", "user", f"user-{i}")
        else:
            m = new_message("assistant", "Jean", f"asst-{i}")
        if compact_from is not None and i < compact_from:
            m["compacted"] = True
        msgs.append(m)
    return msgs


def test_basic_roles():
    # max_context_turns=0 = sin historial (semantica existente); 500 = todo
    msgs = build_llm_messages("SYS", "Jean", _history(4), 500)
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in msgs[1:]] == ["user-0", "asst-1", "user-2", "asst-3"]


def test_max_context_turns_zero_means_no_history():
    msgs = build_llm_messages("SYS", "Jean", _history(6), 0)
    assert [m["role"] for m in msgs] == ["system"]


def test_max_context_turns_caps_tail():
    msgs = build_llm_messages("SYS", "Jean", _history(6), 2)
    assert [m["content"] for m in msgs[1:]] == ["user-4", "asst-5"]


def test_summary_prepended_after_system():
    msgs = build_llm_messages("SYS", "Jean", _history(4), 500, summary="RESUMEN")
    assert msgs[1] == {
        "role": "user",
        "content": "[Contexto previo resumido]\n\nRESUMEN",
    }
    assert [m["content"] for m in msgs[2:]] == ["user-0", "asst-1", "user-2", "asst-3"]


def test_compacted_messages_excluded_from_context():
    msgs = build_llm_messages(
        "SYS", "Jean", _history(6, compact_from=4), 500, summary="RESUMEN"
    )
    assert [m["content"] for m in msgs[1:]] == [
        "[Contexto previo resumido]\n\nRESUMEN",
        "user-4",
        "asst-5",
    ]


def test_max_context_turns_applies_to_uncompacted_tail():
    # 6 compactados + 6 no-compactados; con limite 4 solo van los ultimos 4
    # del tramo no-compactado (el resumen cuenta aparte, no ocupa turns)
    msgs = build_llm_messages(
        "SYS", "Jean", _history(12, compact_from=6), 4, summary="RESUMEN"
    )
    assert [m["content"] for m in msgs[1:]] == [
        "[Contexto previo resumido]\n\nRESUMEN",
        "user-8",
        "asst-9",
        "user-10",
        "asst-11",
    ]


def test_other_persona_sender_prefixed():
    history = _history(2)
    history[1] = new_message("assistant", "Fischl", "hola")
    msgs = build_llm_messages("SYS", "Jean", history, 500)
    assert msgs[2] == {"role": "user", "content": "[Fischl]: hola"}


def test_user_image_marker():
    m = new_message("user", "user", "mirá", image="images/u1.png")
    msgs = build_llm_messages("SYS", "Jean", [m], 500)
    assert msgs[1]["content"] == "mirá\n\n[Attached image: images/u1.png]"


def test_system_prompt_combines_global_and_persona(tmp_path):
    from app.config import ConfigStore

    config = ConfigStore(settings_path=tmp_path / "settings.json")
    config.set("global_system_prompt", "GLOBAL")
    prompt = build_system_prompt(config, {"system_prompt": "PERSONA"})
    assert prompt == "GLOBAL\n\nPERSONA"
