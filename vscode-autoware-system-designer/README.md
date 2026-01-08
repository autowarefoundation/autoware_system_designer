# Autoware System Designer VSCode Extension

A VSCode extension that provides language server support for Autoware System Design Format YAML files, offering validation, auto-completion, and interactive features.

## Features

### Connection Validation
- **Real-time validation** of connection references across files
- **Message type compatibility** checking between ports
- **Cross-file validation** ensures connections are valid

### Auto-completion
- **Entity references** - Complete entity names when referencing nodes, modules, etc.
- **Connection references** - Auto-complete port references (e.g., `instance.input.port_name`)
- **Message types** - Common ROS 2 message types
- **Parameter names** - Common parameter naming patterns

### Go-to-Definition
- Jump to entity definitions from references
- Navigate to port definitions in connected entities

### Hover Documentation
- **Entity information** - Type, file location, and summary
- **Port details** - Message types, QoS settings
- **Connection context** - Instance/component relationships

### Diagnostics
- **Error highlighting** for invalid connections
- **Warning messages** for type mismatches
- **Validation feedback** in real-time

## Supported File Types

- `*.node.yaml` - Node entity definitions
- `*.module.yaml` - Module entity definitions
- `*.system.yaml` - System entity definitions
- `*.parameter_set.yaml` - Parameter set definitions

## Installation

### Prerequisites

- Python 3.7+ with `pygls` and `lsprotocol` packages
- The Autoware System Designer package must be available in the Python path

### Installing Dependencies

```bash
# Install Python dependencies for the language server
pip install -r server/requirements.txt
```

### Building the Extension

```bash
# Install Node.js dependencies
npm install

# Compile TypeScript
npm run compile
```

### Running the Extension

1. Open the extension directory in VSCode
2. Press F5 to launch the Extension Development Host
3. Open a workspace containing Autoware System Design YAML files

## Configuration

### Language Server Path

Set the Python executable path used for the language server:

```json
{
  "autowareSystemDesigner.languageServer.path": "/usr/bin/python3"
}
```

### Debug Logging

Enable debug logging for troubleshooting:

```json
{
  "autowareSystemDesigner.languageServer.debug": true
}
```

## Architecture

### Language Server (Python)

The language server (`server/server.py`) implements the Language Server Protocol using `pygls`:

- **Entity Registry** - Maintains a registry of all parsed entities
- **Connection Validation** - Validates connection references and types
- **Completion Provider** - Provides context-aware auto-completion
- **Definition Provider** - Implements go-to-definition functionality
- **Hover Provider** - Shows detailed documentation on hover

### VSCode Extension (TypeScript)

The VSCode client (`src/extension.ts`) registers the language server and handles:

- **Language registration** for YAML files with specific extensions
- **Server lifecycle** management
- **Configuration** handling

## Development

### Project Structure

```
vscode-autoware-system-designer/
├── src/                    # TypeScript source files
│   └── extension.ts       # Main extension entry point
├── server/                # Python language server
│   ├── server.py         # Language server implementation
│   └── requirements.txt  # Python dependencies
├── package.json          # Extension manifest
├── tsconfig.json         # TypeScript configuration
└── language-configuration.json  # YAML language configuration
```

### Testing

```bash
# Run tests
npm test

# Lint code
npm run lint
```

### Debugging

1. Set breakpoints in the language server code
2. Use VSCode's debugger with the Extension Development Host
3. Check the language server output channel for logs

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly with example files
5. Submit a pull request

## License

Licensed under the Apache License, Version 2.0.

