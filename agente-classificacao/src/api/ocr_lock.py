"""
Lock in-memory para gestão de revisores simultâneos na validação OCR.

Impede que dois revisores recebam a mesma redação e garante que
redações puladas não retornem para o mesmo revisor.

Adequado para 2-5 revisores simultâneos (sem Redis/banco).
"""

import threading
from datetime import datetime, timedelta
from typing import Dict, Set

CLAIM_TTL = timedelta(minutes=5)  # claim individual expira em 5 min
SESSION_TTL = timedelta(minutes=30)  # sessão inteira expira em 30 min sem atividade


class OcrSessionLock:
    def __init__(self):
        self.lock = threading.Lock()
        # {redacao_id: (revisor_id, claimed_at)}
        self.claimed: Dict[int, tuple[int, datetime]] = {}
        # {revisor_id: set(redacao_ids)} — redações puladas por cada revisor
        self.skipped: Dict[int, Set[int]] = {}
        # redações já validadas (salvas no banco)
        self.completed: Set[int] = set()
        self.last_activity: datetime = datetime.now()

    def _purge_expired_claims(self):
        """Remove claims expirados (revisor saiu sem salvar/pular)."""
        now = datetime.now()
        expired = [rid for rid, (_, ts) in self.claimed.items() if now - ts > CLAIM_TTL]
        for rid in expired:
            del self.claimed[rid]

    def claim(self, revisor_id: int, redacao_id: int) -> bool:
        with self.lock:
            self._purge_expired_claims()
            self.last_activity = datetime.now()

            # Já claimed por outro revisor?
            if redacao_id in self.claimed:
                owner, _ = self.claimed[redacao_id]
                if owner != revisor_id:
                    return False

            self.claimed[redacao_id] = (revisor_id, datetime.now())
            return True

    def release(self, revisor_id: int, redacao_id: int):
        with self.lock:
            if redacao_id in self.claimed:
                owner, _ = self.claimed[redacao_id]
                if owner == revisor_id:
                    del self.claimed[redacao_id]

    def skip(self, revisor_id: int, redacao_id: int):
        with self.lock:
            self.last_activity = datetime.now()
            self.skipped.setdefault(revisor_id, set()).add(redacao_id)
            # Libera o claim
            if redacao_id in self.claimed:
                owner, _ = self.claimed[redacao_id]
                if owner == revisor_id:
                    del self.claimed[redacao_id]

    def complete(self, redacao_id: int):
        with self.lock:
            self.last_activity = datetime.now()
            self.completed.add(redacao_id)
            self.claimed.pop(redacao_id, None)

    def get_excluded_ids(self, revisor_id: int) -> Set[int]:
        with self.lock:
            self._purge_expired_claims()
            excluded: Set[int] = set()
            # Redações claimed por OUTROS revisores
            for rid, (owner, _) in self.claimed.items():
                if owner != revisor_id:
                    excluded.add(rid)
            # Redações puladas por ESTE revisor
            excluded |= self.skipped.get(revisor_id, set())
            # Redações já validadas por qualquer revisor
            excluded |= self.completed
            return excluded

    @property
    def is_expired(self) -> bool:
        return datetime.now() - self.last_activity > SESSION_TTL


# ── Registry global de sessões ──

_sessions: Dict[str, OcrSessionLock] = {}
_registry_lock = threading.Lock()


def _make_key(
    teste_prova_id: int,
    redacao_status_id: int | None,
    ocr_min: float | None,
    ocr_max: float | None,
) -> str:
    return f"{teste_prova_id}:{redacao_status_id}:{ocr_min}:{ocr_max}"


def _purge_expired_sessions():
    expired = [k for k, v in _sessions.items() if v.is_expired]
    for k in expired:
        del _sessions[k]


def get_session(
    teste_prova_id: int,
    redacao_status_id: int | None,
    ocr_min: float | None,
    ocr_max: float | None,
) -> OcrSessionLock:
    key = _make_key(teste_prova_id, redacao_status_id, ocr_min, ocr_max)
    with _registry_lock:
        _purge_expired_sessions()
        if key not in _sessions:
            _sessions[key] = OcrSessionLock()
        return _sessions[key]
