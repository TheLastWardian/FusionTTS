import asyncio
import base64
import logging
from dataclasses import dataclass

from app import paths
from app.config import ConfigStore
from app.personas import PersonaStore
from app.services.tts.engine import TTSEngine, TTSError

logger = logging.getLogger(__name__)


@dataclass
class TTSChunk:
    sentence_id: int
    message_id: str
    persona: str
    text: str
    audio: bytes
    sample_rate: int
    words: list | None = None


class TTSDispatcher:
    def __init__(
        self,
        engine: TTSEngine,
        personas: PersonaStore,
        config: ConfigStore,
    ) -> None:
        self._engine = engine
        self._personas = personas
        self._config = config
        # Cajas sin límite (modelo F5-TTS): meter una oración (~200 B) o un
        # chunk nunca bloquea al que envía; la caja absorbe la brecha de
        # velocidad entre el LLM y la síntesis.
        self._work_q: asyncio.Queue = asyncio.Queue()
        self._audio_q: asyncio.Queue = asyncio.Queue()
        self._fail_q: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._stopped = False
        self._shutting_down = False
        self._inflight_task: asyncio.Task | None = None
        self._in_flight = False
        self._sentence_counter = -1
        # Contabilidad de entrega (modelo F5-TTS is_idle): la ronda termina
        # solo cuando cada oración encolada produjo audio en _audio_q o fue
        # contada como perdida. _completed crece DESPUÉS del put en
        # _audio_q, cerrando la vieja carrera de is_idle (el in_flight se
        # limpiaba antes de que el último chunk entrara a la caja).
        self._total = 0
        self._completed = 0
        self._failed = 0
        self._persona_cache: dict[str, tuple[str, str, str | None]] = {}

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker())

    async def _worker(self) -> None:
        logger.info("tts dispatcher worker arrancado")
        while not self._shutting_down:
            item = await self._work_q.get()
            if item is None:
                continue
            # El wait va DESPUÉS del get: si el check fuera solo al inicio del
            # loop, una pausa mientras el worker descansa en get() dejaría que
            # la primera oración de la ronda siguiente se sintetice "a
            # destiempo" y el worker se quedaría bloqueado en el wait con el
            # resto de la ronda colgado en la caja.
            await self._pause_event.wait()
            if self._shutting_down:
                break
            if self._stopped:
                self._count_failed(item[0], "stop", notify=False)
                continue
            await self._process(item)

    async def shutdown(self) -> None:
        self._shutting_down = True
        self._pause_event.set()
        if self._inflight_task is not None and not self._inflight_task.done():
            self._inflight_task.cancel()
        self._work_q.put_nowait(None)
        try:
            if self._worker_task is not None:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
        finally:
            self._worker_task = None
        while True:
            try:
                self._work_q.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("tts dispatcher shutdown")

    async def enqueue(self, sentence: str, message_id: str, persona: str) -> None:
        if self._stopped or self._shutting_down:
            return
        if self._worker_task is None:
            raise TTSError("dispatcher sin worker")
        self._sentence_counter += 1
        self._total += 1
        self._work_q.put_nowait((self._sentence_counter, sentence, message_id, persona))

    async def _process(self, item: tuple[int, str, str, str]) -> None:
        sentence_id, sentence, message_id, persona = item
        audio_b64, transcript, language = self._persona_audio(persona)
        self._in_flight = True
        self._inflight_task = asyncio.create_task(
            self._engine.synthesize(sentence, audio_b64, transcript, language=language)
        )
        try:
            result = await self._inflight_task
        except asyncio.CancelledError:
            self._count_failed(sentence_id, "cancelada", notify=False)
            return
        except TTSError as exc:
            self._count_failed(sentence_id, str(exc), notify=True)
            return
        finally:
            self._in_flight = False
            self._inflight_task = None
        await self._audio_q.put(
            TTSChunk(
                sentence_id,
                message_id,
                persona,
                sentence,
                result.audio,
                result.sample_rate,
                result.words,
            )
        )
        self._completed += 1

    def _count_failed(self, sentence_id: int, reason: str, notify: bool) -> None:
        # Fail-loud (modelo F5-TTS): cada oración perdida se cuenta y se
        # loguea; las fallas de síntesis reales además notifican al cliente.
        self._failed += 1
        logger.warning(
            "tts sentence %s perdida (%s); fallidas %d/%d",
            sentence_id,
            reason,
            self._failed,
            self._total,
        )
        if notify:
            self._fail_q.put_nowait(
                {
                    "type": "tts_state",
                    "state": "error",
                    "failed": self._failed,
                    "total": self._total,
                }
            )

    async def pause(self) -> None:
        self._pause_event.clear()
        if self._inflight_task is not None and not self._inflight_task.done():
            self._inflight_task.cancel()

    async def resume(self) -> None:
        self._pause_event.set()

    async def stop(self) -> None:
        self._stopped = True
        self._pause_event.set()
        if self._inflight_task is not None and not self._inflight_task.done():
            self._inflight_task.cancel()
        while True:
            try:
                item = self._work_q.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None:
                self._count_failed(item[0], "stop", notify=False)
        self._work_q.put_nowait(None)
        while True:
            try:
                self._audio_q.get_nowait()
            except asyncio.QueueEmpty:
                break

    def reset(self) -> None:
        # Nueva ronda (modelo F5-TTS reset): limpia cajas con restos y
        # contadores; el audio viejo de un mensaje cancelado no filtra a la
        # siguiente (los chunks llevan message_id y la caja se vacía).
        self._stopped = False
        while True:
            try:
                self._work_q.get_nowait()
            except asyncio.QueueEmpty:
                break
        while True:
            try:
                self._audio_q.get_nowait()
            except asyncio.QueueEmpty:
                break
        while True:
            try:
                self._fail_q.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._total = 0
        self._completed = 0
        self._failed = 0

    def is_idle(self) -> bool:
        return (
            self._work_q.empty()
            and not self._in_flight
            and self._total == self._completed + self._failed
        )

    async def wait_until_done(self) -> None:
        while not self._shutting_down:
            if self.is_idle():
                return
            await asyncio.sleep(0.05)

    async def wait_audio(self) -> TTSChunk:
        return await self._audio_q.get()

    def audio_empty(self) -> bool:
        return self._audio_q.empty()

    async def wait_failure(self) -> dict:
        return await self._fail_q.get()

    def fail_empty(self) -> bool:
        return self._fail_q.empty()

    def is_stopped(self) -> bool:
        return self._stopped

    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def resolve_persona(self, name: str) -> tuple[str, str, str | None]:
        return self._persona_audio(name)

    def _persona_audio(self, name: str) -> tuple[str, str, str | None]:
        cached = self._persona_cache.get(name)
        if cached is not None:
            return cached
        persona = self._personas.get(name)
        ref_audio = persona.get("reference_audio") if persona is not None else None
        if persona is None or not ref_audio:
            logger.warning("persona %s sin audio de referencia; usando voz auto", name)
            result: tuple[str, str, str | None] = ("", "", None)
        else:
            try:
                wav_path = paths.BASE_DIR / str(ref_audio)
                audio_b64 = base64.b64encode(wav_path.read_bytes()).decode()
                transcript = ""
                ref_transcript = persona.get("reference_audio_transcript")
                if ref_transcript:
                    transcript = (
                        paths.BASE_DIR / str(ref_transcript)
                    ).read_text(encoding="utf-8").strip()
                language = persona.get("reference_audio_language") or None
                result = (audio_b64, transcript, language)
            except (OSError, KeyError) as exc:
                logger.warning(
                    "persona %s: fallo leyendo audio de referencia (%s); voz auto",
                    name,
                    exc,
                )
                result = ("", "", None)
        self._persona_cache[name] = result
        return result

    def invalidate_persona(self, name: str) -> None:
        self._persona_cache.pop(name, None)

    def invalidate_personas(self) -> None:
        self._persona_cache.clear()
