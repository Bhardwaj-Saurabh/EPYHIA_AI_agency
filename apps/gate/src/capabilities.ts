// Which agent may REQUEST which action type (DESIGN.md sections 3-4).
// Capability authorization never replaces admin/customer approval - those are
// checked separately in the pipeline.
export const CAPABILITIES: Readonly<Record<string, readonly string[]>> = {
  strategist: ["model_call"],
  web_builder: ["model_call", "deploy"],
  marketer: ["model_call", "video_render", "publish"],
  ops: ["model_call", "checkout_session", "business_storage"],
  // Deterministic control-plane code (run-shell creation, Flow 1 step 2).
  system: ["run_shell"],
};

// Actions a human must approve before they execute, bound to the exact
// payload hash. Everything else is audited-but-automatic.
export const REQUIRES_ADMIN_APPROVAL: ReadonlySet<string> = new Set([
  "deploy",
  "video_render",
  "publish",
]);
