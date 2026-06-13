"""Example access resolver.

Called at runtime by the engine to decide whether the current caller may
reach a protected node.  ``ctx`` contains the resolved context for the
current request (e.g. user role, subscription tier).

Return ``True`` to allow, ``False`` to deny.
"""


class AccessResolver:
    def can_access(self, ctx: dict, node_id: str) -> bool:
        # Demo: allow everyone.  Replace with real RBAC / policy logic.
        return True
