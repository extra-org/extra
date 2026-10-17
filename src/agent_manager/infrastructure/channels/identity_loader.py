"""Load a trusted deployment plugin once; no provider or account logic here."""

import importlib
import inspect

from agent_manager.domain.channels.identity import ChannelIdentityResolver


def load_channel_identity_resolver(path: str) -> ChannelIdentityResolver:
    try:
        module_name, name = path.split(":", 1)
        plugin_type = getattr(importlib.import_module(module_name), name)
        if not inspect.isclass(plugin_type) or not issubclass(plugin_type, ChannelIdentityResolver):
            raise TypeError("resolver must implement the channel identity port")
        if not inspect.iscoroutinefunction(plugin_type.resolve) or not inspect.iscoroutinefunction(
            plugin_type.close
        ):
            raise TypeError("resolver lookup and cleanup must be async")
        return plugin_type()
    except Exception:
        raise ValueError("channel identity resolver could not be loaded") from None
