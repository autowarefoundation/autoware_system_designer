#!/usr/bin/env python3
# Re-export from lib/common.py (the canonical implementation).
# This shim keeps ros2_topology_diff.py and ros2_topology_similarity.py working unchanged.

import os
import sys

# Allow importing lib.common regardless of the working directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lib.common import (  # noqa: E402, F401
    COMMON_TOPICS,
    PARAM_SVC_SUFFIXES,
    TOOL_NODE_RE,
    Signature,
    basename,
    freeze_map,
    iter_signature_items,
    iter_type_items,
    jaccard,
    load_graph,
    name_similarity,
    signature_from_node,
    signature_id,
    topic_index,
)
