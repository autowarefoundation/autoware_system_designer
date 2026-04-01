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

| Tool                                                     | Version | Purpose                           |
| -------------------------------------------------------- | ------- | --------------------------------- |
| [Node.js](https://nodejs.org/)                           | 18+     | Build toolchain                   |
| [pnpm](https://pnpm.io/)                                 | 8+      | Package manager                   |
| [TypeScript](https://www.typescriptlang.org/)            | 4.9+    | Compile extension source          |
| [@vscode/vsce](https://github.com/microsoft/vscode-vsce) | latest  | Package `.vsix` (production only) |
| Python                                                   | 3.8+    | Language server runtime           |
| pip packages: `pygls>=1.0.0`, `lsprotocol>=2022.0.0`     | —       | Language server libraries         |

### 1. Install Node.js and pnpm

```bash
# Node.js (via nvm — recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
nvm install --lts
nvm use --lts

# pnpm
npm install -g pnpm
```

### 2. Install TypeScript and vsce globally

```bash
pnpm add -g typescript @vscode/vsce
```

### 3. Install Node.js dependencies

```bash
cd path-to/vscode-autoware-system-designer/
pnpm install
```

### 4. Install Python language server dependencies

```bash
pip install -r server/requirements.txt
```

### Build and Install (production)

```bash
vsce package --no-dependencies
code --install-extension vscode-autoware-system-designer-*.vsix
```

### Development (no packaging needed)

1. Open this directory in VSCode:

   ```bash
   code path-to/vscode-autoware-system-designer/
   ```

2. Press **F5** — VSCode compiles the TypeScript and opens an Extension Development Host with the extension loaded live.
3. Edit `src/extension.ts` and the TypeScript compiler (`tsc --watch`) recompiles automatically; reload the host window (`Ctrl+Shift+P` → "Reload Window") to pick up changes.
4. Logs appear in the host window under **Output → "Autoware System Designer Language Server"**.

Enable verbose Python server logging via workspace settings in the host window:

```json
{
  "autowareSystemDesigner.languageServer.debug": true
}
```

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

```text
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
pnpm test

# Lint code
pnpm lint
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
