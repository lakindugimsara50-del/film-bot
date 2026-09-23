"""
cloud_drive — High-Speed Cloud Storage integration (OneDrive & Google Drive)
for FilmSub.lk. Provides automated chunked uploads, permanent CDN streaming links,
multi-drive pooling, health checks, and inactive/offline movie tracking.
"""

from .drive_manager import drive_manager

__all__ = ["drive_manager"]
