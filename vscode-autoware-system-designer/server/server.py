#!/usr/bin/env python3

"""Entry point for the Autoware System Designer Language Server."""

from .base_server import AutowareSystemDesignerLanguageServer


if __name__ == '__main__':
    server = AutowareSystemDesignerLanguageServer()
    server.start()
