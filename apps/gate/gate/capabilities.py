"""Which agent may REQUEST which action type (DESIGN.md sections 3-4).

Capability authorization never replaces admin/customer approval - those are
checked separately in the pipeline.
"""

CAPABILITIES: dict[str, tuple[str, ...]] = {
    "strategist": ("model_call",),
    "web_builder": ("model_call", "deploy", "site_storage"),
    "marketer": ("model_call", "video_render", "publish", "artifact_storage"),
    "ops": ("model_call", "checkout_session", "business_storage"),
    # Deterministic control-plane code: run-shell creation (Flow 1 step 2) and
    # task bookkeeping by the Orchestration Runtime.
    "system": ("run_shell", "task_storage"),
}

# Actions a human must approve before they execute, bound to the exact payload
# hash. Everything else is audited-but-automatic.
REQUIRES_ADMIN_APPROVAL: frozenset[str] = frozenset({"deploy", "video_render", "publish"})
