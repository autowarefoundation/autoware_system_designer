#!/usr/bin/env python3
# Parameter collection helpers for ros2_graph_snapshot.py.

import json
from typing import Dict, List, Optional, Tuple


def param_value_to_string(val: object) -> str:
    """Convert an rclpy ParameterValue to a human-readable string."""
    try:
        from rcl_interfaces.msg import ParameterType
    except Exception:  # noqa: BLE001
        return str(val)
    if hasattr(val, "type"):
        t = val.type
        if t == ParameterType.PARAMETER_NOT_SET:
            return "<not set>"
        if t == ParameterType.PARAMETER_BOOL:
            return str(getattr(val, "bool_value", False)).lower()
        if t == ParameterType.PARAMETER_INTEGER:
            return str(getattr(val, "integer_value", 0))
        if t == ParameterType.PARAMETER_DOUBLE:
            return repr(getattr(val, "double_value", 0.0))
        if t == ParameterType.PARAMETER_STRING:
            return str(getattr(val, "string_value", ""))
        if t == ParameterType.PARAMETER_BYTE_ARRAY:
            return json.dumps(list(getattr(val, "byte_array_value", [])))
        if t == ParameterType.PARAMETER_BOOL_ARRAY:
            return json.dumps(list(getattr(val, "bool_array_value", [])))
        if t == ParameterType.PARAMETER_INTEGER_ARRAY:
            return json.dumps(list(getattr(val, "integer_array_value", [])))
        if t == ParameterType.PARAMETER_DOUBLE_ARRAY:
            return json.dumps(list(getattr(val, "double_array_value", [])))
        if t == ParameterType.PARAMETER_STRING_ARRAY:
            return json.dumps(list(getattr(val, "string_array_value", [])))
    if hasattr(val, "value"):
        return str(getattr(val, "value"))
    return str(val)


def _list_parameters_fallback(node, target_fq: str) -> Tuple[Optional[List[str]], Optional[str]]:
    import rclpy

    try:
        from rcl_interfaces.srv import ListParameters
    except Exception as exc:  # noqa: BLE001
        return None, f"<error: {type(exc).__name__}: {exc}>"
    client = node.create_client(ListParameters, f"{target_fq}/list_parameters")
    if not client.wait_for_service(timeout_sec=0.5):
        return None, "<no parameter service>"
    req = ListParameters.Request()
    req.prefixes = []
    req.depth = 0
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
    if future.result() is None:
        return None, "<timeout listing parameters>"
    names_list = list(future.result().result.names)
    names_list.sort()
    return names_list, None


def _get_parameters_fallback(node, target_fq: str, names: List[str]) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    import rclpy

    try:
        from rcl_interfaces.srv import GetParameters
    except Exception as exc:  # noqa: BLE001
        return None, f"<error: {type(exc).__name__}: {exc}>"
    client = node.create_client(GetParameters, f"{target_fq}/get_parameters")
    if not client.wait_for_service(timeout_sec=0.5):
        return None, "<no parameter service>"
    req = GetParameters.Request()
    req.names = names
    future = client.call_async(req)
    rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
    if future.result() is None:
        return None, "<timeout getting parameters>"
    values: Dict[str, str] = {}
    for name, val in zip(names, future.result().values):
        values[name] = param_value_to_string(val)
    return values, None


def collect_node_params(
    node,
    fq: str,
    mode: str,
    async_client_cls,
) -> Tuple[List[str], Dict[str, str]]:
    """Collect parameter names (and optionally values) for node *fq*.

    mode: "names" or "values"
    async_client_cls: AsyncParametersClient class or None for service-call fallback.

    Returns (names_list, values_dict).
    Sentinel strings inside the returned containers encode error conditions
    (e.g. ["<no parameter service>"] / {"<no parameter service>": ""}).
    """
    import rclpy

    def _err(msg: str) -> Tuple[List[str], Dict[str, str]]:
        return [msg], ({msg: ""} if mode == "values" else {})

    try:
        if async_client_cls is not None:
            client = async_client_cls(node, fq)
            if not client.wait_for_service(timeout_sec=0.5):
                return _err("<no parameter service>")

            future = client.list_parameters(prefixes=[], depth=0)
            rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
            if future.result() is None:
                return _err("<timeout listing parameters>")

            names_list = list(future.result().result.names)
            names_list.sort()

            if mode != "values" or not names_list:
                return names_list, {}

            future_vals = client.get_parameters(names_list)
            rclpy.spin_until_future_complete(node, future_vals, timeout_sec=1.0)
            if future_vals.result() is None:
                return names_list, {"<timeout getting parameters>": ""}

            values_dict: Dict[str, str] = {}
            for pname, val in zip(names_list, future_vals.result().values):
                values_dict[pname] = param_value_to_string(val)
            return names_list, values_dict

        else:
            names_list, status = _list_parameters_fallback(node, fq)
            if status:
                return _err(status)

            names_list = names_list or []
            if mode != "values" or not names_list:
                return names_list, {}

            values, v_status = _get_parameters_fallback(node, fq, names_list)
            if v_status:
                return names_list, {v_status: ""}
            return names_list, values or {}

    except Exception as exc:  # noqa: BLE001
        return _err(f"<error: {type(exc).__name__}: {exc}>")
