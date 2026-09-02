from app.persistence import new_message
from app.services.chat_context import (
    _PRESENCE_NOTE,
    build_llm_messages,
    build_system_prompt,
)


def _m(role, sender, text, present=None, compacted=False):
    m = new_message(role, sender, text)
    if present is not None:
        m["present"] = list(present)
    if compacted:
        m["compacted"] = True
    return m


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
    # el tail es el propio assistant de Jean -> cierra con [Continue]
    assert [m["role"] for m in msgs[1:]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert [m["content"] for m in msgs[1:]] == [
        "user-0",
        "asst-1",
        "user-2",
        "asst-3",
        "[Continue]",
    ]


def test_no_continue_marker_when_tail_is_other_persona():
    history = _history(2)
    history[1] = new_message("assistant", "Fischl", "hola")
    msgs = build_llm_messages("SYS", "Jean", history, 500)
    # la linea final es de Fischl (user [Fischl]: hola): no se agrega nada
    assert msgs[-1] == {"role": "user", "content": "[Fischl]: hola"}


def test_no_continue_marker_when_tail_is_user():
    msgs = build_llm_messages("SYS", "Jean", [new_message("user", "user", "hola")], 500)
    assert msgs[-1] == {"role": "user", "content": "hola"}


def test_max_context_turns_zero_means_no_history():
    msgs = build_llm_messages("SYS", "Jean", _history(6), 0)
    assert [m["role"] for m in msgs] == ["system"]


def test_max_context_turns_caps_tail():
    msgs = build_llm_messages("SYS", "Jean", _history(6), 2)
    assert [m["content"] for m in msgs[1:]] == ["user-4", "asst-5", "[Continue]"]


def test_summary_prepended_after_system():
    msgs = build_llm_messages("SYS", "Jean", _history(4), 500, summary="RESUMEN")
    assert msgs[1] == {
        "role": "user",
        "content": "[Contexto previo resumido]\n\nRESUMEN",
    }
    assert [m["content"] for m in msgs[2:]] == [
        "user-0",
        "asst-1",
        "user-2",
        "asst-3",
        "[Continue]",
    ]


def test_compacted_messages_excluded_from_context():
    msgs = build_llm_messages(
        "SYS", "Jean", _history(6, compact_from=4), 500, summary="RESUMEN"
    )
    assert [m["content"] for m in msgs[1:]] == [
        "[Contexto previo resumido]\n\nRESUMEN",
        "user-4",
        "asst-5",
        "[Continue]",
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
        "[Continue]",
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


def test_presence_note_when_persona_joins_midway():
    history = [
        _m("assistant", "A", "a1", present=["A", "B"]),
        _m("assistant", "B", "b1", present=["A", "B"]),
        _m("user", "user", "u1", present=["A", "B", "C"]),
        _m("assistant", "C", "c1", present=["A", "B", "C"]),
    ]
    msgs = build_llm_messages("SYS", "C", history, 500)
    assert [m["content"] for m in msgs[1:]] == [
        _PRESENCE_NOTE,
        "[A]: a1",
        "[B]: b1",
        "u1",
        "c1",
        "[Continue]",
    ]


def test_no_presence_note_when_always_present():
    history = [
        _m("assistant", "A", "a1", present=["A", "B"]),
        _m("user", "user", "u1", present=["A", "B"]),
    ]
    msgs = build_llm_messages("SYS", "A", history, 500)
    assert [m["content"] for m in msgs[1:]] == ["a1", "u1"]


def test_no_presence_note_for_legacy_messages():
    msgs = build_llm_messages("SYS", "Jean", _history(4), 500)
    assert all(m["content"] != _PRESENCE_NOTE for m in msgs)


def test_presence_note_per_absence_run():
    # C presente -> ausente -> presente -> ausente -> presente: 2 tramos, 2 notas
    history = [
        _m("assistant", "C", "c0", present=["A", "C"]),
        _m("user", "user", "u1", present=["A"]),
        _m("user", "user", "u2", present=["A", "C"]),
        _m("assistant", "B", "b1", present=["A"]),
        _m("user", "user", "u3", present=["A", "C"]),
    ]
    msgs = build_llm_messages("SYS", "C", history, 500)
    contents = [m["content"] for m in msgs[1:]]
    assert contents.count(_PRESENCE_NOTE) == 2
    assert contents == [
        "c0",
        _PRESENCE_NOTE,
        "u1",
        "u2",
        _PRESENCE_NOTE,
        "[B]: b1",
        "u3",
    ]


def test_presence_note_at_window_start():
    history = [
        _m("user", "user", "u0", present=["A"]),
        _m("assistant", "C", "c1", present=["A", "C"]),
    ]
    msgs = build_llm_messages("SYS", "C", history, 500)
    assert msgs[1]["content"] == _PRESENCE_NOTE


def test_summary_note_when_compacted_region_partially_missed():
    history = [
        _m("user", "user", "u0", present=["A"], compacted=True),
        _m("assistant", "B", "b0", present=["A", "B"], compacted=True),
        _m("user", "user", "u1", present=["A", "C"]),
    ]
    msgs = build_llm_messages("SYS", "C", history, 500, summary="RESUMEN")
    assert msgs[1]["content"] == (
        "[Contexto previo resumido] (part of it happened while you were not in the room)"
        "\n\nRESUMEN"
    )


def test_summary_plain_label_when_compacted_region_present():
    history = [
        _m("user", "user", "u0", present=["A", "C"], compacted=True),
        _m("user", "user", "u1", present=["A", "C"]),
    ]
    msgs = build_llm_messages("SYS", "C", history, 500, summary="RESUMEN")
    assert msgs[1]["content"] == "[Contexto previo resumido]\n\nRESUMEN"


def test_roomstore_append_stamps_present(tmp_path):
    from app.config import ConfigStore
    from app.persistence import RoomStore

    config = ConfigStore(settings_path=tmp_path / "settings.json")
    store = RoomStore("test-room", config, root=tmp_path)
    store.active_personas = ["A", "B"]
    m = store.append(new_message("user", "user", "hola"))
    assert m["present"] == ["A", "B"]
    store.active_personas = ["A", "B", "C"]
    m2 = store.append(new_message("assistant", "C", "hi"))
    assert m2["present"] == ["A", "B", "C"]
    store.active_personas = None
    m3 = store.append(new_message("user", "user", "sin set"))
    assert "present" not in m3


def test_system_prompt_combines_global_newcomer_persona(tmp_path):
    from app.config import ConfigStore, NEWCOMER_PROMPT_DEFAULT

    config = ConfigStore(settings_path=tmp_path / "settings.json")
    config.set("global_system_prompt", "GLOBAL")
    prompt = build_system_prompt(config, {"system_prompt": "PERSONA"})
    assert prompt == "GLOBAL\n\n" + NEWCOMER_PROMPT_DEFAULT + "\n\nPERSONA"


def test_system_prompt_newcomer_prompt_disabled_when_empty(tmp_path):
    from app.config import ConfigStore

    config = ConfigStore(settings_path=tmp_path / "settings.json")
    config.set("global_system_prompt", "GLOBAL")
    config.set("newcomer_prompt", "")
    prompt = build_system_prompt(config, {"system_prompt": "PERSONA"})
    assert prompt == "GLOBAL\n\nPERSONA"
