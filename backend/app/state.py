"""Everything the request handlers share, created once at startup."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .db import Database
from .providers.hub import ProviderHub
from .repo import Repo
from .security import RateLimiter
from .services.generation import Generator
from .services.images import ImageService
from .services.jobs import JobManager
from .services.media import MediaStore
from .services.translate import Translator


@dataclass
class AppState:
    settings: Settings
    db: Database
    repo: Repo
    hub: ProviderHub
    media: MediaStore
    jobs: JobManager
    generator: Generator
    images: ImageService
    translator: Translator
    limiter: RateLimiter


def build_state(settings: Settings) -> AppState:
    db = Database(settings.database_path)
    db.migrate()
    repo = Repo(db)
    repo.fail_stale_jobs()  # anything "running" belonged to a previous process
    hub = ProviderHub(settings, repo)
    media = MediaStore(settings.media_dir)
    return AppState(settings=settings, db=db, repo=repo, hub=hub, media=media, jobs=JobManager(repo),
                    generator=Generator(settings, repo, hub, media), images=ImageService(settings, repo, hub, media),
                    translator=Translator(settings, repo, hub),
                    limiter=RateLimiter(settings.rate_limit_per_minute))
