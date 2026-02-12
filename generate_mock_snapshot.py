import json
import os

def generate():
    data = [
        {
            "name": "/sensing/lidar/driver",
            "namespace": "/sensing/lidar",
            "component_type": "velodyne_driver::VelodyneDriver",
            "publishers": ["/sensing/lidar/points_raw"],
            "subscribers": ["/sensing/lidar/packets"]
        },
        {
            "name": "/perception/filter/pointcloud_filter",
            "namespace": "/perception/filter",
            "component_type": "filter::PointCloudFilter",
            "publishers": ["/perception/filter/filtered_points"],
            "subscribers": ["/sensing/lidar/points_raw"]
        }
    ]
    
    with open("graph.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Generated graph.json")

if __name__ == "__main__":
    generate()
