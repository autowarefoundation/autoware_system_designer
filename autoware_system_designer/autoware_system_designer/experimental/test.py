import ament_index_python


def test():
    packages = ament_index_python.get_packages_with_prefixes()
    for package, prefix in packages.items():
        print(package, f"{prefix}/share/{package}")


if __name__ == "__main__":
    test()
