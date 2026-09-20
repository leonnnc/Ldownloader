"""Almacén de trabajos en memoria + limpieza por TTL.

Para el MVP alcanza con un diccionario protegido por lock. En producción
esto se reemplaza por Redis + una cola real (BullMQ / Celery / RQ).
"""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import config


@dataclass
class Job:
    id: str
    url: str
    kind: str  # "mp4" | "mp3"
    format_id: Optional[str] = None
    status: str = "queued"  # queued | processing | done | error | expired
    progress: float = 0.0
    title: Optional[str] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None
    filesize: Optional[int] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def download_url(self) -> Optional[str]:
        return f"/api/file/{self.id}" if self.status == "done" else None

    def to_public(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status,
            "progress": round(self.progress, 1),
            "kind": self.kind,
            "title": self.title,
            "filename": self.filename,
            "filesize": self.filesize,
            "download_url": self.download_url,
            "error": self.error,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # -- CRUD ---------------------------------------------------------------
    def create(self, url: str, kind: str, format_id: Optional[str] = None) -> Job:
        job = Job(id=uuid.uuid4().hex, url=url, kind=kind, format_id=format_id)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            for key, value in fields.items():
                setattr(job, key, value)
            return job

    def all_jobs(self) -> List[Job]:
        with self._lock:
            return list(self._jobs.values())

    # -- Espacio en disco ---------------------------------------------------
    def workdir(self, job_id: str) -> Path:
        d = config.DATA_DIR / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def jobs_waiting_for_upload(self) -> int:
        """Cuántos trabajos están en cola o procesándose."""
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in ("queued", "processing"))

    def counts(self) -> dict:
        """Resumen de estados, para el panel de control."""
        with self._lock:
            summary = {"total": len(self._jobs)}
            for job in self._jobs.values():
                summary[job.status] = summary.get(job.status, 0) + 1
            return summary

    def force_purge(self, keep_active: bool = True) -> dict:
        """Borra archivos ya. Acción de restablecimiento manual.

        `keep_active=True` respeta las descargas en curso (recomendado):
        borrar un archivo a medio escribir deja el trabajo en un estado roto.
        """
        freed_bytes = 0
        removed = 0
        skipped = 0

        for job in self.all_jobs():
            busy = job.status in ("queued", "processing")
            if busy and keep_active:
                skipped += 1
                continue

            directory = config.DATA_DIR / job.id
            if directory.is_dir():
                for path in directory.rglob("*"):
                    if path.is_file():
                        try:
                            freed_bytes += path.stat().st_size
                        except OSError:
                            pass
            shutil.rmtree(directory, ignore_errors=True)

            with self._lock:
                self._jobs.pop(job.id, None)
            removed += 1

        return {
            "jobs_removed": removed,
            "skipped_active": skipped,
            "freed_bytes": freed_bytes,
            "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        }

    # -- Limpieza -----------------------------------------------------------
    def purge_expired(self) -> int:
        """Borra archivos y registros vencidos. Devuelve cuántos limpió."""
        ttl = config.FILE_TTL_MINUTES * 60
        now = time.time()
        removed = 0

        for job in self.all_jobs():
            reference = job.finished_at or job.created_at
            if now - reference < ttl:
                continue

            # Borrar el directorio de trabajo del job.
            shutil.rmtree(config.DATA_DIR / job.id, ignore_errors=True)

            with self._lock:
                # Los jobs ya terminados se eliminan; los activos se marcan.
                if job.status in ("done", "error"):
                    self._jobs.pop(job.id, None)
                else:
                    job.status = "expired"
                    job.filepath = None
            removed += 1

        # Barrido de directorios huérfanos (por si el proceso se reinició).
        known = {j.id for j in self.all_jobs()}
        for child in config.DATA_DIR.iterdir():
            if child.is_dir() and child.name not in known:
                age = now - child.stat().st_mtime
                if age > ttl:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1

        return removed


store = JobStore()
