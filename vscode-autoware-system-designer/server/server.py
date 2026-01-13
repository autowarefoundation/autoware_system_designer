#!/usr/bin/env python3

"""Entry point for the Autoware System Designer Language Server."""

import sys
import os
from pathlib import Path

# Add the server directory to the path so imports work when run directly
server_dir = Path(__file__).parent
if str(server_dir) not in sys.path:
    sys.path.insert(0, str(server_dir))

from base_server import AutowareSystemDesignerLanguageServer


if __name__ == '__main__':
    server = AutowareSystemDesignerLanguageServer()
    server.start()
