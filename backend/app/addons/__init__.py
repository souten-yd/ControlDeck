"""Control Deck Add-on v2 contract package.

Runtime registration and lifecycle management intentionally arrive in PR-A.
PR-0 exposes only the versioned, side-effect-free contract and validation
harness so later slices can test against a real manifest.
"""

from app.addons.contract import ADDON_CONTRACT_VERSION

__all__ = ["ADDON_CONTRACT_VERSION"]
