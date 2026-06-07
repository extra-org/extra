"""Static placeholder for config validation.

The validator checks that this file exists when a node is marked
``protected: true``. It must not import or execute this code during validation.
"""


class AccessResolver:
    def can_access(self, ctx, node_id: str) -> bool:
        raise NotImplementedError("Example placeholder; not executed by validation.")
