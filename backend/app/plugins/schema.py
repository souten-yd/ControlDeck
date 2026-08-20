"""Plugin SDK v1 compatibility exports.

The canonical version-dispatched manifest contract lives in ``app.addons``.
Existing imports remain stable for the v1 registry and API.
"""

from app.addons.schema import (
    PLUGIN_ID_PATTERN,
    NavigationContributionV1,
    PluginManifestV1,
)

NavigationContribution = NavigationContributionV1
PluginManifest = PluginManifestV1

__all__ = ["PLUGIN_ID_PATTERN", "NavigationContribution", "PluginManifest"]
