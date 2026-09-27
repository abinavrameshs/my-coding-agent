"""All event type string constants used by the agent bus."""

# Session lifecycle
SESSION_START = "session.start"
SESSION_END = "session.end"

# Turn lifecycle
TURN_START = "turn.start"
TURN_END = "turn.end"

# Streaming
STREAM_DELTA = "stream.delta"
MESSAGE_ASSISTANT = "message.assistant"

# Tool lifecycle
TOOL_BEFORE = "tool.before"       # fired before execution; listeners can cancel/mutate
TOOL_AFTER = "tool.after"         # fired after execution with result + duration_ms
TOOL_APPROVAL = "tool.approval_requested"  # fired to request user approval

# Plan mode
PLAN_PROPOSED = "plan.proposed"
PLAN_APPROVED = "plan.approved"
PLAN_CANCELLED = "plan.cancelled"

# Context
CONTEXT_COMPACT = "context.compact"

# Subagents
SUBAGENT_START = "subagent.start"
SUBAGENT_END = "subagent.end"
PARALLEL_BATCH_START = "parallel.batch_start"
PARALLEL_BATCH_END = "parallel.batch_end"

# MCP
MCP_SERVER_START = "mcp.server_start"
MCP_READY = "mcp.ready"       # fired once after all servers have started
MCP_TOOL_CALL = "mcp.tool_call"

# Errors
ERROR = "error"

# Wildcard — handlers registered on this receive every event
WILDCARD = "*"
