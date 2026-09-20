"""Métricas en memoria.

Sin esto estás ciego: no sabes si el servicio funciona al 99% o al 40%.
Cada plataforma se mide por separado, porque es normal que YouTube funcione
perfecto mientras Facebook está roto (y al revés).
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List


@dataclass
class PlatformStats:
    """Salud de una plataforma concreta (youtube, facebook, instagram...)."""

    name: str
    success: int = 0
    failure: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    # Ventana deslizante de los últimos 50 resultados para una tasa realista.
    recent: Deque[bool] = field(default_factory=lambda: deque(maxlen=50))

    @property
    def total(self) -> int:
        return self.success + self.failure

    @property
    def success_rate(self) -> float:
        if not self.recent:
            return 1.0
        return sum(self.recent) / len(self.recent)

    @property
    def state(self) -> str:
        if self.total < 3:
            return "sin_datos"
        if self.success_rate >= 0.9:
            return "sano"
        if self.success_rate >= 0.6:
            return "degradado"
        return "caido"

    def to_public(self) -> dict:
        return {
            "platform": self.name,
            "state": self.state,
            "success_rate": round(self.success_rate, 3),
            "success": self.success,
            "failure": self.failure,
            "total": self.total,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
        }


class Metrics:
    """Contadores globales + salud por plataforma."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter = Counter()
        self._platforms: Dict[str, PlatformStats] = {}
        self._started_at = time.time()

    # -- contadores simples -------------------------------------------------
    def inc(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[key] += amount

    # -- salud por plataforma ----------------------------------------------
    def _platform(self, name: str) -> PlatformStats:
        # Se llama siempre con el lock tomado.
        if name not in self._platforms:
            self._platforms[name] = PlatformStats(name=name)
        return self._platforms[name]

    def record(self, platform: str, ok: bool, error: str | None = None) -> None:
        key = (platform or "desconocido").lower()
        now = time.time()
        with self._lock:
            stats = self._platform(key)
            stats.recent.append(ok)
            if ok:
                stats.success += 1
                stats.consecutive_failures = 0
                stats.last_success_at = now
            else:
                stats.failure += 1
                stats.consecutive_failures += 1
                stats.last_error = (error or "")[:300]
                stats.last_failure_at = now
            self._counters[f"platform.{key}.{'ok' if ok else 'fail'}"] += 1

    def platform(self, name: str) -> PlatformStats | None:
        with self._lock:
            return self._platforms.get((name or "").lower())

    def platform_states(self) -> Dict[str, str]:
        with self._lock:
            return {name: s.state for name, s in self._platforms.items()}

    # -- lectura ------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            platforms: List[dict] = [
                s.to_public()
                for s in sorted(self._platforms.values(), key=lambda s: s.total, reverse=True)
            ]

        overall_ok = counters.get("parse.ok", 0) + counters.get("download.ok", 0)
        overall_fail = counters.get("parse.fail", 0) + counters.get("download.fail", 0)
        total = overall_ok + overall_fail

        return {
            "uptime_seconds": int(time.time() - self._started_at),
            "requests": {
                "parse_ok": counters.get("parse.ok", 0),
                "parse_fail": counters.get("parse.fail", 0),
                "download_ok": counters.get("download.ok", 0),
                "download_fail": counters.get("download.fail", 0),
            },
            "overall_success_rate": round(overall_ok / total, 3) if total else None,
            "counters": counters,
            "platforms": platforms,
        }


metrics = Metrics()
