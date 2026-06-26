from __future__ import annotations

CONNECTOR_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["id", "display_name", "command", "workdir", "permission_profile", "input_format", "done_signal"],
    "properties": {
        "id": {
            "type": "string",
        },
        "display_name": {
            "type": "string",
        },
        "description": {
            "type": "string",
        },
        "command": {
            "type": "string",
        },
        "args": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "env": {
            "type": "object",
            "additionalProperties": {
                "type": "string",
            },
        },
        "workdir": {
            "type": "string",
        },
        "permission_profile": {
            "type": "string",
            "enum": ["confirm", "yolo"],
        },
        "input_format": {
            "type": "object",
            "required": ["kind"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["stdin_prompt", "file_prompt"],
                },
                "template": {
                    "type": "string",
                },
                "close_stdin": {
                    "type": "boolean",
                    "description": (
                        "Send EOF after piping the prompt. Defaults to true for "
                        "stdin_prompt connectors whose done_signal is exit_code "
                        "(batch tools that read the prompt then exit); set false "
                        "for interactive agents that keep reading stdin."
                    ),
                },
            },
        },
        "done_signal": {
            "type": "object",
            "required": ["kind"],
            "oneOf": [
                {
                    "properties": {
                        "kind": {"const": "regex"},
                        "pattern": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
                {
                    "properties": {
                        "kind": {"const": "exit_code"},
                        "exit_code": {"type": "integer"},
                    },
                    "required": ["exit_code"],
                },
                {
                    "properties": {
                        "kind": {"const": "tool_call"},
                        "tool_name": {"type": "string"},
                    },
                    "required": ["tool_name"],
                },
            ],
            "unevaluatedProperties": False,
        },
        "exit_on_done": {
            "type": "boolean",
            "default": True,
        },
        "prompt_as_arg": {
            "description": "Append the rendered prompt as a trailing positional CLI argument instead of writing it to PTY stdin after spawn. Required for `claude --print` (which refuses to read stdin from a TTY) and useful for interactive `claude` runs that want a clean first-turn prompt without bracketed-paste shenanigans.",
            "type": "boolean",
            "default": False,
        },
        "verify_commands": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "argus_profile": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                },
                "args": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
                "workdir_readonly": {
                    "type": "boolean",
                    "default": False,
                },
            },
        },
        "resource_limits": {
            "type": "object",
            "properties": {
                "cpu_shares": {
                    "type": "integer",
                    "default": 1024,
                },
                "memory_mb": {
                    "type": "integer",
                    "default": 4096,
                },
                "pids_max": {
                    "type": "integer",
                    "default": 1024,
                },
                "wall_clock_minutes": {
                    "type": "integer",
                    "default": 60,
                },
                "idle_output_minutes": {
                    "type": "integer",
                    "default": 10,
                },
            },
        },
        "interrupt": {
            "type": "object",
            "properties": {
                "soft": {
                    "type": "string",
                    "default": "SIGINT",
                },
                "hard": {
                    "type": "string",
                    "default": "SIGTERM",
                },
                "kill_grace_seconds": {
                    "type": "integer",
                    "default": 5,
                },
            },
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "egress_allowlist": {
            "description": "Hostnames the agent run is allowed to reach. Enforcement happens at the host iptables layer (see deploy/agentnet.md); this list is forwarded to the runner so it can log + reject violations once enforcement lands.",
            "type": "array",
            "items": {"type": "string"},
        },
        "usage_parser": {
            "description": "How Talos should extract token counts and cost from this connector's transcript. See daedalus.connectors.usage.",
            "type": "object",
            "required": ["kind"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["claude", "openai", "regex", "json_block"],
                },
                "input_pattern": {"type": "string"},
                "output_pattern": {"type": "string"},
                "cost_pattern": {"type": "string"},
                "cost_per_input_micros": {
                    "type": "integer",
                    "description": "USD micros per million input tokens (e.g. 3_000_000 = $3.00 / 1M).",
                },
                "cost_per_output_micros": {
                    "type": "integer",
                    "description": "USD micros per million output tokens.",
                },
            },
        },
        "schema_version": {
            "type": "integer",
            "default": 1,
        },
    },
}
