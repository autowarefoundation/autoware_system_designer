import logging
from typing import TYPE_CHECKING

from ..exceptions import ValidationError
from ..models.parameters import ParameterType
from ..parsers.data_parser import entity_name_decode
from ..file_io.source_location import source_from_config, format_source

if TYPE_CHECKING:
    from .config_registry import ConfigRegistry
    from .instances import Instance

logger = logging.getLogger(__name__)


def apply_parameter_set(
    owner_instance: "Instance",
    target_instance: "Instance",
    cfg_component: dict,
    config_registry: "ConfigRegistry",
    check_namespace: bool = True,
    file_parameter_type: ParameterType = ParameterType.OVERRIDE_FILE,
    direct_parameter_type: ParameterType = ParameterType.OVERRIDE,
) -> None:
    """Apply parameter set(s) to an instance using direct node targeting.

    Supports both single parameter_set (str) and multiple parameter_sets (list of str).
    When multiple parameter_sets are provided, they are applied sequentially, allowing
    later sets to overwrite earlier ones.

    Only applies parameters to nodes that are descendants of the given instance.
    """
    parameter_set = cfg_component.get("parameter_set")
    if parameter_set is None:
        return

    # Normalize to list for uniform processing
    parameter_set_list = parameter_set if isinstance(parameter_set, list) else [parameter_set]

    # Apply each parameter set sequentially
    for param_set_id in parameter_set_list:
        try:
            param_set_name, entity_type = entity_name_decode(param_set_id)
            if entity_type != "parameter_set":
                raise ValidationError(
                    f"Invalid parameter set type: {entity_type}, at {owner_instance.configuration.file_path}"
                )

            cfg_param_set = config_registry.get_parameter_set(param_set_name)
            node_params = cfg_param_set.parameters
            logger.info(
                f"Applying parameter set '{param_set_name}' to component '{target_instance.name}'"
            )

            # Determine which resolver to use
            resolver_to_use = owner_instance.parameter_resolver

            # If local_variables exist and we have a resolver, create a scoped resolver
            if cfg_param_set.local_variables and resolver_to_use:
                resolver_to_use = resolver_to_use.copy()
                # Resolve local variables (updating the scoped resolver's map) with source context
                resolved_local_vars = []
                for lv_idx, lv in enumerate(cfg_param_set.local_variables):
                    if not isinstance(lv, dict):
                        continue
                    resolved_lv = lv.copy()
                    lv_source = source_from_config(cfg_param_set, f"/local_variables/{lv_idx}")
                    if 'value' in resolved_lv:
                        resolved_lv['value'] = resolver_to_use.resolve_parameter_value(
                            resolved_lv['value'], source=lv_source
                        )
                        if 'name' in resolved_lv:
                            resolver_to_use.variable_map[resolved_lv['name']] = str(resolved_lv['value'])
                    resolved_local_vars.append(resolved_lv)
                # Keep for any downstream logic expecting resolved list
                cfg_param_set.local_variables = resolved_local_vars
                logger.debug(
                    f"Created scoped resolver for '{param_set_name}' with {len(cfg_param_set.local_variables)} local variables"
                )

            for node_idx, param_config in enumerate(node_params):
                if isinstance(param_config, dict) and "node" in param_config:
                    node_namespace = param_config.get("node")
                    node_source = source_from_config(cfg_param_set, f"/parameters/{node_idx}/node")

                    # Only apply if the target node is under this component's namespace
                    if (
                        check_namespace
                        and node_namespace != target_instance.namespace_str
                        and not node_namespace.startswith(target_instance.namespace_str + "/")
                    ):
                        logger.debug(
                            f"Parameter set '{param_set_name}' skip node '{node_namespace}' (component namespace '{target_instance.namespace_str}')"
                        )
                        continue

                    parameter_files_raw = param_config.get("parameter_files", [])
                    parameters = param_config.get("parameters", [])

                    # Resolve + validate parameter_files with per-entry source context
                    parameter_files = []
                    parameter_file_sources = []
                    if parameter_files_raw:
                        for pf_idx, pf in enumerate(parameter_files_raw):
                            if not isinstance(pf, dict):
                                logger.warning(
                                    f"Invalid parameter_files format in parameter set '{param_set_name}': {pf}{format_source(node_source)}"
                                )
                                continue
                            pf_source = source_from_config(cfg_param_set, f"/parameters/{node_idx}/parameter_files/{pf_idx}")
                            resolved_mapping = {}
                            for param_name, file_path in pf.items():
                                if resolver_to_use:
                                    resolved_mapping[param_name] = resolver_to_use.resolve_parameter_file_path(
                                        file_path, source=pf_source
                                    )
                                else:
                                    resolved_mapping[param_name] = file_path
                            parameter_files.append(resolved_mapping)
                            parameter_file_sources.append(pf_source)

                    # Resolve parameters with per-entry source context
                    resolved_parameters = []
                    parameter_sources = []
                    if parameters:
                        for p_idx, p in enumerate(parameters):
                            if not isinstance(p, dict):
                                continue
                            p_source = source_from_config(cfg_param_set, f"/parameters/{node_idx}/parameters/{p_idx}")
                            resolved_p = p.copy()
                            if resolver_to_use and 'value' in resolved_p:
                                resolved_p['value'] = resolver_to_use.resolve_parameter_value(
                                    resolved_p['value'], source=p_source
                                )
                            if resolver_to_use and 'name' in resolved_p and 'value' in resolved_p:
                                resolver_to_use.variable_map[resolved_p['name']] = str(resolved_p['value'])
                            resolved_parameters.append(resolved_p)
                            parameter_sources.append(p_source)

                    # Apply parameters directly to the target node
                    target_instance.parameter_manager.apply_node_parameters(
                        node_namespace,
                        parameter_files,
                        resolved_parameters,
                        config_registry,
                        file_parameter_type=file_parameter_type,
                        direct_parameter_type=direct_parameter_type,
                        source=node_source,
                        parameter_file_sources=parameter_file_sources,
                        parameter_sources=parameter_sources,
                    )
                    logger.debug(
                        f"Applied parameters to node '{node_namespace}' from set '{param_set_name}' files={len(parameter_files)} configs={len(parameters)}"
                    )
        except Exception as e:
            raise ValidationError(
                f"Error in applying parameter set '{param_set_name}' to instance '{target_instance.name}': {e}"
            )
