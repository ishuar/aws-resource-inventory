"""
State rules: one module per scanned service, mirroring ``services/``.

Every rule is a pure function ``(scan_data, index, config) ->
list[Finding]`` over one region's raw scan sections, using the
``(resource_type, resource_id) -> Resource`` index for identity. Rules
are registered in ``waste.registry.RULES``; the state-rules provider in
``waste.providers`` runs them.
"""
