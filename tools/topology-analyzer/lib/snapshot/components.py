#!/usr/bin/env python3
# Component container detection for ros2_graph_snapshot.py.
# Queries every container's /_container/list_nodes service to build the
# composable-node → container mapping.

from typing import Dict, List, Optional, Tuple


def detect_components(
    node,
    graph: List,
) -> Tuple[Dict[str, Dict], Optional[str]]:
    """Query every container's /_container/list_nodes service.

    Returns (component_info_map, error_str_or_None).
    component_info_map: {composable_fq: {"container": str, "component_id": int}}
    """
    import rclpy

    component_info_map: Dict[str, Dict] = {}
    try:
        from composition_interfaces.srv import ListNodes as _ListNodesSrv  # type: ignore

        for n in graph:
            list_svc = f"{n.fq_name}/_container/list_nodes"
            if list_svc not in n.services:
                continue
            cli = node.create_client(_ListNodesSrv, list_svc)
            try:
                if cli.wait_for_service(timeout_sec=0.5):
                    future = cli.call_async(_ListNodesSrv.Request())
                    rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
                    if future.result() is not None:
                        resp = future.result()
                        for comp_name, comp_id in zip(resp.full_node_names, resp.unique_ids):
                            component_info_map[comp_name] = {
                                "container": n.fq_name,
                                "component_id": int(comp_id),
                            }
            finally:
                node.destroy_client(cli)
    except Exception as exc:  # noqa: BLE001
        return component_info_map, f"{type(exc).__name__}: {exc}"

    return component_info_map, None
