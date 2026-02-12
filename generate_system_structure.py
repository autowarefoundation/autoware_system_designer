#!/usr/bin/env python3
import argparse
import json
import os
import yaml
import sys
from pathlib import Path
from abc import ABC, abstractmethod

# --- 1. Graph Abstraction ---

class GraphProvider(ABC):
    @abstractmethod
    def get_nodes(self):
        """Returns a list of node dicts: {'name': str, 'publishers': [], 'subscribers': []}"""
        pass

class MockGraphProvider(GraphProvider):
    def __init__(self, file_path):
        self.file_path = file_path

    def get_nodes(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Mock file not found: {self.file_path}")
        with open(self.file_path, 'r') as f:
            return json.load(f)

class RosGraphProvider(GraphProvider):
    def get_nodes(self):
        import subprocess
        import re

        # Wrapper to run commands with ROS environment
        def run_ros_cmd(cmd):
            full_cmd = f"source /opt/ros/humble/setup.bash && [ -f install/setup.bash ] && source install/setup.bash; {cmd}"
            result = subprocess.run(["bash", "-c", full_cmd], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Warning: Command failed: {cmd}\n{result.stderr}")
                return ""
            return result.stdout

        print("Listing ROS 2 nodes...")
        node_list_out = run_ros_cmd("ros2 node list")
        node_names = [n.strip() for n in node_list_out.splitlines() if n.strip()]

        nodes = []
        for node_name in node_names:
            print(f"Inspecting node: {node_name}")
            info_out = run_ros_cmd(f"ros2 node info {node_name}")
            
            # Parse Publishers
            pubs = []
            pub_match = re.search(r"Publishers:(.*?)(?:Subscribers:|Service Servers:|$)", info_out, re.DOTALL)
            if pub_match:
                pubs = [line.split(":")[0].strip() for line in pub_match.group(1).splitlines() if ":" in line]

            # Parse Subscribers
            subs = []
            sub_match = re.search(r"Subscribers:(.*?)(?:Service Servers:|Service Clients:|$)", info_out, re.DOTALL)
            if sub_match:
                subs = [line.split(":")[0].strip() for line in sub_match.group(1).splitlines() if ":" in line]

            nodes.append({
                "name": node_name,
                "publishers": pubs,
                "subscribers": subs
            })

        return nodes

# --- 2. System Tree Logic ---

class SystemNode:
    def __init__(self, name, kind="module", parent=None):
        self.name = name  # Directory/Component Name
        self.kind = kind  # "module", "node", "root"
        self.parent = parent
        self.children = {} # name -> SystemNode
        
        # Leaf Data (only for nodes)
        self.node_data = None
        
        # Interface Data (Aggregated)
        self.pubs = set() # Topics published by this tree
        self.subs = set() # Topics subscribed by this tree
        
        # Calculated Interface
        self.inputs = [] 
        self.outputs = []

    def add_node(self, path_parts, data):
        """Recursively build tree from path parts e.g. ['sensing', 'lidar', 'driver']"""
        if not path_parts:
            return

        child_name = path_parts[0]
        is_leaf = (len(path_parts) == 1)
        
        if child_name not in self.children:
            kind = "node" if is_leaf else "module"
            self.children[child_name] = SystemNode(child_name, kind, parent=self)
            
        child = self.children[child_name]
        
        if is_leaf:
            child.node_data = data
            child.pubs = set(data.get("publishers", []))
            child.subs = set(data.get("subscribers", []))
        else:
            child.add_node(path_parts[1:], data)

    def aggregate_interfaces(self):
        """Recursively aggregate pubs/subs from leaf nodes up to root"""
        if self.kind == "node":
            return

        for child in self.children.values():
            child.aggregate_interfaces()
            self.pubs.update(child.pubs)
            self.subs.update(child.subs)

    def calculate_boundaries(self):
        """Determine what is external input vs internal communication"""
        if self.kind == "node":
            return

        # Input: Subscribed by children BUT NOT published by children
        # (If a child publishes X and another subscribes X, it's internal to this module)
        external_subs = self.subs - self.pubs
        
        # Output: Published by children
        # (We assume all pubs are potentially outputs for now)
        external_pubs = self.pubs
        
        # Format for YAML
        for t in sorted(list(external_subs)):
            self.inputs.append({
                "name": t.split('/')[-1] if '/' in t else t,
                "topic": t
            })
            
        for t in sorted(list(external_pubs)):
            self.outputs.append({
                "name": t.split('/')[-1] if '/' in t else t,
                "topic": t
            })

        # Recurse
        for child in self.children.values():
            child.calculate_boundaries()

    def to_yaml(self):
        data = {}
        if self.kind == "root":
            data = {
                "name": "running_system",
                "type": "system",
                "components": []
            }
            for child in self.children.values():
                path = f"{child.name}/{child.name}.module.yaml" if child.kind == "module" else f"{child.name}.node.yaml"
                data["components"].append(path)
                
        elif self.kind == "module":
            data = {
                "name": self.name,
                "type": "module",
                "inputs": self.inputs,
                "outputs": self.outputs,
                "components": []
            }
            for child in self.children.values():
                path = f"{child.name}/{child.name}.module.yaml" if child.kind == "module" else f"{child.name}.node.yaml"
                data["components"].append(path)
                
        elif self.kind == "node":
            data = {
                "name": f"{self.name}.node",
                "type": "node",
                "launch": {
                    "package": "unknown", # Placeholder
                    "plugin": "unknown"
                },
                "inputs": [{"name": t.split('/')[-1], "topic": t} for t in sorted(list(self.subs))],
                "outputs": [{"name": t.split('/')[-1], "topic": t} for t in sorted(list(self.pubs))]
            }
            
        return data

# --- 3. Main Generator Logic ---

def generate_structure(provider: GraphProvider, output_dir: str):
    nodes = provider.get_nodes()
    root = SystemNode("root", "root")
    
    # 1. Build Tree
    for node_data in nodes:
        name = node_data['name']
        if name.startswith('/'):
            name = name[1:]
        path_parts = name.split('/')
        root.add_node(path_parts, node_data)
        
    # 2. Process Interfaces
    root.aggregate_interfaces()
    root.calculate_boundaries()
    
    # 3. Write Artifacts
    output_path = Path(output_dir)
    
    def write_recursive(node, current_path):
        os.makedirs(current_path, exist_ok=True)
        
        filename = ""
        if node.kind == "root":
            filename = "running_system.system.yaml"
        elif node.kind == "module":
            filename = f"{node.name}.module.yaml"
        elif node.kind == "node":
            filename = f"{node.name}.node.yaml"
            
        if filename:
            with open(current_path / filename, 'w') as f:
                yaml.dump(node.to_yaml(), f, sort_keys=False)
        
        # Recurse
        for child in node.children.values():
            child_dir = current_path
            if child.kind == "module":
                child_dir = current_path / child.name
            write_recursive(child, child_dir)

    write_recursive(root, output_path)
    print(f"Generated design files in {output_dir}")

def main():
    parser = argparse.ArgumentParser(description="Reverse Engineer ROS 2 System to Autoware Design")
    parser.add_argument("--mock-file", help="Path to mock graph.json")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    
    args = parser.parse_args()
    
    provider = None
    if args.mock_file:
        provider = MockGraphProvider(args.mock_file)
    else:
        provider = RosGraphProvider() # TODO: Implement live
        
    generate_structure(provider, args.output_dir)

if __name__ == "__main__":
    main()