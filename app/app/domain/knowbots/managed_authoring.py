"""Keep technical-only MCP/Skill bindings out of new authoring and delivery.

Remove this gate only when the shared permission contract is enforced. Reading
or installing capabilities in KnowBench is independent of authoring a method.
"""

from typing import Any

MESSAGE = (
    "方法中的 MCP / Skill 接入暂未开放，请先移除这些绑定；"
    "聊天和工作仍可使用已安装的能力。"
)
CODE = "method_access_not_ready"


def _binding_paths(container: Any, path: str) -> list[str]:
    if not isinstance(container, dict):
        return []
    if container.get("access_requirements"):
        return [f"{path}.access_requirements"]
    requirements = container.get("managed_capabilities")
    if isinstance(requirements, dict) and (
        requirements.get("tools") or requirements.get("skills")
    ):
        return [f"{path}.managed_capabilities"]
    return []


def workdraft_paths(body: dict[str, Any]) -> list[str]:
    return [
        path
        for index, step in enumerate(body.get("steps", []))
        for path in _binding_paths(step.get("advanced"), f"steps[{index}].advanced")
    ]


def pack_paths(pack: Any) -> list[str]:
    if not isinstance(pack, dict):
        return []
    paths = []
    for field in ("workflow", "method_units"):
        rows = pack.get(field)
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            path = f"pack.{field}[{index}]"
            paths.extend(_binding_paths(row, path))
            paths.extend(_binding_paths(row.get("contract"), path + ".contract"))
    sources = pack.get("method_sources")
    if isinstance(sources, dict):
        for alias, source in sources.items():
            definition = source.get("definition") if isinstance(source, dict) else None
            step = definition.get("step") if isinstance(definition, dict) else None
            if isinstance(step, dict):
                paths.extend(
                    _binding_paths(
                        step.get("advanced"),
                        f"pack.method_sources.{alias}.definition.step.advanced",
                    )
                )
    return paths
