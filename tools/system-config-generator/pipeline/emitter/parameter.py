"""Parameter set YAML emitter."""

from __future__ import annotations

from ..namespace_tree import NamespaceNode
from .module import DESIGN_FORMAT


def emit_parameter_set_yaml(system_name: str, component_name: str, ns_node: NamespaceNode) -> str:
    """Generate a *.parameter_set.yaml for one top-level component."""
    ps_name = f"{system_name}_{component_name}.parameter_set"
    lines: list[str] = []
    lines.append(f"autoware_system_design_format: {DESIGN_FORMAT}")
    lines.append("")
    lines.append(f"name: {ps_name}")
    lines.append("")
    lines.append("parameters:")

    all_nodes = ns_node.all_nodes
    has_params = False
    for node in all_nodes:
        if not node.param_files:
            continue
        has_params = True
        lines.append(f"  - node: {node.full_path}")
        lines.append("    param_files:")
        for pf in node.param_files:
            lines.append(f"      - param_file: {pf}")
        lines.append("    param_values: []")

    if not has_params:
        lines.append("  []")

    return "\n".join(lines) + "\n"
