"""Master cloud plane — SaaS commerce, plan management, license issuance, telemetry."""

from src.master_plane.app import create_app

__all__ = ["create_app"]
